"""The diff-applier engine (#499): desired state → minimal writes, dry by default.

Generic and domain-free: every PM table and column it touches is named by the
ownership manifest (`src/core/ingestion/mapping/manifest.yml`), never here.
The engine reads the desired-state Parquet tables the mapping project wrote,
reads row scope from the **live** `producer_crosswalk` — membership with
resolution live or merged, never the `pm_id` a desired row carries — and
computes typed diff entries per binding shape. A dry run stops at the report;
`--execute` writes in one transaction and re-diffs inside it before commit.

Everything the engine reads from the database goes through the `LiveStore`
protocol, so the unit tier runs it against a dict-backed fake and the
integration tier against asyncpg.
"""

import dataclasses
import json
import re
from collections.abc import Container, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from functools import partial
from pathlib import Path
from types import MappingProxyType
from typing import Protocol

from src.core.ingestion.crosswalk import IN_SCOPE, PRODUCER_SOURCE, TOMBSTONE_TYPE
from src.core.ingestion.mapping import BUILD_INFO, Manifest
from src.core.ingestion.mapping.manifest import Index, Lookup, TableSpec
from src.core.ingestion.mapping.parquet import read_records

__all__ = [
    "ENTRY_KINDS",
    "IN_SCOPE",
    "ApplierError",
    "CrosswalkRow",
    "DesiredState",
    "Diff",
    "Entry",
    "LiveStore",
    "Minted",
    "Scope",
    "actionable_merges",
    "diff_desired",
    "entry_id",
    "scope_rows",
    "sql_identifier",
]

# noop     the live row already says what the desired row says
# create   an entity the producer publishes that PM has no row for
# insert   a child row (name, acronym, event) PM lacks
# update   owned columns differ on a row PM has
# retract  an in-scope row the producer no longer publishes, under `retraction:
#          report` — or, under `archive`, a row PM archived or restored by its own
#          hand, which curation keeps (#527). Report-only either way
# stale    the desired state disagrees with the live crosswalk — rebuild
# conflict more than one live row matches a keyed child, or a create or restore
#          would take a slot a live row holds (#424) — a person decides
# merge    a producer tombstone: actionable when `effects` names the primitive
#          that acts on it (#514), report-only when it names none
# archive  an in-scope row the producer no longer publishes, under `retraction:
#          archive` (#527): archived, and its anchor stamped `retracted_at`
# restore  a row the applier archived that the producer publishes again (#527)
ENTRY_KINDS = (
    "noop",
    "create",
    "insert",
    "update",
    "retract",
    "stale",
    "conflict",
    "merge",
    "archive",
    "restore",
)


class ApplierError(RuntimeError):
    """The applier cannot proceed; the message says why and what to do."""


_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")


def sql_identifier(name: str) -> str:
    """A table or column name the manifest supplied, checked before it reaches SQL."""
    if not isinstance(name, str) or not _IDENTIFIER.match(name):
        raise ApplierError(f"{name!r} is not a plain SQL identifier")
    return name


class LiveStore(Protocol):
    """What the engine reads from the database — and nothing else.

    ``entity_rows`` and ``child_rows`` always carry ``id`` back, and
    ``entity_rows`` always carries ``archived_at``, whatever ``columns`` asks
    for: the engine reads both on rows it requested no columns of at all (an
    entity binding owns no column, and still has to see a row archived since
    the export). An implementation that honours the signature literally would
    disable that check in silence, so it is stated here and asserted of both
    implementations.
    """

    async def crosswalk(self, source: str, kinds: Sequence[str]) -> list[dict]: ...

    async def entity_rows(
        self, table: str, ids: Sequence[str], columns: Sequence[str]
    ) -> dict[str, dict]: ...

    async def child_rows(
        self, table: str, parent: str, parent_ids: Sequence[str], columns: Sequence[str]
    ) -> dict[str, list[dict]]: ...

    async def lookup(self, table: str, from_col: str, to_col: str) -> dict[str, str]: ...

    async def tombstones(self, entity_type: str, ids: Sequence[str]) -> dict[str, str | None]: ...

    async def merge_preview(self, primitive: str, loser_id: str, survivor_id: str) -> dict: ...

    async def value_matches(
        self, table: str, column: str, values: Sequence, parent: str
    ) -> dict[object, list[str]]: ...

    async def cascade_counts(
        self, cascades: Mapping[str, Sequence[str]], ids: Sequence[str]
    ) -> dict[str, dict[str, int]]:
        """Per id, the unarchived rows of each cascade table naming it in any of its
        columns — what the database archives with it (#527)."""
        ...

    async def dependent_ids(
        self, dependents: Mapping[str, Sequence[str]], ids: Sequence[str]
    ) -> dict[str, dict[str, list[str]]]:
        """Per id, the unarchived rows of each dependent table naming it — what an
        archive would leave behind (#529)."""
        ...

    async def slot_holders(
        self, table: str, index: Index, tuples: Sequence[tuple]
    ) -> dict[tuple, list[dict]]:
        """Rows of ``table`` holding each tuple on ``index``, as ``{id, archived_at}`` (#527).

        NULLs are equal — the partial identity indexes are ``NULLS NOT DISTINCT``
        — so the unarchived holders are exactly the rows an insert or unarchive of
        that tuple would collide with, and the archived ones are the hint that PM
        once held it. A row the index does not cover — its ``when`` unmet — is no
        holder, and a folded column compares as the index stores it (#529).
        """
        ...


@dataclass(frozen=True)
class CrosswalkRow:
    """One live `producer_crosswalk` row, as scope sees it."""

    kind: str
    producer_id: str
    pm_id: str | None
    resolution: str
    # #527: the time the applier archived the row this anchor names — the same
    # transaction's NOW() it writes to the row's `archived_at`, which is the
    # provenance that lets it restore its own archives and no one else's
    retracted_at: object = None


@dataclass(frozen=True)
class Minted:
    """A reference to the row this run mints for ``(entity, producer_id)`` (#527).

    Stands in an identity column's value until the writer knows the id; as text
    — which is how the digest and the report see it — it names what it points at.
    """

    entity: str
    producer_id: str

    def __str__(self) -> str:
        return f"minted:{self.entity}:{self.producer_id}"


@dataclass(frozen=True)
class Entry:
    """One line of the diff. `entry_id` is stable across runs so a decision can name it."""

    entry_id: str
    table: str
    kind: str
    producer_id: str | None
    pm_id: str | None
    changes: dict[str, tuple[object, object]] = field(default_factory=dict)
    reason: str | None = None
    hint: tuple[dict, ...] = ()
    row_id: str | None = None  # the PM child row an update targets
    # merge only: what acting on it does — {"primitive", "preview"} or
    # {"primitive", "already_merged"}. Empty = report-only. Enters the digest.
    effects: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.kind not in ENTRY_KINDS:
            raise ValueError(f"unknown entry kind {self.kind!r}")


