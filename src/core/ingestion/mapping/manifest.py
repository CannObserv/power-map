"""The ownership manifest, typed (#497 step 6; v2 in #499 step 1).

`manifest.yml` is the contract between the mapping models and the applier:
per desired-state table, what the producer claims (`key`, `retraction`,
`owned_columns`, `owned_event_types`) and — v2 — how a row binds to a PM table
(`target`). The applier interprets four binding shapes and nothing else:

    entity   a row in an entity table (people, organizations, role_assignments):
             identity, create, and retraction — reported, or (#527) archived,
             with `identity` naming what a create writes, `unique_live` the
             partial index a create or restore must not collide on, and
             `supersession` the tuple an archive is paired on in the report
    column   owned columns on that entity row (organizations.parent_id; the
             assignment dates, where `asserts_null` makes a null clear a value)
    child    a keyed child row of the entity (names, acronyms, events), matched
             by `any_then_canonical` or `key`
    merge    a producer tombstone: re-point the loser's PM row at the survivor's.
             Acted on only when the binding names a merge `primitive` (#514,
             persons); an unbound merge table is report-only (#520, orgs)

The loader is typed and strict so a binding missing what its shape needs
fails here, at load, rather than at 09:30 in the nightly chain.
"""

from dataclasses import dataclass, field
from pathlib import Path

import yaml

