"""Pull usa-wa's published dataset snapshots into the local store (#496).

Step 1 of the #490 PM-side pipeline. Fetches the catalog, lands every subscribed
dataset whose latest version is not already held, verifies each against the
digest the catalog states, and prunes old versions.

**No `--execute` flag, deliberately.** The #402/#399 rule gates writes to the
production *database*, and this script never opens a database connection: it
writes only into its own snapshot store, and a re-run with nothing new upstream
writes nothing at all. The gated step is the applier (#499), which reads what
this leaves behind.

Auth is an exe.dev VM bearer token in `USA_WA_TOKEN`. The catalog is served from
a **private** proxy, so an absent or wrong token does not 401 — it returns a
login page. `src.core.ingestion.datasets` names that outcome as authentication;
this script refuses to start without a token at all, which is one fewer way to
meet it.

Usage:
    uv run "${env_args[@]}" python -m scripts.pull_datasets
    uv run "${env_args[@]}" python -m scripts.pull_datasets --dataset pm_anchors
    uv run "${env_args[@]}" python -m scripts.pull_datasets --root /srv/snapshots --keep 5

Exit codes: 0 all subscribed datasets are held; 1 a dataset failed, was
incompatible, or was subscribed but absent from the catalog; 2 usage.
"""

import argparse
import asyncio
import os
import sys

import httpx

from src.core.ingestion.datasets import (
    CatalogError,
    PullReport,
    SnapshotStore,
    Subscription,
    fetch_catalog,
    pull,
)
from src.core.logging import configure_logging, get_logger

logger = get_logger(__name__)

DEFAULT_BASE_URL = "https://usa-wa.exe.xyz:8000"
DEFAULT_ROOT = "data/usa_wa_snapshots"
DEFAULT_SCHEMA_MAJOR = 1
DEFAULT_KEEP = 3
TOKEN_VAR = "USA_WA_TOKEN"


def build_subscription(datasets: list[str], *, schema_major: int) -> Subscription:
    """Named datasets **replace** the conformed-tier default rather than extending it."""
    return Subscription(names=frozenset(datasets) if datasets else None, schema_major=schema_major)


def _log_report(report: PullReport, *, schema_major: int) -> None:
    # Every outcome `failed_run` counts is in the headline: it is the line that
    # gets grepped, and "0 failed" on a run that exits 1 contradicts the exit
    # code for two of the three ways a pull can fail.
    logger.info(
        "pull complete: %d landed, %d unchanged, %d failed, %d incompatible, %d missing",
        len(report.landed),
        len(report.skipped),
        len(report.failed),
        len(report.incompatible),
        len(report.missing),
    )
    for name in report.landed:
        logger.info("  landed    %s", name)
    for name in report.skipped:
        logger.info("  unchanged %s", name)
    for name, reason in report.failed:
        logger.error("  FAILED    %s — %s", name, reason)
    for name, schema_version in report.incompatible:
        logger.error(
            "  INCOMPATIBLE %s — publishes schema %s, this consumer is pinned to major %s",
            name,
            schema_version,
            schema_major,
        )
    for name in report.missing:
        logger.error("  MISSING   %s — subscribed, but the catalog does not carry it", name)


async def run(
    base_url: str,
    *,
    token: str,
    store: SnapshotStore,
    subscription: Subscription,
    keep: int,
    client: httpx.AsyncClient | None = None,
) -> PullReport:
    """Fetch the catalog, land what is subscribed, prune what is stale."""
    owns_client = client is None
    client = client or httpx.AsyncClient(timeout=60.0)
    try:
        catalog = await fetch_catalog(base_url, token=token, client=client)
        logger.info("catalog lists %d dataset(s) at %s", len(catalog), base_url)
        report = await pull(
            base_url,
            catalog,
            store,
            token=token,
            client=client,
            subscription=subscription,
        )
    finally:
        if owns_client:
            await client.aclose()

    # Prune after landing, sparing the version this run just fetched: a `--keep`
    # smaller than the number of versions must never delete the newest one. Note
    # this is the newest *published* version, not the one the applier last
    # applied — nothing records that yet (#499).
    for entry in catalog:
        if not subscription.wants(entry):
            continue
        removed = store.prune(entry.name, keep=keep, keep_version=entry.latest_version)
        for version in removed:
            logger.info("  pruned    %s %s", entry.name, version)

    _log_report(report, schema_major=subscription.schema_major)
    return report


def main(argv: list[str] | None = None) -> int:
    """Entry point; returns the process exit code."""
    configure_logging()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--base-url", default=os.environ.get("USA_WA_DATASETS_URL", DEFAULT_BASE_URL)
    )
    parser.add_argument(
        "--root", default=DEFAULT_ROOT, help=f"Snapshot store (default {DEFAULT_ROOT})"
    )
    parser.add_argument(
        "--dataset",
        action="append",
        default=[],
        help="Dataset to pull; repeatable. Omit for every conformed product.",
    )
    parser.add_argument("--schema-major", type=int, default=DEFAULT_SCHEMA_MAJOR)
    parser.add_argument(
        "--keep",
        type=int,
        default=DEFAULT_KEEP,
        help=f"Versions to retain per dataset (default {DEFAULT_KEEP})",
    )
    args = parser.parse_args(argv)

    token = os.environ.get(TOKEN_VAR)
    if not token:
        # Refusing here rather than sending an unauthenticated request: the proxy
        # answers that with a login page, and "authentication failed" is a worse
        # message than "you have not set the token".
        parser.error(
            f"{TOKEN_VAR} is not set — mint one with 'ssh exe.dev ssh-key generate-api-key'"
        )

    try:
        report = asyncio.run(
            run(
                args.base_url,
                token=token,
                store=SnapshotStore(args.root),
                subscription=build_subscription(args.dataset, schema_major=args.schema_major),
                keep=args.keep,
            )
        )
    except CatalogError as exc:
        # The catalog is the one failure that stops the whole run, and its message
        # is written to tell the operator whether to look at the token or at the
        # publisher. A traceback in the journal buries that sentence.
        logger.error("catalog unreadable: %s", exc)
        return 1
    return 1 if report.failed_run else 0


if __name__ == "__main__":
    sys.exit(main())
