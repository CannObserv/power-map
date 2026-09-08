"""Seed the producer crosswalk from a published anchor export (#495).

Transition safeguard 1 of the dataset-subscription design (#490): before the
applier may scope anything to "usa-wa's rows", PM has to agree with the producer
about which rows those are. This reads the export, resolves every PM id through
PM's own merge history, and writes the result to ``producer_crosswalk``.

**A blocking report stops the run.** An anchor that resolves nowhere, or two
producer entities PM has already merged into one, is a disagreement about
identity — the one thing the applier cannot settle by itself. Re-pointing it on
a guess is how a seed mints duplicates, so the script refuses and leaves the
diff for the triage pass (#501).

An archived anchor is stored rather than blocked, but it is **not** in the
applier's scope: the scope query is ``resolution IN ('live', 'merged')``. Where
PM's duplicate audit archived the producer's span and kept a deepened one under a
new ULID, the report names the live sibling — that re-point is a triage decision
(#501), never the seed's to make.

Note the asymmetry a dry run cannot fix: `deleted_entities` is pruned at 90 days
(`scripts/prune_outbox.py`), so an anchor broken by an older merge resolves as
``missing`` rather than ``merged``. ``missing`` therefore means "PM cannot say",
never "it never existed".

Usage:
    # dry run:
    uv run python -m scripts.seed_producer_crosswalk --export data/anchor-export
    uv run python -m scripts.seed_producer_crosswalk --export data/anchor-export --execute
"""

import argparse
import asyncio
import json
from datetime import datetime
from pathlib import Path

import asyncpg

from scripts._dsn import add_dsn_args, resolve_dsn
from src.core.ingestion.crosswalk import (
    Anchor,
    AnchorFormatError,
    SeedReport,
    load_anchors,
    parse_anchors,
    verify_digest,
)
from src.core.logging import configure_logging, get_logger

logger = get_logger(__name__)

ANCHORS_FILE = "anchors.csv"
MANIFEST_FILE = "manifest.json"


class BlockedSeed(RuntimeError):
    """The report says a human has to look before anything is written."""


def read_export(export_dir: Path) -> tuple[list[Anchor], dict]:
    """Read and verify an export directory, returning its anchors and manifest."""
    export_dir = Path(export_dir)
    raw = (export_dir / ANCHORS_FILE).read_bytes()
    manifest = json.loads((export_dir / MANIFEST_FILE).read_text())

    for key in ("sha256", "exported_at"):
        if key not in manifest:
            raise AnchorFormatError(f"{MANIFEST_FILE} has no {key!r}")

    # Before parsing, not after: a truncated file is a shorter valid CSV.
    verify_digest(raw, manifest["sha256"])
    return parse_anchors(raw.decode("utf-8")), manifest


def _log_report(report: SeedReport) -> None:
    for status, count in sorted(report.counts.items()):
        logger.info("  %-22s %d", status, count)
    for entry in report.unresolved:
        logger.warning(
            "  UNRESOLVED %s %s -> %s (%s)",
            entry.anchor.kind,
            entry.anchor.producer_id,
            entry.anchor.pm_id,
            entry.status,
        )
    for (kind, pm_id), producer_ids in report.collisions.items():
        logger.warning("  COLLISION  %s %s <- %s", kind, pm_id, ", ".join(producer_ids))
    for entry in report.supersessions:
        logger.warning(
            "  SUPERSEDED %s %s -> archived %s, live sibling(s) %s",
            entry.anchor.kind,
            entry.anchor.producer_id,
            entry.archived_pm_id,
            ", ".join(entry.live_siblings),
        )
    for kind, producer_id in report.stale:
        logger.warning("  STALE      %s %s is no longer in the export", kind, producer_id)


async def seed(
    db: asyncpg.Connection, export_dir: Path, *, source: str, execute: bool
) -> SeedReport:
    """Resolve an export against ``db``; write it only when nothing blocks."""
    anchors, manifest = read_export(export_dir)
    generated_at = datetime.fromisoformat(manifest["exported_at"].replace("Z", "+00:00"))
    logger.info("%d anchor(s) from %s", len(anchors), export_dir)

    if not execute:
        report = await load_anchors(
            db,
            source,
            anchors,
            execute=False,
            export_generated_at=generated_at,
            export_sha256=manifest["sha256"],
        )
        _log_report(report)
        logger.info("Dry run — pass --execute to seed the crosswalk")
        return report

    # One pass, inside the transaction: resolving twice would read the database at
    # two instants, so a merge landing between them makes the report describe
    # something other than what was written. The rollback — not a prior pass — is
    # what makes a blocking report write nothing.
    async with db.transaction():
        report = await load_anchors(
            db,
            source,
            anchors,
            execute=True,
            export_generated_at=generated_at,
            export_sha256=manifest["sha256"],
        )
        _log_report(report)
        if report.is_blocking:
            raise BlockedSeed(
                f"{len(report.unresolved)} unresolvable anchor(s) and "
                f"{len(report.collisions)} collision(s) — resolve them before seeding"
            )

    logger.info("Seeded %d crosswalk row(s)", len(anchors))
    return report


async def run(dsn: str, export_dir: Path, *, source: str, execute: bool) -> None:
    """Connect and seed; raises `BlockedSeed` rather than writing a partial scope."""
    conn = await asyncpg.connect(dsn)
    try:
        await seed(conn, export_dir, source=source, execute=execute)
    finally:
        await conn.close()


def main() -> None:
    configure_logging()
    parser = argparse.ArgumentParser(description=__doc__)
    add_dsn_args(parser)
    parser.add_argument(
        "--export",
        type=Path,
        required=True,
        help=f"Directory holding {ANCHORS_FILE} + {MANIFEST_FILE}",
    )
    parser.add_argument(
        "--source",
        default="usa_wa",
        help="Producer key recorded on every row (default: usa_wa)",
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Commit changes (default is dry run)",
    )
    args = parser.parse_args()
    dsn = resolve_dsn(args, parser)
    asyncio.run(run(dsn, args.export, source=args.source, execute=args.execute))


if __name__ == "__main__":
    main()
