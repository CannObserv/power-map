"""The ownership manifest, typed (#497 step 6; v2 in #499 step 1).

`manifest.yml` is the contract between the mapping models and the applier:
per desired-state table, what the producer claims (`key`, `retraction`,
`owned_columns`, `owned_event_types`) and — v2 — how a row binds to a PM table
(`target`). The applier interprets four binding shapes and nothing else:

    entity   a row in an entity table (people, organizations): identity, create,
             report-only retraction
    column   one column on that entity row (organizations.parent_id)
    child    a keyed child row of the entity (names, acronyms, events), matched
             by `any_then_canonical` or `key`
    merge    nothing — every row is a report entry (#514 acts on them)

The loader is typed and strict so a binding missing what its shape needs
fails here, at load, rather than at 09:30 in the nightly chain.
"""

from dataclasses import dataclass, field
from pathlib import Path

import yaml

__all__ = [
    "MANIFEST_PATH",
    "MATCHES",
    "RETRACTIONS",
    "SHAPES",
    "Manifest",
    "ManifestError",
    "TableSpec",
    "Target",
    "Thresholds",
    "load_manifest",
    "parse_manifest",
]

MANIFEST_PATH = Path(__file__).resolve().parent / "manifest.yml"
SHAPES = ("entity", "column", "child", "merge")
MATCHES = ("any_then_canonical", "key")
RETRACTIONS = ("none", "report", "archive")
_THRESHOLD_KEYS = ("creates", "merges", "conflicts", "stale", "updates")


class ManifestError(ValueError):
    """The manifest does not say what the applier needs it to say."""


@dataclass(frozen=True)
class Thresholds:
    """Counts an `--execute` refuses to exceed; ``None`` is unlimited."""

    creates: int = 0
    merges: int = 0
    conflicts: int = 0
    stale: int = 0
    updates: int | None = None


@dataclass(frozen=True)
class Target:
    """How one desired-state table binds to a PM table."""

    shape: str
    table: str | None = None
    parent: str | None = None
    columns: dict[str, str] = field(default_factory=dict)  # desired column → PM column
    match: str | None = None
    type_column: str | None = None  # any_then_canonical: rows "of the type" share it
    canonical: str | None = None  # any_then_canonical: the boolean PM column
    key_columns: list[str] = field(default_factory=list)  # key: desired columns of the match
    constants: dict[str, object] = field(default_factory=dict)  # PM column → literal
    lookups: dict[str, dict[str, str]] = field(default_factory=dict)  # desired col → table/from/to
    insert_defaults: dict[str, object] = field(default_factory=dict)  # PM column → literal
    archived: str | None = None  # key: match among rows where this is null
    hint_on_create: bool = False


@dataclass(frozen=True)
class TableSpec:
    """One desired-state table's claim and binding."""

    name: str
    entity: str
    key: list[str]
    pm_key: str
    retraction: str
    owned_columns: list[str]
    target: Target
    owned_event_types: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class Manifest:
    """The whole contract."""

    version: int
    tables: dict[str, TableSpec]
    thresholds: Thresholds
    streak: int


def _require(mapping: dict, key: str, where: str) -> object:
    if key not in mapping:
        raise ManifestError(f"{where}: {key} is required")
    return mapping[key]


