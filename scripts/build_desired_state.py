"""Build PM's desired state from the snapshot store (#497).

Step 2 of the #490 PM-side pipeline, after the puller (#496) and the PM-table
export (`scripts/export_pm_tables.py`): run the dbt-duckdb mapping project and
copy every table the ownership manifest declares out to Parquet under
`data/desired_state/` — the diffable artifact the applier (#499) reads.

**No `--execute` and no DSN.** This never opens a database connection; it reads
files and writes files. The PM tables it joins arrive through the export step,
which is where the DSN lives and is echoed.

Exit codes: 0 built and written; 1 the build failed or a dbt test *errored*
(a dbt *warning* — the fixture-known blank names — does not fail the run, but
is printed); 2 usage.

Usage:
    uv run --group mapping python -m scripts.build_desired_state
    uv run --group mapping python -m scripts.build_desired_state --root /srv/snap --out /srv/out
"""

import argparse
import sys
from pathlib import Path

from src.core.ingestion.mapping import run_dbt, write_desired_state
from src.core.logging import configure_logging, get_logger

logger = get_logger(__name__)

DEFAULT_ROOT = "data/usa_wa_snapshots"
DEFAULT_OUT = "data/desired_state"
DEFAULT_DUCKDB = "data/mapping.duckdb"


def build(root: Path, out: Path, duckdb_path: Path) -> int:
    """Run `dbt build`, report, and export; return the exit code."""
    result = run_dbt(["build"], snapshot_root=root, duckdb_path=str(duckdb_path))
    nodes = list(result.result.results) if result.success or result.result else []
    for r in nodes:
        status = str(r.status)
        if status == "warn":
            logger.warning("  WARN      %s — %s", r.node.name, r.message)
        elif status not in ("success", "pass"):
            logger.error("  %-9s %s — %s", status.upper(), r.node.name, r.message)
    if not result.success:
        logger.error("dbt build failed: %s", result.exception or "see the node lines above")
        return 1
    counts = write_desired_state(duckdb_path, out)
    for table, n in counts.items():
        logger.info("  wrote     %-32s %d row(s)", table, n)
    logger.info("desired state written: %d table(s) under %s", len(counts), out)
    return 0


def main(argv: list[str] | None = None) -> int:
    """Entry point; returns the process exit code."""
    configure_logging()
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--root", default=DEFAULT_ROOT, help=f"Snapshot store (default {DEFAULT_ROOT})"
    )
    parser.add_argument(
        "--out", default=DEFAULT_OUT, help=f"Desired-state directory (default {DEFAULT_OUT})"
    )
    parser.add_argument(
        "--duckdb", default=DEFAULT_DUCKDB, help=f"duckdb working file (default {DEFAULT_DUCKDB})"
    )
    args = parser.parse_args(argv)
    return build(Path(args.root), Path(args.out), Path(args.duckdb))


if __name__ == "__main__":
    sys.exit(main())
