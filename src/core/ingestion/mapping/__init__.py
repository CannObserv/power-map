"""PM's dbt-duckdb mapping project (#497): usa-wa ontology → PM desired state.

This package is the seam between the snapshot store (#496) and the diff-applier
(#499). It owns the dbt project beside it and the one way to run it, `run_dbt`,
which turns "which snapshot version, which duckdb file, where is the export"
into the environment the profile and sources read.

Models never open a database connection. Everything they read is a file —
usa-wa's `data.csv` per dataset version and Parquet exports of PM's own tables
— which is what lets `dbt build` run hermetically in the unit tier.
"""

import os
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

import yaml
from dbt.adapters.duckdb.connections import DuckDBConnectionManager
from dbt.cli.main import dbtRunner, dbtRunnerResult

from src.core.ingestion.datasets import DATA_FILE, SnapshotStore
from src.core.ingestion.mapping.parquet import PM_EXPORT_DIR, export_table
from src.core.logging import get_logger

__all__ = [
    "PM_EXPORT_DIR",
    "PM_SOURCES",
    "PROJECT_DIR",
    "RunPaths",
    "USA_WA_SOURCES",
    "load_manifest",
    "run_dbt",
    "source_env",
    "write_desired_state",
]

logger = get_logger(__name__)

PROJECT_DIR = Path(__file__).resolve().parent
MANIFEST_PATH = PROJECT_DIR / "manifest.yml"

# dataset name → the env var its source reads. Adding a source means adding it
# here and in models/sources.yml; `test_project.py` holds the two together.
USA_WA_SOURCES: dict[str, str] = {
    "persons": "PM_SRC_PERSONS",
    "organizations": "PM_SRC_ORGANIZATIONS",
    "person_crosswalk": "PM_SRC_PERSON_CROSSWALK",
    "org_crosswalk": "PM_SRC_ORG_CROSSWALK",
}

# PM table → env var, read from `<snapshot_root>/_pm/<table>.parquet`.
PM_SOURCES: dict[str, str] = {
    "producer_crosswalk": "PM_SRC_PRODUCER_CROSSWALK",
    "curation_overlay": "PM_SRC_CURATION_OVERLAY",
}


@dataclass(frozen=True)
class RunPaths:
    """Where one dbt invocation reads from and writes to."""

    snapshot_root: Path
    duckdb_path: str
    target_path: Path
    log_path: Path


def source_env(
    snapshot_root: Path | str, *, versions: Mapping[str, str] | None = None
) -> dict[str, str]:
    """Env vars pointing each declared source at a concrete file.

    A usa-wa dataset resolves to a **named** version — the one in ``versions``
    if given, else the newest the store holds. A dataset the store lacks is
    left unset, so its source renders as ``''`` and the model that reads it
    fails at run time rather than at parse.
    """
    store = SnapshotStore(snapshot_root)
    env: dict[str, str] = {}
    for name, var in USA_WA_SOURCES.items():
        version = (versions or {}).get(name) or (store.versions(name) or [None])[-1]
        if version is not None:
            env[var] = str(store.version_dir(name, version) / DATA_FILE)
    for table, var in PM_SOURCES.items():
        path = Path(snapshot_root) / PM_EXPORT_DIR / f"{table}.parquet"
        if path.exists():
            env[var] = str(path)
    return env


@contextmanager
def _environ(overrides: Mapping[str, str]) -> Iterator[None]:
    """Set env vars for the duration of one in-process dbt invocation."""
    saved = {k: os.environ.get(k) for k in overrides}
    os.environ.update(overrides)
    try:
        yield
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def run_dbt(
    args: list[str],
    *,
    snapshot_root: Path | str,
    duckdb_path: str,
    target_path: Path | str | None = None,
    versions: Mapping[str, str] | None = None,
) -> dbtRunnerResult:
    """Invoke dbt in-process against the project, with every path made explicit.

    ``args`` is the dbt subcommand and its flags (``["build"]``,
    ``["parse"]``, ``["test", "--select", "..."]``). Project, profile, target
    and log paths are always supplied here so nothing depends on the cwd or on
    ``~/.dbt``.
    """
    root = Path(snapshot_root)
    target = Path(target_path) if target_path else root / ".dbt-target"
    paths = RunPaths(root, duckdb_path, target, target / "logs")
    env = {"PM_MAPPING_DUCKDB": paths.duckdb_path, "PM_SNAPSHOT_ROOT": str(root)}
    env.update(source_env(root, versions=versions))
    full = [
        *args,
        "--project-dir",
        str(PROJECT_DIR),
        "--profiles-dir",
        str(PROJECT_DIR),
        "--target-path",
        str(paths.target_path),
        "--log-path",
        str(paths.log_path),
    ]
    with _environ(env):
        try:
            return dbtRunner().invoke(full)
        finally:
            # dbt runs in-process and its adapter keeps the duckdb file open in
            # a module-level singleton. duckdb refuses a second connection to
            # the same file with a different configuration, so the export
            # step — or a test's read-only connection — would fail until the
            # adapter let go. Release it here, every time, so a built file is
            # a plain file the moment `run_dbt` returns.
            DuckDBConnectionManager.close_all_connections()


def load_manifest() -> dict:
    """The ownership manifest — what each desired-state table claims (#499's contract)."""
    with MANIFEST_PATH.open() as f:
        return yaml.safe_load(f)


def write_desired_state(duckdb_path: Path | str, out_dir: Path | str) -> dict[str, int]:
    """Copy every manifest table out of a built duckdb file as Parquet.

    One file per table under ``out_dir``, each staged and moved into place, so
    #499 can treat a file's presence as the whole table. Returns row counts.
    """
    out = Path(out_dir)
    tables = list(load_manifest()["tables"])
    counts = {table: export_table(duckdb_path, table, out / f"{table}.parquet") for table in tables}
    # The directory is #499's input. A .parquet left over from a table the
    # manifest no longer names would read as a live claim, so it goes; nothing
    # else in the directory is ours to touch (CR 3).
    for stale in out.glob("*.parquet"):
        if stale.stem not in tables:
            stale.unlink()
            logger.warning("removed stale desired-state file %s — not in the manifest", stale.name)
    return counts