def entry_id(spec: TableSpec, row: dict) -> str:
    """``<table>:<key values>`` — keyed on the producer's identity, so a create keeps
    its id across the flip that gives it a pm_id."""
    return f"{spec.name}:" + "|".join(str(row.get(k)) for k in spec.key)


class Scope:
    """Row scope, read from the live crosswalk at run time."""

    def __init__(self, rows: Iterable[CrosswalkRow]):
        self._rows = {(r.kind, r.producer_id): r for r in rows}

    @classmethod
    async def load(cls, store: LiveStore, *, source: str, kinds: Sequence[str]) -> "Scope":
        rows = await store.crosswalk(source, kinds)
        return cls(
            CrosswalkRow(
                r["kind"], r["producer_id"], r.get("pm_id"), r["resolution"], r.get("retracted_at")
            )
            for r in rows
        )

    def row(self, kind: str, producer_id: str) -> CrosswalkRow | None:
        return self._rows.get((kind, producer_id))

    def resolve(self, kind: str, producer_id: str) -> str | None:
        """The in-scope PM id for a producer id, else None."""
        r = self.row(kind, producer_id)
        return r.pm_id if r is not None and r.resolution in IN_SCOPE else None

    def in_scope(self, kind: str) -> set[str]:
        return {
            r.producer_id
            for r in self._rows.values()
            if r.kind == kind and r.resolution in IN_SCOPE
        }


@dataclass(frozen=True)
class DesiredState:
    """The mapping project's output, loaded: rows per manifest table plus BUILD.json."""

    tables: dict[str, list[dict]]
    build_info: dict | None

    @classmethod
    def load(cls, directory: Path | str, manifest: Manifest) -> "DesiredState":
        root = Path(directory)
        tables: dict[str, list[dict]] = {}
        for name in manifest.tables:
            path = root / f"{name}.parquet"
            if not path.exists():
                raise ApplierError(
                    f"desired state has no {name}.parquet under {root} — "
                    "run scripts/build_desired_state.py first"
                )
            tables[name] = read_records(path)
        info_path = root / BUILD_INFO
        build_info = json.loads(info_path.read_text()) if info_path.exists() else None
        return cls(tables=tables, build_info=build_info)


def scope_rows(
    spec: TableSpec, rows: Sequence[dict], scope: Scope
) -> tuple[list[dict], list[Entry]]:
    """Split a table's desired rows into the ones scope agrees with and `stale` entries.

    A desired row carries the pm_id the crosswalk export said at build time;
    the live crosswalk is the authority. Any disagreement — a merge since, an
    archive since, a re-seed, a producer id PM linked since — means the desired
    state must be rebuilt before anything is applied. Merge tables pass through
    untouched: a tombstoned loser has no producer row to scope here, so
    `_diff_merge` checks its anchor itself (#514).
    """
    if spec.target.shape == "merge":
        return list(rows), []
    kept: list[dict] = []
    stale: list[Entry] = []

    def _stale(row: dict, reason: str) -> None:
        stale.append(
            Entry(
                entry_id=entry_id(spec, row),
                table=spec.name,
                kind="stale",
                producer_id=row.get("producer_id"),
                pm_id=row.get(spec.pm_key),
                reason=reason,
            )
        )

    for row in rows:
        producer_id = row["producer_id"]
        desired = row.get(spec.pm_key)
        live = scope.row(spec.entity, producer_id)
        if desired is None:
            if live is not None:
                _stale(
                    row,
                    f"desired state says create, live crosswalk resolves {producer_id} to "
                    f"{live.pm_id} ({live.resolution}) — rebuild, never mint",
                )
            else:
                kept.append(row)
        elif live is None:
            _stale(
                row, f"no crosswalk row for {producer_id} (desired {desired}) — re-seed or rebuild"
            )
        elif live.resolution not in IN_SCOPE:
            _stale(row, f"anchor left scope: {live.resolution} — rebuild")
        elif live.pm_id != desired:
            _stale(
                row,
                f"pm_id drifted: desired {desired}, live {live.pm_id} ({live.resolution}); rebuild",
            )
        else:
            kept.append(row)
    return kept, stale


@dataclass
class Diff:
    """Every entry of one run: scope's `stale` entries, then each shape in turn —
    entity bindings first, a referenced entity's before the binding naming it
    (#527) — each table in manifest order and its rows in key order."""

    entries: list[Entry]

    def by_kind(self, kind: str) -> list[Entry]:
        return [e for e in self.entries if e.kind == kind]

    @property
    def counts(self) -> dict[str, int]:
        return {kind: sum(1 for e in self.entries if e.kind == kind) for kind in ENTRY_KINDS}


def actionable_merges(diff: Diff) -> list[Entry]:
    """The merges a run would act on — `effects` names their primitive (#514).

    Non-empty puts the run in the **merge phase**: the verdict weighs only what a
    merge can trip, and an execute writes these and nothing else.
    """
    return [e for e in diff.entries if e.kind == "merge" and e.effects]


def _with_minted(
    spec: TableSpec, rows: Sequence[dict], minted: Mapping[tuple[str, str], str]
) -> list[dict]:
    """Desired rows with this run's minted ids filled in, so the re-diff inside the
    execute transaction sees a created row as anchored, not as a create the live
    crosswalk now contradicts."""
    if not minted or spec.target.shape == "merge":
        return list(rows)
    out = []
    for row in rows:
        pm_id = minted.get((spec.entity, row.get("producer_id")))
        if row.get(spec.pm_key) is None and pm_id is not None:
            row = {**row, spec.pm_key: pm_id}
        out.append(row)
    return out


