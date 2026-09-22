"""Pull usa-wa's published dataset snapshots into the local store (#496).

Step 1 of the #490 PM-side pipeline. Fetches the catalog, lands every subscribed
dataset whose latest version is not already held, verifies each against the
length and digest the catalog states, and prunes old versions.

**The subscription is the pins (#536).** Each dataset the mapping models read is
pinned in `meta` on its source in `src/core/ingestion/mapping/models/sources.yml`
— a `schema_major` and a `contract_hash` — and those pins are the whole
subscription, so the nightly needs no flags. A version whose contract differs
from its pin is refused, not landed; re-pinning is a reviewed diff, not a flag.

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
    uv run "${env_args[@]}" python -m scripts.pull_datasets --dataset persons
    uv run "${env_args[@]}" python -m scripts.pull_datasets --root /srv/snapshots --keep 5

**The catalog carries a heartbeat (#551).** `checked_at` says the publisher
completed a run, `stale_after` the deadline for the next one. Both are recorded
in `pull.json` at the store root, which the build reads; a pull past the
deadline fails the run, because every dataset then reads `unchanged` and a
producer behind the clock is indistinguishable from a settled night otherwise.

Exit codes: 0 all subscribed datasets are held — including any the publisher
serves with no `datapackage.json`, which the report names; 1 a dataset failed,
was incompatible, was subscribed but absent from the catalog, or the publisher
is past its own `stale_after`; 2 usage — no token, a `--dataset` that is not
pinned, or a pin file that will not load.
"""

import asyncio
import os
import sys
from collections.abc import Callable
from datetime import UTC, datetime

import httpx

from scripts._dsn import build_parser
from src.core.ingestion.datasets import (
    PINS_PATH,
    CatalogError,
    PullReport,
    SnapshotStore,
    Subscription,
    fetch_catalog,
    load_subscription,
    pull,
)
from src.core.logging import configure_logging, get_logger

logger = get_logger(__name__)

DEFAULT_BASE_URL = "https://usa-wa.exe.xyz:8000"
DEFAULT_ROOT = "data/usa_wa_snapshots"
DEFAULT_KEEP = 3
TOKEN_VAR = "USA_WA_TOKEN"


def build_subscription(datasets: list[str], *, pinned: Subscription) -> Subscription:
    """Named datasets **narrow** the pinned set, each keeping its own pin.

    A name with no pin is refused rather than pulled: landing it would mean
    landing a contract nobody pinned.
    """
    if not datasets:
        return pinned
    unpinned = sorted(set(datasets) - set(pinned.pins))
    if unpinned:
        # Not "pin it": a pin exists only on a source the models read, and the
        # mapping project's parity test refuses one for anything else.
        raise ValueError(
            f"not pinned: {', '.join(unpinned)} — only the datasets the mapping models "
            f"read are pinned ({PINS_PATH})"
        )
    return Subscription({name: pinned.pins[name] for name in datasets})


def _log_report(report: PullReport) -> None:
    # Every outcome `failed_run` counts is in the headline: it is the line that
    # gets grepped, and "0 failed" on a run that exits 1 contradicts the exit
    # code for three of the four ways a pull can fail.
    logger.info(
        "pull complete: %d landed, %d unchanged, %d failed, %d incompatible, %d missing%s",
        len(report.landed),
        len(report.skipped),
        len(report.failed),
        len(report.incompatible),
        len(report.missing),
        ", producer stale" if report.producer_stale else "",
    )
    for name in report.landed:
        logger.info("  landed    %s", name)
    for name in report.skipped:
        logger.info("  unchanged %s", name)
    for name in report.landed_without_package:
        logger.warning("  landed    %s — with no datapackage.json (see #497)", name)
    for name, reason in report.failed:
        logger.error("  FAILED    %s — %s", name, reason)
    for name, reason in report.incompatible:
        logger.error("  INCOMPATIBLE %s — %s", name, reason)
    for name in report.missing:
        logger.error("  MISSING   %s — subscribed, but the catalog does not carry it", name)
    if report.producer_stale:
        logger.error("  STALE     %s", report.producer_stale)


async def run(
    base_url: str,
    *,
    token: str,
    store: SnapshotStore,
    subscription: Subscription,
    keep: int,
    client: httpx.AsyncClient | None = None,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> PullReport:
    """Fetch the catalog, land what is subscribed, prune what is stale."""
    owns_client = client is None
    client = client or httpx.AsyncClient(timeout=60.0)
    try:
        catalog = await fetch_catalog(base_url, token=token, client=client)
        logger.info("catalog lists %d dataset(s) at %s", len(catalog.entries), base_url)
        report = await pull(
            base_url,
            catalog.entries,
            store,
            token=token,
            client=client,
            subscription=subscription,
        )
    finally:
        if owns_client:
            await client.aclose()

    # The heartbeat (#551), recorded whatever it says: `BUILD.json` reads it to
    # state whether the desired state was built on a producer behind its clock,
    # and the gate at the far end of the chain reads that.
    at = now()
    store.record_pull(catalog, at=at)
    if catalog.stale(at):
        report.producer_stale = catalog.lateness(at)

    # Prune after landing, sparing the version this run just fetched: a `--keep`
    # smaller than the number of versions must never delete the newest one. Note
    # this is the newest *published* version, not the one the applier last
    # applied — nothing records that yet (#499).
    for entry in catalog.entries:
        if not subscription.wants(entry):
            continue
        removed = store.prune(entry.name, keep=keep, keep_version=entry.latest_version)
        for version in removed:
            logger.info("  pruned    %s %s", entry.name, version)

    _log_report(report)
    return report


def main(argv: list[str] | None = None) -> int:
    """Entry point; returns the process exit code."""
    configure_logging()
    parser = build_parser(__doc__)
    parser.add_argument(
        "--base-url",
        default=os.environ.get("USA_WA_DATASETS_URL", DEFAULT_BASE_URL),
        help=f"Publisher root (env USA_WA_DATASETS_URL, default {DEFAULT_BASE_URL})",
    )
    parser.add_argument(
        "--root", default=DEFAULT_ROOT, help=f"Snapshot store (default {DEFAULT_ROOT})"
    )
    parser.add_argument(
        "--dataset",
        action="append",
        default=[],
        help="Pinned dataset to pull; repeatable. Omit for every pinned dataset.",
    )
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
        subscription = build_subscription(args.dataset, pinned=load_subscription())
    except ValueError as exc:
        # A malformed pin lands here too: it is configuration, like a bad flag.
        parser.error(str(exc))

    try:
        report = asyncio.run(
            run(
                args.base_url,
                token=token,
                store=SnapshotStore(args.root),
                subscription=subscription,
                keep=args.keep,
            )
        )
    except CatalogError as exc:
        # The catalog is the one failure that stops the whole run, and its message
        # is written to tell the operator whether to look at the token or at the
        # publisher. A traceback in the journal buries that sentence.
        logger.error("catalog unreadable: %s", exc)
        return 1
    except OSError as exc:
        # `record_pull` and `prune` are the two filesystem calls outside `pull`'s
        # own per-dataset guard, so a full disk turned a run that had landed and
        # verified every dataset into a traceback (CR 3).
        logger.error("snapshot store %s unwritable: %s", args.root, exc)
        return 1
    return 1 if report.failed_run else 0


if __name__ == "__main__":
    sys.exit(main())
