"""PM's dbt-duckdb mapping project (#497): usa-wa ontology → PM desired state.

This package is the seam between the snapshot store (#496) and the diff-applier
(#499). It owns the dbt project beside it and the one way to run it, `run_dbt`,
which turns "which snapshot version, which duckdb file, where is the export"
into the environment the profile and sources read.

Models never open a database connection. Everything they read is a file —
usa-wa's `data.csv` per dataset version and Parquet exports of PM's own tables
— which is what lets `dbt build` run hermetically in the unit tier.
"""

import hashlib
import json
import os
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from dbt.adapters.duckdb.connections import DuckDBConnectionManager
from dbt.cli.main import dbtRunner, dbtRunnerResult

from src.core.ingestion.datasets import (
    DATA_FILE,
    PACKAGE_FILE,
    SNAPSHOT_FILE,
    CatalogError,
    SnapshotStore,
    Subscription,
    load_subscription,
    parse_moment,
)
from src.core.ingestion.mapping.manifest import MANIFEST_PATH, Manifest, load_manifest
from src.core.ingestion.mapping.parquet import PM_EXPORT_DIR, export_table
from src.core.logging import get_logger

__all__ = [
    "BUILD_INFO",
    "MANIFEST_PATH",
    "PM_EXPORT_DIR",
    "PM_SOURCES",
    "PROJECT_DIR",
    "Manifest",
    "RunPaths",
    "USA_WA_SOURCES",
    "check_contracts",
    "held_contract",
    "held_contracts",
    "load_manifest",
    "producer_state",
    "resolved_versions",
    "run_dbt",
    "source_env",
    "write_build_info",
    "write_desired_state",
]

logger = get_logger(__name__)

PROJECT_DIR = Path(__file__).resolve().parent
# Written beside the desired-state tables: which inputs the artifact came from.
BUILD_INFO = "BUILD.json"

# dataset name → the env var its source reads. Adding a source means adding it
# here and in models/sources.yml; `test_project.py` holds the two together.
USA_WA_SOURCES: dict[str, str] = {
    "persons": "PM_SRC_PERSONS",
    "organizations": "PM_SRC_ORGANIZATIONS",
    "person_crosswalk": "PM_SRC_PERSON_CROSSWALK",
    "org_crosswalk": "PM_SRC_ORG_CROSSWALK",
    "assignments": "PM_SRC_ASSIGNMENTS",
    "roles": "PM_SRC_ROLES",
}

# PM table → env var, read from `<snapshot_root>/_pm/<table>.parquet`.
PM_SOURCES: dict[str, str] = {
    "producer_crosswalk": "PM_SRC_PRODUCER_CROSSWALK",
    "curation_overlay": "PM_SRC_CURATION_OVERLAY",
    "role_types": "PM_SRC_ROLE_TYPES",  # #529: the qualifier policies (#273/#302)
}


@dataclass(frozen=True)
class RunPaths:
    """Where one dbt invocation reads from and writes to."""

    snapshot_root: Path
    duckdb_path: str
    target_path: Path
    log_path: Path


def resolved_versions(
    snapshot_root: Path | str, *, versions: Mapping[str, str] | None = None
) -> dict[str, str]:
    """The named version each usa-wa dataset resolves to — from ``versions`` if
    given, else the newest the store holds. A dataset the store lacks is absent."""
    store = SnapshotStore(snapshot_root)
    resolved: dict[str, str] = {}
    for name in USA_WA_SOURCES:
        version = (versions or {}).get(name) or (store.versions(name) or [None])[-1]
        if version is not None:
            resolved[name] = version
    return resolved


def _stated_contract(path: Path) -> str | None:
    """The bare `contract_hash` a provenance file states, or None.

    Unreadable counts as unstated: the fallback below then tries the other file,
    and a version that states nothing anywhere is already a defined outcome.
    """
    try:
        raw = json.loads(path.read_text()).get("contract_hash")
    except (OSError, ValueError, AttributeError):
        # ValueError covers both `json.JSONDecodeError` and the
        # `UnicodeDecodeError` a write truncated mid multi-byte character
        # raises from `read_text` (CR 1); AttributeError, a document whose top
        # level is not an object.
        return None
    # `snapshot.json` records it bare, `datapackage.json` prefixed.
    return raw.rpartition(":")[2].lower() if isinstance(raw, str) and raw else None