async def diff_desired(
    state: DesiredState,
    manifest: Manifest,
    store: LiveStore,
    *,
    source: str = PRODUCER_SOURCE,
    minted: Mapping[tuple[str, str], str] | None = None,
) -> Diff:
    """Diff the whole desired state against the live database, read-only.

    Order matters and is fixed: scope every table first (stale entries), then
    entity shapes (so creates are known), then columns, then child rows, then
    merges. Nothing here writes; the caller decides what to do with the diff.
    ``minted`` — (kind, producer_id) → pm_id for entities created by the run
    that is now re-diffing inside its transaction — is applied to the desired
    rows first (`_with_minted`).

    Rows are taken in key order, never file order (CR 11): the Parquet a build
    writes carries no ORDER BY, and a shape with state across rows — the
    canonical claim in `_diff_child` — must reach the same answer, and the run
    the same digest, whichever order a rebuild happened to write.
    """
    # Every kind a binding scopes, and every kind an identity reference names —
    # an assignment resolves its role through the crosswalk before roles are a
    # binding of their own (#527).
    kinds = sorted(
        {spec.entity for spec in manifest.tables.values()}
        | {
            i.entity
            for spec in manifest.tables.values()
            for i in spec.target.identity.values()
            if i.entity
        }
    )
    scope = await Scope.load(store, source=source, kinds=kinds)
    entries: list[Entry] = []
    kept: dict[str, list[dict]] = {}
    for name, spec in manifest.tables.items():
        ordered = sorted(
            _with_minted(spec, state.tables[name], minted or {}), key=partial(entry_id, spec)
        )
        rows, stale = scope_rows(spec, ordered, scope)
        kept[name] = rows
        entries.extend(stale)

    creating: set[tuple[str, str]] = set()
    restored: dict[str, dict[tuple, list[str]]] = {}  # binding → slot → rows restored onto it
    order = _entity_order(manifest)
    lookups = {name: await _identity_lookups(spec, store) for name, spec in order}
    # Absence first, across every archiving binding (#529): the dependents guard
    # below has to know what this plan archives before any binding decides a
    # restore or a create, and a binding's own absences free its slots.
    absent: dict[str, list[Entry]] = {}
    archived_now: dict[str, set[str]] = {}
    for name, spec in order:
        if spec.retraction != "archive":
            continue
        archived_now[name] = set()
        absent[name] = await _diff_absent(
            spec,
            kept[name],
            state,
            manifest,
            scope,
            store,
            archived_now[name],
            lookups[name],
        )
    archiving_tables = {
        manifest.tables[name].target.table: ids for name, ids in archived_now.items()
    }
    for name, spec in order:
        if spec.target.dependents and absent.get(name):
            absent[name] = await _guard_dependents(
                spec, absent[name], archiving_tables, archived_now[name], store
            )
    for name, spec in order:
        found = await _diff_entity(
            spec,
            kept[name],
            state,
            manifest,
            scope,
            store,
            creating,
            restored,
            absent.get(name, []),
            archived_now.get(name, set()),
            lookups[name],
        )
        entries.extend(found)
        creating |= {(spec.entity, e.producer_id) for e in found if e.kind == "create"}
    plan = _entity_plan(manifest, entries, restored)
    for name, spec in manifest.tables.items():
        if spec.target.shape == "column":
            entity = _entity_binding(manifest, spec)
            entries.extend(
                await _diff_column(spec, kept[name], creating, store, entity=entity, plan=plan)
            )
    for name, spec in manifest.tables.items():
        if spec.target.shape == "child":
            entries.extend(await _diff_child(spec, kept[name], creating, state, scope, store))
    for name, spec in manifest.tables.items():
        if spec.target.shape == "merge":
            entries.extend(await _diff_merge(spec, kept[name], scope, store))
    return Diff(entries)


def _entry(spec: TableSpec, row: dict, kind: str, **kw) -> Entry:
    return Entry(
        entry_id=entry_id(spec, row),
        table=spec.name,
        kind=kind,
        producer_id=row.get("producer_id"),
        pm_id=row.get(spec.pm_key),
        **kw,
    )


def _index_columns(indexes: Sequence[Index]) -> tuple[str, ...]:
    """Every column the indexes key on or test — what a row must carry to be placed."""
    out: list[str] = []
    for index in indexes:
        for col in (*index.columns, *index.when):
            if col not in out:
                out.append(col)
    return tuple(out)


def _index_of(indexes: Sequence[Index], values: Mapping[str, object]) -> tuple[int, Index] | None:
    """The index covering a row with these values: the first whose ``when`` holds (#529).

    None when none covers it — the row is outside every partial index, so it takes
    no slot and collides with nothing.
    """
    for i, index in enumerate(indexes):
        if all((values.get(col) is None) == (state == "null") for col, state in index.when.items()):
            return i, index
    return None


def _slot_key(i: int, index: Index, values: Mapping[str, object]) -> tuple:
    """The slot a row takes on ``index``, as the index stores it (#529).

    A folded column compares folded, so two rows of one run whose values differ
    only in case are rivals — the collision ``slot_holders`` cannot report,
    because neither row is live for it to find. Folding the probe's own tuple is
    harmless: the store folds again.
    """
    return (
        i,
        tuple(
            v.lower() if index.fold.get(c) == "lower" and isinstance(v, str) else v
            for c, v in ((c, values.get(c)) for c in index.columns)
        ),
    )


async def _identity_lookups(spec: TableSpec, store: LiveStore) -> dict[Lookup, dict[str, str]]:
    """Each PM vocabulary an identity column reads, once per run (#529)."""
    out: dict[Lookup, dict[str, str]] = {}
    for ident in spec.target.identity.values():
        if ident.lookup is not None and ident.lookup not in out:
            out[ident.lookup] = await store.lookup(
                ident.lookup.table, ident.lookup.from_column, ident.lookup.to_column
            )
    return out


def _entity_plan(
    manifest: Manifest,
    entries: Sequence[Entry],
    restored: Mapping[str, Mapping[tuple, list[str]]],
) -> "_EntityPlan":
    """Collect what the entity bindings decided, for the column bindings after them."""
    slots: dict[str, dict[tuple, list[str]]] = {}
    for e in entries:
        spec = manifest.tables.get(e.table)
        if e.kind != "create" or spec is None or not spec.target.unique_live:
            continue
        values = {col: new for col, (_, new) in e.changes.items()}
        placed = _index_of(spec.target.unique_live, values)
        if placed is None:
            continue
        slot = _slot_key(*placed, values)
        if not any(isinstance(v, Minted) for v in slot[1]):
            slots.setdefault(e.table, {}).setdefault(slot, []).append(e.producer_id)
    return _EntityPlan(
        archiving=frozenset(e.pm_id for e in entries if e.kind == "archive"),
        restoring=frozenset(e.pm_id for e in entries if e.kind == "restore"),
        create_slots=slots,
        restore_slots=restored,
    )


def _entity_order(manifest: Manifest) -> list[tuple[str, TableSpec]]:
    """Entity bindings in manifest order, except that a binding whose `identity`
    references another entity comes after that entity's own binding — so a
    create it names is already known, whichever order the manifest lists them."""
    pending = [(n, s) for n, s in manifest.tables.items() if s.target.shape == "entity"]
    providers = {s.entity for _, s in pending}
    ordered: list[tuple[str, TableSpec]] = []
    placed: set[str] = set()
    while pending:
        ready = [
            (n, s)
            for n, s in pending
            if {i.entity for i in s.target.identity.values() if i.entity} & providers - {s.entity}
            <= placed
        ]
        if not ready:
            names = ", ".join(n for n, _ in pending)
            raise ApplierError(f"entity bindings reference each other in a cycle: {names}")
        for item in ready:
            ordered.append(item)
            placed.add(item[1].entity)
            pending.remove(item)
    return ordered


def _absent(spec: TableSpec, state: DesiredState, manifest: Manifest, scope: Scope) -> list[str]:
    """In-scope producer ids of ``spec.entity`` the snapshot no longer carries.

    An absent producer id a tombstone accounts for is a merge, not a retraction
    (#514): before the merge it is a loser in this build's merge table; after it,
    its anchor resolves to a row a published id still claims.
    """
    present = {r["producer_id"] for r in state.tables[spec.name]}
    merged_away = {
        r.get("loser_producer_id")
        for other in manifest.tables.values()
        if other.target.shape == "merge" and other.entity == spec.entity
        for r in state.tables[other.name]
    }
    claimed = {scope.resolve(spec.entity, p) for p in present} - {None}
    return [
        producer_id
        for producer_id in sorted(scope.in_scope(spec.entity) - present - merged_away)
        if scope.resolve(spec.entity, producer_id) not in claimed
    ]