def _target(name: str, raw: object) -> Target:
    where = f"{name}: target"
    if not isinstance(raw, dict):
        raise ManifestError(f"{name}: target is required")
    shape = raw.get("shape")
    if shape not in SHAPES:
        raise ManifestError(f"{where}: unknown shape {shape!r} (one of {', '.join(SHAPES)})")
    target = Target(
        shape=shape,
        table=raw.get("table"),
        parent=raw.get("parent"),
        columns=dict(raw.get("columns") or {}),
        match=raw.get("match"),
        type_column=raw.get("type_column"),
        canonical=raw.get("canonical"),
        key_columns=list(raw.get("key_columns") or []),
        constants=dict(raw.get("constants") or {}),
        lookups={k: dict(v) for k, v in (raw.get("lookups") or {}).items()},
        insert_defaults=dict(raw.get("insert_defaults") or {}),
        archived=raw.get("archived"),
        hint_on_create=bool(raw.get("hint_on_create", False)),
    )
    if shape in ("entity", "column", "child") and not target.table:
        raise ManifestError(f"{where}: shape {shape} requires target.table")
    if shape in ("column", "child") and not target.columns:
        raise ManifestError(f"{where}: shape {shape} requires target.columns")
    if shape == "child":
        if not target.parent:
            raise ManifestError(f"{where}: shape child requires target.parent")
        if target.match not in MATCHES:
            raise ManifestError(
                f"{where}: unknown match {target.match!r} (one of {', '.join(MATCHES)})"
            )
        if target.match == "any_then_canonical" and not target.canonical:
            raise ManifestError(f"{where}: match any_then_canonical requires target.canonical")
        if target.match == "key" and not target.key_columns:
            raise ManifestError(f"{where}: match key requires target.key_columns")
        for col in target.key_columns:
            if col not in target.columns:
                raise ManifestError(f"{where}: key column {col!r} is not in target.columns")
    for col, lookup in target.lookups.items():
        if col not in target.columns:
            raise ManifestError(f"{where}: lookup {col!r} is not in target.columns")
        for k in ("table", "from", "to"):
            if k not in lookup:
                raise ManifestError(f"{where}: lookup {col!r} needs {k}")
    return target


def _table(name: str, raw: object) -> TableSpec:
    if not isinstance(raw, dict):
        raise ManifestError(f"{name}: a mapping is required")
    retraction = _require(raw, "retraction", name)
    if retraction not in RETRACTIONS:
        raise ManifestError(f"{name}: unknown retraction {retraction!r}")
    key = list(_require(raw, "key", name))
    if not key:
        raise ManifestError(f"{name}: key must name at least one column")
    return TableSpec(
        name=name,
        entity=str(_require(raw, "entity", name)),
        key=key,
        pm_key=str(_require(raw, "pm_key", name)),
        retraction=str(retraction),
        owned_columns=list(_require(raw, "owned_columns", name)),
        owned_event_types=list(raw.get("owned_event_types") or []),
        target=_target(name, raw.get("target")),
    )


def _thresholds(raw: object) -> Thresholds:
    if raw is None:
        return Thresholds()
    if not isinstance(raw, dict):
        raise ManifestError("thresholds: a mapping is required")
    unknown = set(raw) - set(_THRESHOLD_KEYS)
    if unknown:
        raise ManifestError(f"thresholds: unknown key(s) {sorted(unknown)}")
    values = {}
    for k in _THRESHOLD_KEYS:
        v = raw.get(k, None if k == "updates" else 0)
        if v is not None and (not isinstance(v, int) or isinstance(v, bool) or v < 0):
            raise ManifestError(f"thresholds.{k}: a non-negative integer or null is required")
        values[k] = v
    return Thresholds(**values)


def parse_manifest(raw: dict) -> Manifest:
    """Validate a loaded YAML document into a `Manifest`; raise `ManifestError` otherwise."""
    if not isinstance(raw, dict):
        raise ManifestError("the manifest must be a mapping")
    version = raw.get("version")
    if version != 2:
        raise ManifestError(f"version 2 is required, found {version!r}")
    tables_raw = raw.get("tables")
    if not isinstance(tables_raw, dict) or not tables_raw:
        raise ManifestError("tables: at least one table is required")
    streak = raw.get("streak", 3)
    if not isinstance(streak, int) or isinstance(streak, bool) or streak < 1:
        raise ManifestError("streak: a positive integer is required")
    return Manifest(
        version=version,
        tables={name: _table(name, spec) for name, spec in tables_raw.items()},
        thresholds=_thresholds(raw.get("thresholds")),
        streak=streak,
    )


def load_manifest(path: Path | str = MANIFEST_PATH) -> Manifest:
    """Load and validate the manifest — the contract #499 reads."""
    with Path(path).open() as f:
        return parse_manifest(yaml.safe_load(f))