def held_contract(snapshot_root: Path | str, name: str, version: str) -> str | None:
    """The contract a held version states — `snapshot.json`, else `datapackage.json`.

    Only versions landed by the #536 puller record it in `snapshot.json`; the
    four sources landed before it carry it in `datapackage.json` alone, which
    usa-wa#385's baseline re-mint published. None means neither states one.
    """
    d = SnapshotStore(snapshot_root).version_dir(name, version)
    return _stated_contract(d / SNAPSHOT_FILE) or _stated_contract(d / PACKAGE_FILE)


def held_contracts(
    snapshot_root: Path | str, *, versions: Mapping[str, str] | None = None
) -> dict[str, str | None]:
    """The contract each resolved source's held snapshot states — `BUILD.json`'s record."""
    return {
        name: held_contract(snapshot_root, name, version)
        for name, version in resolved_versions(snapshot_root, versions=versions).items()
    }


def check_contracts(
    snapshot_root: Path | str,
    *,
    versions: Mapping[str, str] | None = None,
    subscription: Subscription | None = None,
) -> list[str]:
    """Why each resolved source must not be built from, one sentence each (#553).

    The pin in `models/sources.yml` gates **landing** (#536); `resolved_versions`
    consults nothing. A snapshot landed under an older contract stays the newest
    one the store holds until a replacement lands, so a build between deploying
    a re-pin and the next successful pull pairs the new models with the old
    shape. The nightly chain is safe by ordering alone — pull 09:00, build
    09:30 — which leaves the hand-run right after a re-pin, the one someone
    actually does.

    A version stating no contract anywhere is pre-usa-wa#385 and cannot be
    compared: that **warns and does not refuse**. Refusing would stop every
    build against a store landed before #536 until each dataset happened to
    re-mint, and a store that far behind is the staleness check's finding
    (#535/#551), not this one's.
    """
    pins = (subscription or load_subscription()).pins
    findings: list[str] = []
    for name, version in resolved_versions(snapshot_root, versions=versions).items():
        pin = pins.get(name)
        if pin is None:
            # A source the models read with no pin is what `test_project.py`'s
            # parity test forbids; here there is simply no contract to compare.
            continue
        pinned = pin.contract_hash
        contract = held_contract(snapshot_root, name, version)
        if contract is None:
            logger.warning(
                "%s %s states no contract_hash — landed before usa-wa#385, so the"
                " models cannot be checked against it; pull it again to check it",
                name,
                version,
            )
        elif contract != pinned:
            findings.append(
                f"{name} {version} holds contract sha256:{contract}, but the models are"
                f" pinned to sha256:{pinned} — pull before building"
            )
    return findings


def producer_state(snapshot_root: Path | str, *, now: datetime | None = None) -> dict | None:
    """What the last pull recorded of the publisher's heartbeat, judged at ``now`` (#551).

    None when no pull has recorded one — a hand-made store, or a pull older
    than #551. Unknown is not late, and nothing downstream reads it as late.
    """
    record = SnapshotStore(snapshot_root).pull_record()
    if record is None:
        return None
    try:
        deadline = parse_moment(record.get("stale_after"), label="pull.json stale_after")
    except CatalogError:
        # Someone edited the record; that is not evidence about the producer.
        logger.warning("pull.json states an unreadable stale_after — not read as stale")
        deadline = None
    return {**record, "stale": bool(deadline and (now or datetime.now(UTC)) > deadline)}