async def _diff_entity(
    spec: TableSpec,
    rows: Sequence[dict],
    state: DesiredState,
    manifest: Manifest,
    scope: Scope,
    store: LiveStore,
    creating: set[tuple[str, str]],
    restored: dict[str, dict[tuple, list[str]]],
    absent: Sequence[Entry],
    archived_now: set[str],
    lookups: Mapping[Lookup, dict[str, str]],
) -> list[Entry]:
    table = spec.target.table
    archiving = spec.retraction == "archive"
    anchored = [r for r in rows if r.get(spec.pm_key) is not None]
    unique = _index_columns(spec.target.unique_live)
    live = (
        await store.entity_rows(table, [r[spec.pm_key] for r in anchored], columns=unique)
        if anchored
        else {}
    )
    # `diff_desired` decided this binding's absences before any binding's restores
    # and creates, so the guard could see them (#529).
    entries: list[Entry] = list(absent)

    restoring: list[tuple[dict, dict]] = []
    for row in anchored:
        pm_id = row[spec.pm_key]
        found = live.get(pm_id)
        if found is None:
            entries.append(_entry(spec, row, "stale", reason=f"live row missing: {table}/{pm_id}"))
        elif found.get("archived_at") is None:
            entries.append(_entry(spec, row, "noop"))
        elif not archiving:
            entries.append(
                _entry(spec, row, "stale", reason=f"live row archived since the export: {pm_id}")
            )
        elif _applier_archived(scope.row(spec.entity, row["producer_id"]), found):
            restoring.append((row, found))
        else:
            entries.append(
                _entry(
                    spec,
                    row,
                    "retract",
                    reason="archived in PM while the producer still publishes it; PM's archive"
                    " stands and nothing is written",
                )
            )
    restores, taken = await _diff_restores(spec, restoring, archived_now, store)
    entries.extend(restores)
    if taken:
        restored[spec.name] = taken

    creates = [r for r in rows if r.get(spec.pm_key) is None]
    hints = await _create_hints(spec, creates, state, manifest, store)
    entries.extend(
        await _diff_creates(
            spec, creates, hints, scope, creating, archived_now, taken, lookups, store
        )
    )

    if spec.retraction == "report":
        for producer_id in _absent(spec, state, manifest, scope):
            row = {"producer_id": producer_id, spec.pm_key: scope.resolve(spec.entity, producer_id)}
            entries.append(
                _entry(
                    spec,
                    row,
                    "retract",
                    reason="absent from the snapshot; policy is report, nothing is written",
                )
            )
    return entries


def _applier_archived(anchor: CrosswalkRow, live_row: Mapping) -> bool:
    """Whether the row's archive is the applier's own (#527, CR 1).

    Its archive stamps the anchor's `retracted_at` and the row's `archived_at`
    with one transaction's NOW(), so the two are equal. The stamp outlives a
    person's restore; if they archive the row again, that archive carries its own
    time and stays PM's.
    """
    return anchor.retracted_at is not None and anchor.retracted_at == live_row.get("archived_at")


async def _diff_absent(
    spec: TableSpec,
    rows: Sequence[dict],
    state: DesiredState,
    manifest: Manifest,
    scope: Scope,
    store: LiveStore,
    archived_now: set[str],
    lookups: Mapping[Lookup, dict[str, str]],
) -> list[Entry]:
    """Under `retraction: archive`, what absence means for each in-scope row (#527).

    Its live row archives — unless a person restored it after the applier had
    archived it (`retracted_at` still set), which curation keeps. A row already
    archived stays so; one that is gone is `stale`. ``archived_now`` collects the
    rows this plan archives, which no longer hold a slot for a restore or create.
    Each archive names, in ``effects``, the published spans on its `supersession`
    tuple — the pairing #501's triage reads (usa-wa#289: a collapsed span). That
    pairing is keyed on the archived row's own live values, so a published row
    naming an entity this run mints matches nothing by construction — which is
    why nothing here needs to know this run's creates, and could not: absences
    are decided before any binding's (#529).
    """
    absent = _absent(spec, state, manifest, scope)
    if not absent:
        return []
    table = spec.target.table
    pairing = spec.target.supersession
    ids = {p: scope.resolve(spec.entity, p) for p in absent}
    found = await store.entity_rows(table, sorted(set(ids.values())), columns=tuple(pairing))
    published: dict[tuple, list[str]] = {}
    if pairing:
        for row in rows:
            resolved = _resolve_identity(spec, row, scope, frozenset(), lookups)
            if isinstance(resolved, dict):
                key = tuple(resolved.get(c) for c in pairing)
                published.setdefault(key, []).append(row["producer_id"])
    entries: list[Entry] = []
    for producer_id in absent:
        pm_id = ids[producer_id]
        row = {"producer_id": producer_id, spec.pm_key: pm_id}
        live_row = found.get(pm_id)
        if live_row is None:
            entries.append(_entry(spec, row, "stale", reason=f"live row missing: {table}/{pm_id}"))
        elif live_row.get("archived_at") is not None:
            entries.append(_entry(spec, row, "noop"))
        elif scope.row(spec.entity, producer_id).retracted_at is not None:
            entries.append(
                _entry(
                    spec,
                    row,
                    "retract",
                    reason="restored in PM after the applier archived it; the producer still"
                    " omits it, PM's restore stands and nothing is written",
                )
            )
        else:
            archived_now.add(pm_id)
            covering = published.get(tuple(live_row.get(c) for c in pairing), []) if pairing else []
            entries.append(
                _entry(
                    spec,
                    row,
                    "archive",
                    reason="absent from the snapshot; policy is archive",
                    effects={"superseded_by": sorted(covering)} if covering else {},
                )
            )
    return await _with_cascades(spec, entries, store)


async def _guard_dependents(
    spec: TableSpec,
    entries: Sequence[Entry],
    archiving_tables: Mapping[str, set[str]],
    archived_now: set[str],
    store: LiveStore,
) -> list[Entry]:
    """An archive that would leave live rows naming the row is a `conflict` (#529).

    The dependents this same plan archives do not count — the ordinary re-key
    drops a role's spans with it. What is left is PM's own, or another producer's,
    and which of the two records is right is a person's call. A blocked archive
    keeps its slot, so a create or restore that counted on it conflicts too rather
    than colliding.
    """
    ids = sorted({e.pm_id for e in entries if e.kind == "archive"})
    if not ids:
        return list(entries)
    found = await store.dependent_ids(spec.target.dependents, ids)
    out: list[Entry] = []
    for e in entries:
        left = {
            table: [r for r in rows if r not in archiving_tables.get(table, set())]
            for table, rows in (found.get(e.pm_id, {}) if e.kind == "archive" else {}).items()
        }
        left = {table: rows for table, rows in left.items() if rows}
        if not left:
            out.append(e)
            continue
        archived_now.discard(e.pm_id)
        named = "; ".join(
            f"{len(rows)} in {table} ({', '.join(sorted(rows)[:5])}"
            + (", …)" if len(rows) > 5 else ")")
            for table, rows in sorted(left.items())
        )
        out.append(
            dataclasses.replace(
                e,
                kind="conflict",
                effects={},
                reason=f"archiving it would leave live rows naming it — {named}; a person decides",
            )
        )
    return out


