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

import json
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from functools import partial
from pathlib import Path
from typing import Protocol

from src.core.ingestion.crosswalk import PRODUCER_SOURCE, TOMBSTONE_TYPE
from src.core.ingestion.mapping import BUILD_INFO, Manifest
from src.core.ingestion.mapping.manifest import TableSpec
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
    "Scope",
    "actionable_merges",
    "diff_desired",
    "entry_id",
    "scope_rows",
    "sql_identifier",
]

# Crosswalk resolutions that put a row in the applier's scope (docs/SCHEMA.md
# § Producer crosswalk). `archived` is in the table and out of scope.
IN_SCOPE = ("live", "merged")

# noop     the live row already says what the desired row says
# create   an entity the producer publishes that PM has no row for
# insert   a child row (name, acronym, event) PM lacks
# update   owned columns differ on a row PM has
# retract  an in-scope row the producer no longer publishes — report-only here
# stale    the desired state disagrees with the live crosswalk — rebuild
# conflict more than one live row matches a keyed child — a person decides
# merge    a producer tombstone: actionable when `effects` names the primitive
#          that acts on it (#514), report-only when it names none
ENTRY_KINDS = ("noop", "create", "insert", "update", "retract", "stale", "conflict", "merge")


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


@dataclass(frozen=True)
class CrosswalkRow:
    """One live `producer_crosswalk` row, as scope sees it."""

    kind: str
    producer_id: str
    pm_id: str | None
    resolution: str


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
            CrosswalkRow(r["kind"], r["producer_id"], r.get("pm_id"), r["resolution"]) for r in rows
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
    """Every entry of one run, in manifest table order then key order."""

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
    kinds = sorted({spec.entity for spec in manifest.tables.values()})
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
    for name, spec in manifest.tables.items():
        if spec.target.shape == "entity":
            found = await _diff_entity(spec, kept[name], state, manifest, scope, store)
            entries.extend(found)
            creating |= {(spec.entity, e.producer_id) for e in found if e.kind == "create"}
    for name, spec in manifest.tables.items():
        if spec.target.shape == "column":
            entries.extend(await _diff_column(spec, kept[name], creating, store))
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


async def _diff_entity(
    spec: TableSpec,
    rows: Sequence[dict],
    state: DesiredState,
    manifest: Manifest,
    scope: Scope,
    store: LiveStore,
) -> list[Entry]:
    if spec.retraction == "archive":
        raise ApplierError(
            f"{spec.name}: retraction 'archive' is #500's to build — this applier reports only"
        )
    table = spec.target.table
    anchored = [r for r in rows if r.get(spec.pm_key) is not None]
    live = (
        await store.entity_rows(table, [r[spec.pm_key] for r in anchored], columns=())
        if anchored
        else {}
    )
    entries: list[Entry] = []
    for row in anchored:
        pm_id = row[spec.pm_key]
        found = live.get(pm_id)
        if found is None:
            entries.append(_entry(spec, row, "stale", reason=f"live row missing: {table}/{pm_id}"))
        elif found.get("archived_at") is not None:
            entries.append(
                _entry(spec, row, "stale", reason=f"live row archived since the export: {pm_id}")
            )
        else:
            entries.append(_entry(spec, row, "noop"))

    creates = [r for r in rows if r.get(spec.pm_key) is None]
    hints = await _create_hints(spec, creates, state, manifest, store)
    for row in creates:
        entries.append(_entry(spec, row, "create", hint=hints.get(row["producer_id"], ())))

    if spec.retraction == "report":
        present = {r["producer_id"] for r in state.tables[spec.name]}
        # An absent producer id a tombstone accounts for is a merge, not a
        # retraction (#514): before the merge it is a loser in this build's merge
        # table; after it, its anchor resolves to a row a published id still claims.
        merged_away = {
            r.get("loser_producer_id")
            for name, other in manifest.tables.items()
            if other.target.shape == "merge" and other.entity == spec.entity
            for r in state.tables[name]
        }
        claimed = {scope.resolve(spec.entity, p) for p in present} - {None}
        for producer_id in sorted(scope.in_scope(spec.entity) - present - merged_away):
            if scope.resolve(spec.entity, producer_id) in claimed:
                continue
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


async def _diff_column(
    spec: TableSpec, rows: Sequence[dict], creating: set[tuple[str, str]], store: LiveStore
) -> list[Entry]:
    target = spec.target
    columns = target.columns  # desired column → PM column
    pm_columns = tuple(columns.values())
    anchored = [r for r in rows if r.get(spec.pm_key) is not None]
    live = (
        await store.entity_rows(target.table, [r[spec.pm_key] for r in anchored], pm_columns)
        if anchored
        else {}
    )

    # A null in an owned column is silence, not an instruction to clear PM's
    # value (CR 5). `retraction: none` says an absent row says nothing; a
    # present row carrying a null must not say more than one that is missing.
    def claimed(row: dict) -> list[str]:
        return [d for d in columns if row.get(d) is not None]

    entries: list[Entry] = []
    for row in rows:
        pm_id = row.get(spec.pm_key)
        if pm_id is None:
            if (spec.entity, row["producer_id"]) not in creating:
                entries.append(_entry(spec, row, "stale", reason="no entity row is created for it"))
                continue
            changes = {columns[d]: (None, row[d]) for d in claimed(row)}
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
        elif found.get("archived_at") is not None:
            entries.append(
                _entry(spec, row, "stale", reason=f"live row archived since the export: {pm_id}")
            )
        else:
            changes = {
                columns[d]: (found.get(columns[d]), row[d])
                for d in claimed(row)
                if found.get(columns[d]) != row[d]
            }
            entries.append(_entry(spec, row, "update" if changes else "noop", changes=changes))
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
    if spec.retraction == "archive":
        raise ApplierError(
            f"{spec.name}: retraction 'archive' is #500's to build — this applier reports only"
        )
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
