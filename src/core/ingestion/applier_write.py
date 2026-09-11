"""The writer and the transaction (#499 step 6).

`plan_statements` turns a diff into parameterised SQL in a fixed order —
entity creates, each followed by its `producer_crosswalk` row; then child
rows; then columns — so a created parent exists before a child or a parent
claim names it. A statement names exactly the columns the entry changed, plus
`id`, the parent column and the manifest's insert defaults on an insert;
nothing else is ever in a statement, which is the column-scoping proof. Only
`create`, `insert` and `update` entries produce statements; every other kind
is a report, and a `stale` entry refuses the whole plan.

`apply_diff` runs the plan in one transaction, re-diffs inside it, and rolls
back unless nothing is left to write — nor left for a person: a `conflict` the
writes themselves created blocks the commit too, since the verdict that would
have caught it ran before the write.

**The merge phase (#514).** A diff holding an actionable merge writes the merges
and nothing else: each through its registered primitive (`applier_merge`), then
every crosswalk anchor naming a row the merge retired re-points at its survivor.
The row entries were computed against the pre-merge state and wait for the next
diff. The commit needs every merge acted on to be a `noop` in the re-diff and
nothing in the re-diff that the pre-write diff lacked: a merge may only make
entries go away. The Heck case (#515) is why — a merge and a name update in
one plan destroyed the canonical name in either order, and the ordinary
"nothing left to write" check passed, because the end state satisfied the
desired state.
"""

import json
from collections.abc import Awaitable, Callable, Iterator, Mapping
from dataclasses import dataclass
from typing import Protocol

from src.core.db import generate_id
from src.core.ingestion.applier import (
    ApplierError,
    Diff,
    Entry,
    actionable_merges,
    sql_identifier,
)
from src.core.ingestion.applier_merge import MERGE_PRIMITIVES, MergePrimitive
from src.core.ingestion.applier_report import digest_view
from src.core.ingestion.crosswalk import repoint_anchors
from src.core.ingestion.mapping import Manifest

__all__ = [
    "BLOCKING_KINDS",
    "WRITE_KINDS",
    "ApplyResult",
    "Connection",
    "Statement",
    "VerificationFailed",
    "apply_diff",
    "plan_statements",
]

WRITE_KINDS = ("create", "insert", "update")
# What the in-transaction re-diff refuses to commit over: anything still to
# write, a desired state that drifted, and a `conflict` — which the verdict
# would have blocked on, but the verdict is computed before the write and never
# again, so a conflict the writes themselves created had nobody left to see it.
BLOCKING_KINDS = (*WRITE_KINDS, "stale", "conflict")
_CROSSWALK_SQL = (
    "INSERT INTO producer_crosswalk"
    " (id, source, kind, producer_id, exported_pm_id, pm_id, resolution)"
    " VALUES ($1, $2, $3, $4, $5, $6, $7)"
)


class VerificationFailed(ApplierError):
    """The re-diff inside the transaction still had writes; nothing was committed."""


class Connection(Protocol):
    """The slice of an asyncpg connection the writer uses."""

    async def execute(self, sql: str, *args) -> object: ...

    async def fetch(self, sql: str, *args) -> list: ...

    def transaction(self): ...


@dataclass(frozen=True)
class Statement:
    """One parameterised write, tagged with the entry it comes from."""

    kind: str  # entity | crosswalk | insert | update | column
    table: str
    sql: str
    args: tuple
    entry_id: str


@dataclass(frozen=True)
class ApplyResult:
    written: int
    minted: dict[tuple[str, str], str]
    after: Diff
    merged: int = 0  # merge phase: merges folded through a primitive
    anchors: int = 0  # merge phase: crosswalk anchors re-pointed at a survivor


_ident = sql_identifier


def _generate_ids() -> Iterator[str]:
    while True:
        yield generate_id()


def plan_statements(
    diff: Diff, manifest: Manifest, *, source: str, ids: Iterator[str] | None = None
) -> tuple[list[Statement], dict[tuple[str, str], str]]:
    """The statements for a diff, in execution order, and the ids minted for creates."""
    ids = ids if ids is not None else _generate_ids()
    stale = diff.by_kind("stale")
    if stale:
        raise ApplierError(
            f"{len(stale)} stale entries (first: {stale[0].entry_id}) — nothing is written "
            "beside a stale row; rebuild the desired state first"
        )
    minted: dict[tuple[str, str], str] = {}
    entities: list[Statement] = []
    children: list[Statement] = []
    columns: list[Statement] = []

    for e in diff.by_kind("create"):
        spec = manifest.tables[e.table]
        table = _ident(spec.target.table)
        pm_id = next(ids)
        minted[(spec.entity, e.producer_id)] = pm_id
        entities.append(
            Statement(
                "entity", table, f"INSERT INTO {table} (id) VALUES ($1)", (pm_id,), e.entry_id
            )
        )
        args = (next(ids), source, spec.entity, e.producer_id, pm_id, pm_id, "live")
        entities.append(
            Statement("crosswalk", "producer_crosswalk", _CROSSWALK_SQL, args, e.entry_id)
        )

    def row_of(e) -> str:
        """The entity row an entry targets: live, or minted by this plan."""
        spec = manifest.tables[e.table]
        if e.pm_id is not None:
            return e.pm_id
        key = (spec.entity, e.producer_id)
        if key not in minted:
            raise ApplierError(
                f"{e.entry_id}: names a row that is neither live nor created this run "
                f"({e.producer_id})"
            )
        return minted[key]

    for e in diff.by_kind("insert"):
        target = manifest.tables[e.table].target
        table = _ident(target.table)
        values = {col: new for col, (_, new) in e.changes.items()}
        for col, default in target.insert_defaults.items():
            values.setdefault(col, default)
        ordered = sorted(values)
        names = ["id", _ident(target.parent), *(_ident(c) for c in ordered)]
        placeholders = ", ".join(f"${i}" for i in range(1, len(names) + 1))
        sql = f"INSERT INTO {table} ({', '.join(names)}) VALUES ({placeholders})"
        args = (next(ids), row_of(e), *(values[c] for c in ordered))
        children.append(Statement("insert", table, sql, args, e.entry_id))

    for e in diff.by_kind("update"):
        target = manifest.tables[e.table].target
        table = _ident(target.table)
        cols = sorted(_ident(c) for c in e.changes)
        sets = ", ".join(f"{c} = ${i}" for i, c in enumerate(cols, 1))
        sql = f"UPDATE {table} SET {sets} WHERE id = ${len(cols) + 1}"
        args = tuple(e.changes[c][1] for c in cols)
        if target.shape == "column":
            columns.append(Statement("column", table, sql, (*args, row_of(e)), e.entry_id))
        else:
            if e.row_id is None:
                raise ApplierError(f"{e.entry_id}: an update on a child row needs its row id")
            children.append(Statement("update", table, sql, (*args, e.row_id), e.entry_id))

    return [*entities, *children, *columns], minted