async def _with_cascades(spec: TableSpec, entries: list[Entry], store: LiveStore) -> list[Entry]:
    """Each archive with the rows the database archives along with it (#527) —
    the #301 relationships on an assignment. Previewed because a restore does not
    bring them back; enters the digest as what the archive does."""
    cascades = spec.target.cascades
    ids = sorted({e.pm_id for e in entries if e.kind == "archive"})
    if not cascades or not ids:
        return entries
    counts = await store.cascade_counts(cascades, ids)
    out: list[Entry] = []
    for e in entries:
        found = (
            {t: n for t, n in counts.get(e.pm_id, {}).items() if n} if e.kind == "archive" else {}
        )
        out.append(dataclasses.replace(e, effects={**e.effects, "cascades": found}) if found else e)
    return out


def _resolve_identity(
    spec: TableSpec,
    row: dict,
    scope: Scope,
    creating: Container[tuple[str, str]],
    lookups: Mapping[Lookup, dict[str, str]] = MappingProxyType({}),
) -> dict[str, object] | str:
    """PM column → value for everything a create of ``row`` writes; a reference
    resolves to its in-scope PM id, or to the row this run mints for it, and a
    slug through the vocabulary its `lookup` names (#529). Returns the reason
    instead when one of them resolves nowhere — the row cannot be written."""
    out: dict[str, object] = {}
    for col, ident in spec.target.identity.items():
        value = row.get(col)
        if ident.lookup is not None and value is not None:
            found = lookups.get(ident.lookup, {}).get(value)
            if found is None:
                return (
                    f"names {ident.lookup.table} {value!r}, which PM does not carry;"
                    " a create cannot be written without it"
                )
            value = found
        if ident.entity is not None and value is None:
            return f"names no {ident.entity}; a create cannot be written without one"
        if ident.entity is not None:
            pm_id = scope.resolve(ident.entity, value)
            if pm_id is None:
                if (ident.entity, value) not in creating:
                    return f"names {ident.entity} {value}, neither in scope nor created this run"
                pm_id = Minted(ident.entity, value)
            value = pm_id
        out[ident.column] = value
    return out


async def _diff_creates(
    spec: TableSpec,
    creates: Sequence[dict],
    hints: Mapping[str, tuple[dict, ...]],
    scope: Scope,
    creating: set[tuple[str, str]],
    archived_now: set[str],
    restored: Mapping[tuple, list[str]],
    lookups: Mapping[Lookup, dict[str, str]],
    store: LiveStore,
) -> list[Entry]:
    """A `create` per unanchored row, carrying the identity its INSERT writes (#527).

    A reference that resolves nowhere is `stale`. On a binding with
    `unique_live`, a create whose tuple a live row holds — or a restore of this
    run takes back, or another create of this run shares — is a `conflict`: the
    INSERT would collide. A holder this plan archives first does not count. An
    archived holder is a hint: PM once held this slot, and the row may be the
    same tenure.
    """
    table = spec.target.table
    indexes = spec.target.unique_live
    entries: list[Entry] = []
    pending: list[tuple[dict, dict[str, object]]] = []
    for row in creates:
        resolved = _resolve_identity(spec, row, scope, creating, lookups)
        if isinstance(resolved, str):
            entries.append(_entry(spec, row, "stale", reason=resolved))
        else:
            pending.append((row, resolved))

    # Each create takes a slot on the one index that covers it, if any (#529).
    slots: dict[str, tuple] = {}
    for row, resolved in pending:
        placed = _index_of(indexes, resolved)
        if placed is not None:
            slots[row["producer_id"]] = _slot_key(*placed, resolved)
    # A tuple naming a row this run mints has no holder yet, so it is not probed —
    # but two creates can still share it (CR 2).
    probe: dict[int, list[tuple]] = {}
    for i, key in dict.fromkeys(slots.values()):
        if not any(isinstance(v, Minted) for v in key) and key not in probe.setdefault(i, []):
            probe[i].append(key)
    holders: dict[tuple, list[dict]] = {}
    for i, keys in probe.items():
        found = await store.slot_holders(table, indexes[i], keys)
        holders.update({(i, key): rows for key, rows in found.items()})
    sharing: dict[tuple, list[str]] = {}
    for producer_id, key in slots.items():
        sharing.setdefault(key, []).append(producer_id)

    for row, resolved in pending:
        producer_id = row["producer_id"]
        key = slots.get(producer_id)
        columns = indexes[key[0]].columns if key is not None else ()
        found = holders.get(key, []) if key is not None else []
        blocking = sorted(
            h["id"] for h in found if h.get("archived_at") is None and h["id"] not in archived_now
        )
        restoring = sorted(restored.get(key, [])) if key is not None else []
        rivals = sorted(p for p in sharing.get(key, []) if p != producer_id) if key else []
        if blocking or restoring or rivals:
            held = ", ".join(
                [
                    *blocking,
                    *(f"the restore of {p}" for p in restoring),
                    *(f"the create of {p}" for p in rivals),
                ]
            )
            entries.append(
                _entry(
                    spec,
                    row,
                    "conflict",
                    reason=f"creating it would take the slot {held} holds on"
                    f" ({', '.join(columns)}); a person decides",
                )
            )
            continue
        archived = tuple(
            {"table": table, "columns": list(columns), "archived_holder": h["id"]}
            for h in sorted(found, key=lambda h: h["id"])
            if h.get("archived_at") is not None
        )
        entries.append(
            _entry(
                spec,
                row,
                "create",
                changes={col: (None, value) for col, value in resolved.items()},
                hint=tuple(hints.get(producer_id, ())) + archived,
            )
        )
    return entries


