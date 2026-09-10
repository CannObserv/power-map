"""Export PM's own tables to Parquet for the mapping models (#497).

The models run in duckdb and never open a database connection: everything
they read is a file. Two of those files are PM's — `producer_crosswalk` (#495,
the row scope and the producer → PM id join) and `curation_overlay` (#497, the
PM-wins overrides). This script is the one place they cross the seam.

**Read-only, so no `--execute`.** The #402/#399 rule gates writes to the
production *database*; this issues only SELECTs and writes only files under
the snapshot store, each staged and moved into place so a reader never sees a
partial table. The DSN still resolves through `scripts/_dsn.py` and is echoed,
because "which database am I reading" is worth a line even when nothing is
written.

Usage:
    uv run --group mapping "${env_args[@]}" python -m scripts.export_pm_tables
    uv run --group mapping "${env_args[@]}" python -m scripts.export_pm_tables --test
    uv run --group mapping "${env_args[@]}" python -m scripts.export_pm_tables --root /srv/snapshots

Exit codes: 0 both tables written; 1 a table is absent on the target (schema.sql
not yet applied there); 2 usage.
"""

import argparse
import asyncio
import sys
from collections.abc import Awaitable, Callable
from functools import partial
from pathlib import Path

import asyncpg

from scripts._dsn import add_dsn_args, resolve_dsn
from src.core.ingestion.mapping.parquet import PM_EXPORT_DIR, TableSpec, write_parquet
from src.core.logging import configure_logging, get_logger

logger = get_logger(__name__)

DEFAULT_ROOT = "data/usa_wa_snapshots"

# Column order here is Parquet column order — the models' `sources.yml`
# reads by name, but the integration test pins the order so a reordered
# SELECT cannot silently swap two TEXT columns.
TABLES: dict[str, TableSpec] = {
    "producer_crosswalk": TableSpec(
        name="producer_crosswalk",
        columns=(
            "id",
            "source",
            "kind",
            "producer_id",
            "exported_pm_id",
            "pm_id",
            "resolution",
            "export_generated_at",
            "export_sha256",
            "created_at",
            "updated_at",
        ),
        types=("TEXT",) * 7 + ("TIMESTAMPTZ", "TEXT", "TIMESTAMPTZ", "TIMESTAMPTZ"),
    ),
    "curation_overlay": TableSpec(
        name="curation_overlay",
        columns=(
            "id",
            "entity_type",
            "entity_id",
            "field",
            "value",
            "note",
            "created_by",
            "created_at",
            "updated_at",
        ),
        types=("TEXT",) * 7 + ("TIMESTAMPTZ", "TIMESTAMPTZ"),
    ),
}

Fetcher = Callable[[TableSpec], Awaitable[list[tuple]]]


async def fetch_rows(conn: asyncpg.Connection, spec: TableSpec) -> list[tuple]:
    """SELECT ``spec.columns`` from ``spec.name``, ordered by id for a stable file."""
    cols = ", ".join(spec.columns)
    records = await conn.fetch(f"SELECT {cols} FROM {spec.name} ORDER BY id")
    return [tuple(r) for r in records]


async def run(fetch: Fetcher, *, root: Path | str) -> dict[str, int]:
    """Write every table in ``TABLES`` under ``<root>/_pm/``; return row counts."""
    out = Path(root) / PM_EXPORT_DIR
    report: dict[str, int] = {}
    for name, spec in TABLES.items():
        rows = await fetch(spec)
        report[name] = write_parquet(rows, spec, out / f"{name}.parquet")
        logger.info("  exported  %-20s %d row(s)", name, report[name])
    return report


async def _run_against(dsn: str, root: str) -> dict[str, int]:
    conn = await asyncpg.connect(dsn)
    try:
        return await run(partial(fetch_rows, conn), root=root)
    finally:
        await conn.close()


def main(argv: list[str] | None = None) -> int:
    """Entry point; returns the process exit code."""
    configure_logging()
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    add_dsn_args(parser)
    parser.add_argument(
        "--root", default=DEFAULT_ROOT, help=f"Snapshot store (default {DEFAULT_ROOT})"
    )
    args = parser.parse_args(argv)
    dsn = resolve_dsn(args, parser)
    try:
        asyncio.run(_run_against(dsn, args.root))
    except asyncpg.UndefinedTableError as exc:
        # A deploy state, not a bug here: curation_overlay lands on a target
        # when schema.sql is applied to it. Say which table and why rather
        # than leaving a traceback for the operator to decode.
        logger.error("%s — has src/core/schema.sql been applied to this target?", exc)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