async def apply_diff(
    diff: Diff,
    manifest: Manifest,
    conn: Connection,
    *,
    source: str,
    rediff: Callable[[Mapping[tuple[str, str], str]], Awaitable[Diff]],
    ids: Iterator[str] | None = None,
    merges: Mapping[str, MergePrimitive] = MERGE_PRIMITIVES,
) -> ApplyResult:
    """Write the diff in one transaction; re-diff inside it; commit only if nothing is left.

    ``rediff`` receives the ids this plan minted, so a row created here reads
    as anchored on the second pass rather than as a create the live crosswalk
    now contradicts. A diff holding an actionable merge is written by
    :func:`_apply_merges` instead — merges only.
    """
    if acted := actionable_merges(diff):
        return await _apply_merges(
            diff, acted, manifest, conn, source=source, rediff=rediff, merges=merges
        )
    statements, minted = plan_statements(diff, manifest, source=source, ids=ids)
    async with conn.transaction():
        for st in statements:
            await conn.execute(st.sql, *st.args)
        after = await rediff(minted)
        left = [e for e in after.entries if e.kind in BLOCKING_KINDS]
        if left:
            named = ", ".join(f"{e.entry_id} ({e.kind})" for e in left[:5])
            raise VerificationFailed(
                f"after writing, {len(left)} entries remain — rolled back: {named}"
            )
    return ApplyResult(written=len(statements), minted=minted, after=after)


async def _apply_merges(
    diff: Diff,
    acted: list[Entry],
    manifest: Manifest,
    conn: Connection,
    *,
    source: str,
    rediff: Callable[[Mapping[tuple[str, str], str]], Awaitable[Diff]],
    merges: Mapping[str, MergePrimitive],
) -> ApplyResult:
    """Fold each actionable merge and re-point its anchors, in one verified transaction."""
    stale = diff.by_kind("stale")
    if stale:
        raise ApplierError(
            f"{len(stale)} stale entries (first: {stale[0].entry_id}) — no merge is folded "
            "beside a stale row; rebuild the desired state first"
        )
    actor = f"apply_desired_state ({source} merge tombstone, #514)"
    merged = anchors = 0
    async with conn.transaction():
        for e in acted:
            spec = manifest.tables[e.table]
            primitive = merges[spec.target.primitive]
            survivor = e.changes["survivor_pm_id"][1]
            if not e.effects.get("already_merged"):
                dropped = await primitive.merge(
                    conn, winner_id=survivor, loser_id=e.pm_id, actor_email=actor
                )
                anchors += await repoint_anchors(conn, primitive.dropped_kind, dropped)
                merged += 1
            anchors += await repoint_anchors(conn, spec.entity, [(e.pm_id, survivor)])
        after = await rediff({})
        _verify_merges(diff, acted, after)
    return ApplyResult(written=merged, minted={}, after=after, merged=merged, anchors=anchors)


def _view(entry: Entry) -> str:
    return json.dumps(digest_view(entry), sort_keys=True, default=str)


def _verify_merges(before: Diff, acted: list[Entry], after: Diff) -> None:
    """Raise unless every merge acted on is now a noop and the re-diff holds nothing new."""
    acted_ids = {e.entry_id for e in acted}
    pending = [e for e in after.entries if e.entry_id in acted_ids and e.kind != "noop"]
    known = {_view(e) for e in before.entries if e.kind != "noop" and e.entry_id not in acted_ids}
    appeared = [
        e
        for e in after.entries
        if e.kind != "noop" and e.entry_id not in acted_ids and _view(e) not in known
    ]
    if pending or appeared:
        named = ", ".join(f"{e.entry_id} ({e.kind})" for e in [*pending, *appeared][:5])
        raise VerificationFailed(
            f"after the merges, {len(pending)} merge(s) still pending and {len(appeared)}"
            f" entry(ies) the merges changed or created — rolled back: {named}"
        )