async def _diff_restores(
    spec: TableSpec,
    restoring: Sequence[tuple[dict, dict]],
    archived_now: set[str],
    store: LiveStore,
) -> tuple[list[Entry], dict[tuple, list[str]]]:
    """A `restore` per row the applier archived and the producer publishes again —
    or a `conflict` when a live row now holds its slot on the partial identity
    index (#424): unarchiving it would collide, and which of the two is the
    tenure is a person's call. A holder this plan archives first does not count.
    Two restores onto one slot conflict alike (CR 2). Returns the entries and the
    slots the restores take back, which a create or move of this run cannot."""
    if not restoring:
        return [], {}
    indexes = spec.target.unique_live
    tuples: dict[str, tuple | None] = {}
    for row, found in restoring:
        placed = _index_of(indexes, found)
        tuples[row[spec.pm_key]] = _slot_key(*placed, found) if placed is not None else None
    holders: dict[tuple, list[dict]] = {}
    probe: dict[int, list[tuple]] = {}
    for slot in tuples.values():
        if slot is not None and slot[1] not in probe.setdefault(slot[0], []):
            probe[slot[0]].append(slot[1])
    for i, keys in probe.items():
        found_rows = await store.slot_holders(spec.target.table, indexes[i], keys)
        holders.update({(i, key): rows for key, rows in found_rows.items()})
    sharing: dict[tuple, list[str]] = {}
    for pm_id, key in tuples.items():
        if key is not None:
            sharing.setdefault(key, []).append(pm_id)
    entries: list[Entry] = []
    taken: dict[tuple, list[str]] = {}
    for row, _found in restoring:
        pm_id = row[spec.pm_key]
        key = tuples[pm_id]
        columns = indexes[key[0]].columns if key is not None else ()
        blocking = sorted(
            h["id"]
            for h in holders.get(key, [])
            if h.get("archived_at") is None and h["id"] != pm_id and h["id"] not in archived_now
        )
        rivals = sorted(p for p in sharing.get(key, []) if p != pm_id) if key is not None else []
        if blocking or rivals:
            held = ", ".join([*blocking, *(f"the restore of {p}" for p in rivals)])
            entries.append(
                _entry(
                    spec,
                    row,
                    "conflict",
                    reason=f"restoring {pm_id} would take the slot {held} holds"
                    f" on ({', '.join(columns)}) — #424; a person decides",
                )
            )
        else:
            if key is not None:
                taken.setdefault(key, []).append(pm_id)
            entries.append(
                _entry(
                    spec,
                    row,
                    "restore",
                    reason="published again after the applier archived it; what archived"
                    " with it stays archived",
                )
            )
    return entries, taken


async def _create_hints(
    spec: TableSpec,
    creates: Sequence[dict],
    state: DesiredState,
    manifest: Manifest,
    store: LiveStore,
) -> dict[str, tuple[dict, ...]]:
    """For each create, the parents in a hinting child table already carrying its value —
    a person PM holds under another producer's anchor is a twin, not a create."""
    if not creates:
        return {}
    producers = {r["producer_id"] for r in creates}
    hints: dict[str, list[dict]] = {}
    for child in manifest.tables.values():
        target = child.target
        if not (target.shape == "child" and target.hint_on_create and child.entity == spec.entity):
            continue
        value_col = child.owned_columns[0]
        pm_col = target.columns[value_col]
        wanted = {
            r["producer_id"]: r[value_col]
            for r in state.tables[child.name]
            if r["producer_id"] in producers and r.get(value_col) is not None
        }
        if not wanted:
            continue
        matches = await store.value_matches(
            target.table, pm_col, sorted(set(wanted.values())), target.parent
        )
        for producer_id, value in wanted.items():
            for parent in sorted(matches.get(value, [])):
                hints.setdefault(producer_id, []).append(
                    {"table": target.table, "column": pm_col, "value": value, "parent": parent}
                )
    return {k: tuple(v) for k, v in hints.items()}


@dataclass(frozen=True)
class _EntityPlan:
    """What the entity bindings decided, as a column binding on the same table
    needs it (#527): which rows this plan archives and restores, and the slots
    its creates and restores take on the partial identity index."""

    archiving: frozenset[str] = frozenset()
    restoring: frozenset[str] = frozenset()
    create_slots: Mapping[str, Mapping[tuple, list[str]]] = field(default_factory=dict)
    restore_slots: Mapping[str, Mapping[tuple, list[str]]] = field(default_factory=dict)


def _entity_binding(manifest: Manifest, spec: TableSpec) -> TableSpec | None:
    """The entity binding of the PM table a column or child binding writes to."""
    return next(
        (
            s
            for s in manifest.tables.values()
            if s.target.shape == "entity"
            and s.entity == spec.entity
            and s.target.table == spec.target.table
        ),
        None,
    )


async def _diff_column(
    spec: TableSpec,
    rows: Sequence[dict],
    creating: set[tuple[str, str]],
    store: LiveStore,
    *,
    entity: TableSpec | None = None,
    plan: _EntityPlan = _EntityPlan(),
) -> list[Entry]:
    target = spec.target
    columns = target.columns  # desired column → PM column
    pm_columns = tuple(columns.values())
    # #527: the entity binding of the same table, when it has one, decides three
    # things here: which moves are moves on its partial identity index, what a
    # create already wrote, and whether an archived row is its report or stale.
    unique = _index_columns(entity.target.unique_live) if entity is not None else ()
    written = {i.column for i in entity.target.identity.values()} if entity is not None else set()
    archives = entity is not None and entity.retraction == "archive"
    asserted = set(target.asserts_null)
    anchored = [r for r in rows if r.get(spec.pm_key) is not None]
    live = (
        await store.entity_rows(
            target.table, [r[spec.pm_key] for r in anchored], (*pm_columns, *unique)
        )
        if anchored
        else {}
    )

    # A null in an owned column is silence, not an instruction to clear PM's
    # value (CR 5). `retraction: none` says an absent row says nothing; a
    # present row carrying a null must not say more than one that is missing.
    # `asserts_null` names the exceptions (#527): there, null is the value.
    def claimed(row: dict) -> list[str]:
        return [d for d in columns if row.get(d) is not None or d in asserted]

    entries: list[Entry] = []
    moving: list[tuple[dict, str, dict, dict]] = []
    for row in rows:
        pm_id = row.get(spec.pm_key)
        if pm_id is None:
            if (spec.entity, row["producer_id"]) not in creating:
                # The entity binding says why (a conflict, a reference that resolves
                # nowhere): a rebuild would not help, so point at it (CR 5).
                reason = "no entity row is created for it"
                if entity is not None:
                    reason += f"; see its {entity.name} entry"
                entries.append(_entry(spec, row, "stale", reason=reason))
                continue
            # The create's INSERT already wrote its identity, and a new row's
            # columns start null — so neither is a write here.
            changes = {
                columns[d]: (None, row[d])
                for d in claimed(row)
                if columns[d] not in written and row.get(d) is not None
            }
            entries.append(
                _entry(
                    spec,
                    row,
                    "update" if changes else "noop",
                    changes=changes,
                    reason="on a row this run creates" if changes else None,
                )
            )
            continue
        found = live.get(pm_id)
        if found is None:
            entries.append(
                _entry(spec, row, "stale", reason=f"live row missing: {target.table}/{pm_id}")
            )
            continue
        if found.get("archived_at") is not None and pm_id not in plan.restoring:
            if not archives:
                entries.append(
                    _entry(
                        spec, row, "stale", reason=f"live row archived since the export: {pm_id}"
                    )
                )
            # else: PM archived it and the entity binding has reported it (#527)
            continue
        changes = {
            columns[d]: (found.get(columns[d]), row.get(d))
            for d in claimed(row)
            if found.get(columns[d]) != row.get(d)
        }
        if unique and set(changes) & set(unique):
            after = {c: changes[c][1] if c in changes else found.get(c) for c in unique}
            moving.append((row, pm_id, after, changes))
        else:
            entries.append(_entry(spec, row, "update" if changes else "noop", changes=changes))
    if moving:
        entries.extend(await _diff_moves(spec, entity, moving, plan, store))
    return entries