__all__ = [
    "MANIFEST_PATH",
    "MATCHES",
    "MERGE_PRIMITIVES",
    "OVERLAY_SHAPES",
    "RETRACTIONS",
    "SHAPES",
    "Identity",
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
# The merge primitives a `merge` binding may name — each is a core merge function
# the applier's registry (`applier_merge.MERGE_PRIMITIVES`) knows how to call.
MERGE_PRIMITIVES = ("person",)
_THRESHOLD_KEYS = ("creates", "merges", "conflicts", "stale", "archives", "restores", "updates")
# The keys only an entity binding carries (#527), and the one only a column
# binding does — each is refused on any other shape rather than ignored.
_ENTITY_KEYS = ("identity", "unique_live", "supersession", "cascades")
_IDENTITY_KEYS = ("column", "entity")


class ManifestError(ValueError):
    """The manifest does not say what the applier needs it to say."""


@dataclass(frozen=True)
class Thresholds:
    """Counts an `--execute` refuses to exceed; ``None`` is unlimited."""

    creates: int = 0
    merges: int = 0
    conflicts: int = 0
    stale: int = 0
    archives: int = 0  # #527: gated like creates — the first run is #501's to size
    restores: int = 0
    updates: int | None = None


@dataclass(frozen=True)
class Identity:
    """One column an entity create writes (#527), once and never again.

    A plain desired column is copied as it is; a reference (``entity`` set) is a
    producer id of that kind, resolved to its PM id from the live crosswalk or to
    the id this run mints for it.
    """

    column: str  # the PM column
    entity: str | None = None


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
    primitive: str | None = None  # merge: the core merge it acts through; None = report-only
    # entity (#527): desired column → what a create writes; the PM columns of the
    # partial index a create or restore must not collide on; the columns an
    # archive is paired on with the spans still published (triage, report-only)
    identity: dict[str, Identity] = field(default_factory=dict)
    unique_live: list[str] = field(default_factory=list)
    supersession: list[str] = field(default_factory=list)
    # entity (#527): tables whose rows the database archives with an archived row
    # (a trigger does it), each with the columns that point at it — previewed in
    # an archive's effects, since a restore does not bring them back
    cascades: dict[str, list[str]] = field(default_factory=dict)
    # column (#527): desired columns whose null is a value — "clear it" — rather
    # than silence (CR 5's default)
    asserts_null: list[str] = field(default_factory=list)


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
    # #498: the curation_overlay field that pins each owned column — the pair is
    # (entity, field). Required where a value is owned (column, child). Written
    # as one field for a table owning one value, or (#527) as a map, owned
    # column → field, where one binding owns several.
    overlays: dict[str, str] = field(default_factory=dict)


# The shapes that carry a producer-owned value a curator can pin (#498).
OVERLAY_SHAPES = ("column", "child")


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


def _identity(where: str, raw: object) -> dict[str, Identity]:
    if not isinstance(raw, dict):
        raise ManifestError(f"{where}: identity must map desired columns to what a create writes")
    out: dict[str, Identity] = {}
    for col, value in raw.items():
        if isinstance(value, str):
            out[col] = Identity(column=value)
            continue
        if not isinstance(value, dict) or "column" not in value:
            raise ManifestError(f"{where}: identity {col!r} names no PM column")
        unknown = set(value) - set(_IDENTITY_KEYS)
        if unknown:
            raise ManifestError(f"{where}: identity {col!r} has unknown key(s) {sorted(unknown)}")
        out[col] = Identity(column=str(value["column"]), entity=value.get("entity"))
    return out


def _target(name: str, raw: object) -> Target:
    where = f"{name}: target"
    if not isinstance(raw, dict):
        raise ManifestError(f"{name}: target is required")
    shape = raw.get("shape")
    if shape not in SHAPES:
        raise ManifestError(f"{where}: unknown shape {shape!r} (one of {', '.join(SHAPES)})")
    if shape != "entity":
        for key in _ENTITY_KEYS:
            if key in raw:
                raise ManifestError(f"{where}: only an entity binding names {key}, not {shape}")
    if shape != "column" and "asserts_null" in raw:
        raise ManifestError(f"{where}: only a column binding names asserts_null, not {shape}")
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
        primitive=raw.get("primitive"),
        identity=_identity(where, raw["identity"]) if "identity" in raw else {},
        unique_live=list(raw.get("unique_live") or []),
        supersession=list(raw.get("supersession") or []),
        cascades={t: list(cols or []) for t, cols in (raw.get("cascades") or {}).items()},
        asserts_null=list(raw.get("asserts_null") or []),
    )
    # The tuples are computed from what a create writes, so each names one of its
    # columns — a column the create never writes could not be checked at all.
    written = {i.column for i in target.identity.values()}
    for key in ("unique_live", "supersession"):
        for col in getattr(target, key):
            if col not in written:
                raise ManifestError(f"{where}: {key} column {col!r} is not an identity column")
    for table, cols in target.cascades.items():
        if not cols:
            raise ManifestError(f"{where}: cascades {table!r} names no column pointing at the row")
    for col in target.asserts_null:
        if col not in target.columns:
            raise ManifestError(f"{where}: asserts_null column {col!r} is not in target.columns")
    if target.primitive is not None:
        if shape != "merge":
            raise ManifestError(f"{where}: only a merge binding names a primitive, not {shape}")
        if target.primitive not in MERGE_PRIMITIVES:
            raise ManifestError(
                f"{where}: unknown primitive {target.primitive!r}"
                f" (one of {', '.join(MERGE_PRIMITIVES)})"
            )
        if not target.table:
            raise ManifestError(f"{where}: a merge with a primitive requires target.table")
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
    target = _target(name, raw.get("target"))
    if retraction == "archive":
        # Archiving is an entity's policy (#527), and its restore is fallible on a
        # partial identity index (#424) — so the index is named, or nothing checks it.
        if target.shape != "entity":
            raise ManifestError(f"{name}: retraction archive is for an entity binding")
        if not target.unique_live:
            raise ManifestError(
                f"{name}: retraction archive names unique_live — a restore must be checked"
                " against the partial index it could collide on (#424)"
            )
    owned = list(_require(raw, "owned_columns", name))
    overlay = raw.get("overlay")
    if target.shape in OVERLAY_SHAPES and not overlay:
        raise ManifestError(
            f"{name}: a {target.shape} binding owns a value, so it names its overlay field"
            " — without one a curator's correction cannot be pinned (#498)"
        )
    if target.shape not in OVERLAY_SHAPES and overlay is not None:
        raise ManifestError(f"{name}: an overlay field on a {target.shape} table pins nothing")
    if isinstance(overlay, dict):
        if set(overlay) != set(owned):
            raise ManifestError(
                f"{name}: an overlay map pins exactly the owned columns {sorted(owned)},"
                f" not {sorted(overlay)}"
            )
        overlays = {col: str(field_) for col, field_ in overlay.items()}
    elif overlay is not None:
        overlays = {col: str(overlay) for col in owned}
    else:
        overlays = {}
    return TableSpec(
        name=name,
        entity=str(_require(raw, "entity", name)),
        key=key,
        pm_key=str(_require(raw, "pm_key", name)),
        retraction=str(retraction),
        owned_columns=owned,
        owned_event_types=list(raw.get("owned_event_types") or []),
        target=target,
        overlays=overlays,
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
