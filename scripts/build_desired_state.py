"""Build PM's desired state from the snapshot store (#497).

Step 2 of the #490 PM-side pipeline, after the puller (#496) and the PM-table
export (`scripts/export_pm_tables.py`): run the dbt-duckdb mapping project and
copy every table the ownership manifest declares out to Parquet under
`data/desired_state/` — the diffable artifact the applier (#499) reads.

**No `--execute` and no DSN.** This never opens a database connection; it reads
files and writes files. The PM tables it joins arrive through the export step,
which is where the DSN lives and is echoed.

**The held snapshots are checked against their pins first (#553).** The puller's
pin gate covers landing; the build resolves each source to the newest version
the store holds and would otherwise read it under whatever the models now say.
A held contract that is not its pin refuses the build by name. A version that
states no contract anywhere is pre-usa-wa#385 and only warns — it cannot be
compared, and no re-mint is worth blocking every build on.

Exit codes: 0 built and written; 1 a held snapshot is not its pin, the build
failed, or a dbt test *errored* (a dbt *warning* — the fixture-known blank
names — does not fail the run, but is printed); 2 usage. A successful run also
writes `BUILD.json` beside the tables: the dataset versions and contracts, the
producer heartbeat the last pull recorded (#551), and the PM-export digests.

Usage:
    uv run --group mapping python -m scripts.build_desired_state
    uv run --group mapping python -m scripts.build_desired_state --root /srv/snap --out /srv/out
"""

import sys
from pathlib import Path

from scripts._dsn import build_parser
from src.core.ingestion.mapping import (
    check_contracts,
    run_dbt,
    write_build_info,
    write_desired_state,
)
from src.core.logging import configure_logging, get_logger

logger = get_logger(__name__)

DEFAULT_ROOT = "data/usa_wa_snapshots"
DEFAULT_OUT = "data/desired_state"
DEFAULT_DUCKDB = "data/mapping.duckdb"


def build(root: Path, out: Path, duckdb_path: Path) -> int:
    """Check the held contracts, run `dbt build`, report, and export; return the exit code."""
    # Before dbt, not after: a build from the wrong shape is not a build to
    # inspect. The chain stops here, so the applier never sees its output.
    if findings := check_contracts(root):
        for finding in findings:
            logger.error("  CONTRACT  %s", finding)
        logger.error(
            "the held snapshots do not match the pins in models/sources.yml — "
            "run scripts/pull_datasets.py, or revert the re-pin"
        )
        return 1
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
    info = write_build_info(out, snapshot_root=root, counts=counts)
    logger.info("  built from %s", ", ".join(f"{k}@{v}" for k, v in info["datasets"].items()))
    logger.info("desired state written: %d table(s) under %s", len(counts), out)
    return 0


def main(argv: list[str] | None = None) -> int:
    """Entry point; returns the process exit code."""
    configure_logging()
    parser = build_parser(__doc__)
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