async def _diff_moves(
    spec: TableSpec,
    entity: TableSpec,
    moving: Sequence[tuple[dict, str, dict, dict]],
    plan: _EntityPlan,
    store: LiveStore,
) -> list[Entry]:
    """Updates that move a row on its partial identity index (#527), checked as a
    create is: a live holder of the new slot — other than the row itself, and not
    archived by this plan — or another move, create or restore (CR 2) of this run
    onto it, is a `conflict`. Otherwise the UPDATE would fail the index
    mid-transaction."""
    indexes = entity.target.unique_live
    slots: dict[str, tuple | None] = {}
    for row, _, after, _ in moving:
        placed = _index_of(indexes, after)
        slots[row["producer_id"]] = _slot_key(*placed, after) if placed is not None else None
    probe: dict[int, list[tuple]] = {}
    for slot in slots.values():
        if slot is not None and slot[1] not in probe.setdefault(slot[0], []):
            probe[slot[0]].append(slot[1])
    holders: dict[tuple, list[dict]] = {}
    for i, keys in probe.items():
        found = await store.slot_holders(spec.target.table, indexes[i], keys)
        holders.update({(i, key): rows for key, rows in found.items()})
    sharing: dict[tuple, list[str]] = {}
    for row, _, _, _ in moving:
        slot = slots[row["producer_id"]]
        if slot is not None:
            sharing.setdefault(slot, []).append(row["producer_id"])
    creates = plan.create_slots.get(entity.name, {})
    restores = plan.restore_slots.get(entity.name, {})
    entries: list[Entry] = []
    for row, pm_id, _after, changes in moving:
        slot = slots[row["producer_id"]]
        columns = indexes[slot[0]].columns if slot is not None else ()
        blocking = sorted(
            h["id"]
            for h in holders.get(slot, [])
            if h.get("archived_at") is None and h["id"] != pm_id and h["id"] not in plan.archiving
        )
        rivals = [p for p in sharing.get(slot, []) if p != row["producer_id"]]
        rivals += [f"the create of {p}" for p in creates.get(slot, [])]
        rivals += [f"the restore of {p}" for p in restores.get(slot, []) if p != pm_id]
        if blocking or rivals:
            held = ", ".join([*blocking, *sorted(rivals)])
            entries.append(
                _entry(
                    spec,
                    row,
                    "conflict",
                    reason=f"moving {pm_id} would take the slot {held} holds on"
                    f" ({', '.join(columns)}); a person decides",
                )
            )
        else:
            entries.append(_entry(spec, row, "update", changes=changes))
    return entries


async def _diff_merge(
    spec: TableSpec, rows: Sequence[dict], scope: Scope, store: LiveStore
) -> list[Entry]:
    """A producer tombstone, classified against live state (#514).

    `noop` once the live crosswalk resolves the loser's producer id to the
    survivor — the merge ran, and the next build, or the re-diff inside the
    execute transaction, sees it. Report-only (`merge`, no effects) for a null
    survivor or a table no primitive binds. `stale` when the loser's live anchor
    no longer names the row the build exported — the rule `scope_rows` applies to
    every other shape. Otherwise both rows must be ones PM can write: an
    actionable `merge` carries the primitive's preview; a loser PM already folded
    into the survivor (a curator merged the pair first) is an actionable merge
    whose only work is the anchors; anything else is `stale`.
    """
    target = spec.target
    live: dict[str, dict] = {}
    if target.primitive is not None:
        ids = sorted({r[k] for r in rows for k in (spec.pm_key, "survivor_pm_id") if r.get(k)})
        live = await store.entity_rows(target.table, ids, ()) if ids else {}

    entries: list[Entry] = []
    for row in rows:
        loser, survivor = row.get(spec.pm_key), row.get("survivor_pm_id")

        def add(kind: str, reason: str | None = None, effects: dict | None = None) -> None:
            entries.append(
                Entry(
                    entry_id=entry_id(spec, row),
                    table=spec.name,
                    kind=kind,
                    producer_id=row.get("loser_producer_id"),
                    pm_id=loser,
                    changes={"survivor_pm_id": (None, survivor)},
                    reason=reason,
                    effects=effects or {},
                )
            )

        if survivor is None:
            add("merge", "survivor out of scope: report, never act")
            continue
        if target.primitive is None:
            add("merge", f"no merge primitive is bound for {spec.entity}: report-only")
            continue
        anchored = scope.resolve(spec.entity, row.get("loser_producer_id"))
        if anchored == survivor:
            add("noop")
            continue
        if anchored != loser:
            # `loser_pm_id` is the build's crosswalk export; the live crosswalk is
            # the authority (`scope_rows`). A fold through an anchor that no longer
            # names the loser could never re-point it, so it could never verify.
            now = anchored or "nothing in scope"
            add("stale", f"loser anchor drifted: desired {loser}, live {now} — rebuild")
            continue
        survivor_row = live.get(survivor)
        if survivor_row is None or survivor_row.get("archived_at") is not None:
            state = "missing from" if survivor_row is None else "archived in"
            add("stale", f"survivor {survivor} is {state} {target.table}: a person decides")
            continue
        loser_row = live.get(loser)
        if loser_row is not None and loser_row.get("archived_at") is not None:
            add("stale", f"loser {loser} is archived in {target.table}: a person decides")
        elif loser_row is not None:
            preview = await store.merge_preview(target.primitive, loser, survivor)
            add(
                "merge",
                f"fold {loser} into {survivor} through the {target.primitive} merge",
                {"primitive": target.primitive, "preview": preview},
            )
        elif (end := await _merged_into(store, TOMBSTONE_TYPE[spec.entity], loser)) == survivor:
            add(
                "merge",
                f"PM already merged {loser} into {survivor}: only the anchors re-point",
                {"primitive": target.primitive, "already_merged": True},
            )
        else:
            where = "with no survivor" if end is None else f"into {end}, not {survivor}"
            add("stale", f"loser {loser} left {target.table} {where}: a person decides")
    return entries


async def _merged_into(store: LiveStore, entity_type: str, pm_id: str) -> str | None:
    """Where ``pm_id``'s tombstones lead: the first id with none, or None.

    None when ``pm_id`` has no tombstone at all, when a hop records no survivor,
    or on a cycle — every case in which PM cannot say the row folded anywhere.
    """
    seen: set[str] = set()
    current = pm_id
    while current not in seen:
        seen.add(current)
        found = await store.tombstones(entity_type, [current])
        if current not in found:
            return None if current == pm_id else current
        if found[current] is None:
            return None
        current = found[current]
    return None