def source_env(
    snapshot_root: Path | str, *, versions: Mapping[str, str] | None = None
) -> dict[str, str]:
    """Env vars pointing each declared source at a concrete file.

    A usa-wa dataset resolves to a **named** version (`resolved_versions`). A
    dataset the store lacks is left unset, so its source renders as ``''`` and
    the model that reads it fails at run time rather than at parse.
    """
    store = SnapshotStore(snapshot_root)
    env: dict[str, str] = {}
    for name, version in resolved_versions(snapshot_root, versions=versions).items():
        env[USA_WA_SOURCES[name]] = str(store.version_dir(name, version) / DATA_FILE)
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

    **This does not check the held contracts** (#553). `check_contracts` is the
    build *script*'s gate, because every fixture build goes through here against
    a store that states no contract at all. A hand-run of `run_dbt` therefore
    builds from whatever the store holds — call `check_contracts` first if that
    matters (CR 7).
    """
    root = Path(snapshot_root)
    if target_path:
        target = Path(target_path)
    elif duckdb_path != ":memory:":
        # Beside the working file it describes, never inside the snapshot
        # store — that is a verbatim mirror of upstream (CR 4).
        target = Path(duckdb_path).resolve().parent / ".dbt-target"
    else:
        target = root / ".dbt-target"
    if duckdb_path != ":memory:":
        Path(duckdb_path).resolve().parent.mkdir(parents=True, exist_ok=True)
    paths = RunPaths(root, duckdb_path, target, target / "logs")
    env = {"PM_MAPPING_DUCKDB": paths.duckdb_path}
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


def write_desired_state(duckdb_path: Path | str, out_dir: Path | str) -> dict[str, int]:
    """Copy every manifest table out of a built duckdb file as Parquet.

    One file per table under ``out_dir``, each staged and moved into place, so
    #499 can treat a file's presence as the whole table. Returns row counts.
    """
    out = Path(out_dir)
    tables = list(load_manifest().tables)
    counts = {table: export_table(duckdb_path, table, out / f"{table}.parquet") for table in tables}
    # The directory is #499's input. A .parquet left over from a table the
    # manifest no longer names would read as a live claim, so it goes; nothing
    # else in the directory is ours to touch (CR 3).
    for stale in out.glob("*.parquet"):
        if stale.stem not in tables:
            stale.unlink()
            logger.warning("removed stale desired-state file %s — not in the manifest", stale.name)
    return counts


def _sha256(path: Path) -> str | None:
    if not path.exists():
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_build_info(
    out_dir: Path | str,
    *,
    snapshot_root: Path | str,
    counts: Mapping[str, int],
    versions: Mapping[str, str] | None = None,
    now: datetime | None = None,
) -> dict:
    """Record what the desired state was built from (#499; round-2 finding 24).

    `BUILD.json` beside the tables: the dataset version each source resolved
    to and the contract it holds (#553), the publisher's heartbeat as the last
    pull recorded it (#551), the digest of each PM export the models joined, the
    row counts, and when. The applier copies it into every run summary and
    ledger line, so a diff can always be traced to the inputs that produced it.

    ``now`` is the moment of this build: it stamps `built_at` and judges the
    producer's deadline, and defaults to the current moment. One parameter, one
    clock (CR 11) — it exists so the field deciding whether a run counts towards
    the `--execute` streak can be pinned to a moment in a test (CR 6).
    """
    root = Path(snapshot_root)
    at = now or datetime.now(UTC)
    info = {
        "built_at": at.strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
        "snapshot_root": str(root.resolve()),
        "datasets": resolved_versions(root, versions=versions),
        # Beside the versions: a diff traced to a version alone cannot say which
        # shape produced it, and a version is re-minted over unchanged data (#553).
        "contracts": held_contracts(root, versions=versions),
        # Whether the publisher was behind its own clock when this was built
        # (#551). The applier's ledger reads it; the gate refuses a streak built
        # on it.
        "producer": producer_state(root, now=at),
        "pm_exports": {
            table: _sha256(root / PM_EXPORT_DIR / f"{table}.parquet") for table in PM_SOURCES
        },
        "tables": dict(counts),
    }
    Path(out_dir).mkdir(parents=True, exist_ok=True)
    (Path(out_dir) / BUILD_INFO).write_text(json.dumps(info, indent=2, sort_keys=True) + "\n")
    return info