async def _diff_child(
    spec: TableSpec,
    rows: Sequence[dict],
    creating: set[tuple[str, str]],
    state: DesiredState,
    scope: Scope,
    store: LiveStore,
) -> list[Entry]:
    """A keyed child row of the entity (a name, an acronym, an event).

    `any_then_canonical`: any row of the key type carrying the value satisfies
    the claim; absent everywhere, the canonical row of that type is the one in
    dispute (`update`); no such row, `insert` — canonical only when the parent
    has no canonical row at all, and only for the first such insert of the run
    in key order — the flag is unique per parent, and which row takes it must
    not depend on file order. `key`: match on the key columns among
    unarchived rows — none is an `insert`, one compares the owned columns,
    more than one is a `conflict`. Under `retraction: report`, an owned event
    type on an in-scope parent that the snapshot no longer carries is a
    `retract` entry; every other live row is invisible.
    """
    target = spec.target
    columns = target.columns  # desired column → PM column

    # Every in-scope parent when absence is reported (an owned type PM holds
    # that the snapshot dropped), else only the parents the desired rows name.
    # Both are within scope, so nothing outside the crosswalk is ever read.
    if spec.retraction == "report":
        parents = sorted(
            {scope.resolve(spec.entity, p) for p in scope.in_scope(spec.entity)} - {None}
        )
    else:
        parents = sorted({r[spec.pm_key] for r in rows if r.get(spec.pm_key) is not None})
    if not rows and not parents:
        return []  # nothing to compare and nothing that could be retracted: read nothing
    lookups = {
        col: await store.lookup(lk["table"], lk["from"], lk["to"])
        for col, lk in target.lookups.items()
    }

    def pm_value(row: dict, desired_col: str) -> object:
        value = row.get(desired_col)
        if desired_col in lookups:
            if value not in lookups[desired_col]:
                table = target.lookups[desired_col]["table"]
                raise ApplierError(f"{spec.name}: unknown {desired_col} {value!r}; not in {table}")
            return lookups[desired_col][value]
        return value

    wanted = sorted(
        set(columns.values())
        | set(target.constants)
        | {c for c in (target.canonical, target.archived) if c}
    )
    live = await store.child_rows(target.table, target.parent, parents, wanted) if parents else {}

    def candidates(pm_id: str) -> list[dict]:
        found = live.get(pm_id, [])
        if target.archived:
            found = [r for r in found if r.get(target.archived) is None]
        if target.constants:
            found = [r for r in found if all(r.get(k) == v for k, v in target.constants.items())]
        return found

    def insert_changes(row: dict) -> dict[str, tuple[object, object]]:
        changes = {columns[c]: (None, pm_value(row, c)) for c in columns}
        changes.update({k: (None, v) for k, v in target.constants.items()})
        return changes

    # The canonical flag is the parent's *display pointer*, and PM's indexes
    # (uq_person_canonical_name, uq_org_canonical_name, uq_org_canonical_acronym)
    # are unique on the parent alone where it is true. Canonicality was decided
    # per row against the pre-write snapshot, so two inserts on one parent both
    # claimed it and the second aborted the transaction (CR 4). A claim made
    # here is remembered for the rest of the run.
    canonical_taken: set[object] = set()

    def claims_canonical(parent: object, already_taken: bool) -> bool:
        if already_taken or parent in canonical_taken:
            return False
        canonical_taken.add(parent)
        return True

    entries: list[Entry] = []
    seen: set[tuple[str, tuple]] = set()
    for row in rows:
        pm_id = row.get(spec.pm_key)
        if pm_id is None:
            if (spec.entity, row["producer_id"]) not in creating:
                entries.append(_entry(spec, row, "stale", reason="no entity row is created for it"))
                continue
            changes = insert_changes(row)
            if target.match == "any_then_canonical":
                # A new parent has no canonical row — but only its first insert.
                new_parent = ("create", row["producer_id"])
                changes[target.canonical] = (None, claims_canonical(new_parent, False))
            entries.append(
                _entry(spec, row, "insert", changes=changes, reason="on a row this run creates")
            )
            continue
        found = candidates(pm_id)
        if target.match == "any_then_canonical":
            if len(spec.owned_columns) != 1:
                raise ApplierError(f"{spec.name}: any_then_canonical needs one owned column")
            value_col = columns[spec.owned_columns[0]]
            desired = pm_value(row, spec.owned_columns[0])
            of_type = found
            if target.type_column:
                type_col = columns[target.type_column]
                type_value = pm_value(row, target.type_column)
                of_type = [r for r in found if r.get(type_col) == type_value]
            match = next((r for r in of_type if r.get(value_col) == desired), None)
            if match is not None:
                entries.append(_entry(spec, row, "noop", row_id=match.get("id")))
                continue
            canonical = next((r for r in of_type if r.get(target.canonical)), None)
            if canonical is not None:
                entries.append(
                    _entry(
                        spec,
                        row,
                        "update",
                        row_id=canonical.get("id"),
                        changes={value_col: (canonical.get(value_col), desired)},
                    )
                )
            else:
                changes = insert_changes(row)
                taken = any(r.get(target.canonical) for r in found)
                changes[target.canonical] = (None, claims_canonical(pm_id, taken))
                entries.append(_entry(spec, row, "insert", changes=changes))
        else:
            key = {columns[c]: pm_value(row, c) for c in target.key_columns}
            seen.add((pm_id, tuple(sorted(key.items()))))
            matches = [r for r in found if all(r.get(k) == v for k, v in key.items())]
            if not matches:
                entries.append(_entry(spec, row, "insert", changes=insert_changes(row)))
            elif len(matches) > 1:
                ids = ", ".join(str(r.get("id")) for r in matches)
                entries.append(
                    _entry(
                        spec,
                        row,
                        "conflict",
                        reason=f"{len(matches)} live rows match {key}: {ids}; a person decides",
                    )
                )
            else:
                m = matches[0]
                changes = {
                    columns[c]: (m.get(columns[c]), pm_value(row, c))
                    for c in spec.owned_columns
                    if m.get(columns[c]) != pm_value(row, c)
                }
                kind = "update" if changes else "noop"
                entries.append(_entry(spec, row, kind, row_id=m.get("id"), changes=changes))

    if spec.retraction == "report" and spec.owned_event_types and target.match == "key":
        type_desired = target.key_columns[0]
        type_pm = columns[type_desired]
        owned_values = {pm_value({type_desired: t}, type_desired) for t in spec.owned_event_types}
        reverse = {v: k for k, v in lookups.get(type_desired, {}).items()}
        for producer_id in sorted(scope.in_scope(spec.entity)):
            pm_id = scope.resolve(spec.entity, producer_id)
            for r in candidates(pm_id):
                if r.get(type_pm) not in owned_values:
                    continue
                key = tuple(sorted((columns[c], r.get(columns[c])) for c in target.key_columns))
                if (pm_id, key) in seen:
                    continue
                row = {"producer_id": producer_id, spec.pm_key: pm_id}
                row[type_desired] = reverse.get(r.get(type_pm), r.get(type_pm))
                entries.append(
                    _entry(
                        spec,
                        row,
                        "retract",
                        row_id=r.get("id"),
                        reason="absent from the snapshot; policy is report, nothing is written",
                    )
                )
    return entries
