"""The asyncpg-backed `LiveStore` (#499 step 7).

Every read the engine makes, as SQL: the live crosswalk, entity rows by id,
child rows by parent, a lookup table, the same-value probe behind create
hints, merge tombstones, and a merge primitive's read-only preview (#514).
Table and column names arrive from the manifest and are checked as plain
identifiers before they reach a statement; values are always bound. Reads are
scoped by the ids the engine asks for, which are always in scope.
"""

from collections.abc import Mapping, Sequence

from src.core.ingestion.applier import sql_identifier
from src.core.ingestion.applier_merge import MERGE_PRIMITIVES
from src.core.ingestion.mapping.manifest import Index

__all__ = ["PostgresLiveStore"]

_CROSSWALK_SQL = (
    "SELECT kind, producer_id, pm_id, resolution, retracted_at FROM producer_crosswalk"
    " WHERE source = $1 AND kind = ANY($2::text[])"
)


def _columns(names: Sequence[str]) -> str:
    seen: list[str] = []
    for name in names:
        if name not in seen:
            seen.append(sql_identifier(name))
    return ", ".join(seen)


class PostgresLiveStore:
    def __init__(self, conn):
        self._conn = conn

    async def crosswalk(self, source: str, kinds: Sequence[str]) -> list[dict]:
        rows = await self._conn.fetch(_CROSSWALK_SQL, source, list(kinds))
        return [dict(r) for r in rows]

    async def entity_rows(
        self, table: str, ids: Sequence[str], columns: Sequence[str]
    ) -> dict[str, dict]:
        sql = (
            f"SELECT {_columns(('id', 'archived_at', *columns))} FROM {sql_identifier(table)}"
            " WHERE id = ANY($1::text[])"
        )
        rows = await self._conn.fetch(sql, list(ids))
        return {r["id"]: dict(r) for r in rows}

    async def child_rows(
        self, table: str, parent: str, parent_ids: Sequence[str], columns: Sequence[str]
    ) -> dict[str, list[dict]]:
        parent = sql_identifier(parent)
        sql = (
            f"SELECT {_columns(('id', parent, *columns))} FROM {sql_identifier(table)}"
            f" WHERE {parent} = ANY($1::text[])"
        )
        out: dict[str, list[dict]] = {}
        for r in await self._conn.fetch(sql, list(parent_ids)):
            out.setdefault(r[parent], []).append(dict(r))
        return out

    async def lookup(self, table: str, from_col: str, to_col: str) -> dict[str, str]:
        sql = (
            f"SELECT {sql_identifier(from_col)} AS k, {sql_identifier(to_col)} AS v"
            f" FROM {sql_identifier(table)}"
        )
        return {r["k"]: r["v"] for r in await self._conn.fetch(sql)}

    async def tombstones(self, entity_type: str, ids: Sequence[str]) -> dict[str, str | None]:
        rows = await self._conn.fetch(
            "SELECT entity_id, merged_into FROM deleted_entities"
            " WHERE entity_type = $1 AND entity_id = ANY($2::text[])",
            entity_type,
            list(ids),
        )
        return {r["entity_id"]: r["merged_into"] for r in rows}

    async def merge_preview(self, primitive: str, loser_id: str, survivor_id: str) -> dict:
        preview = MERGE_PRIMITIVES[primitive].preview
        return await preview(self._conn, winner_id=survivor_id, loser_id=loser_id)

    async def value_rows(self, table: str, column: str, parent: str) -> list[tuple[str, str]]:
        """Every value, whatever its visibility: a twin may hold only a non-public name,
        and the hint names the parent, never this text (#533)."""
        # visibility-allowlist (issue #121): the create hint matches every name, so a
        # twin under a deadname is not minted as a new public person — docs/NAMES.md.
        sql = (
            f"SELECT {sql_identifier(column)} AS value, {sql_identifier(parent)} AS parent"
            f" FROM {sql_identifier(table)} WHERE {sql_identifier(column)} IS NOT NULL"
        )
        return [(r["value"], r["parent"]) for r in await self._conn.fetch(sql)]

    async def slot_holders(
        self, table: str, index: Index, tuples: Sequence[tuple]
    ) -> dict[tuple, list[dict]]:
        """One probe per tuple, ``IS NOT DISTINCT FROM`` per column — the index's own
        NULL rule — so each column's type is Postgres's to infer, never ours. A
        folded column compares as the index stores it, and the index's own
        predicate is applied, so a row it does not cover is no holder (#529)."""
        terms = []
        for i, col in enumerate(index.columns, 1):
            name = sql_identifier(col)
            terms.append(
                f"lower({name}) IS NOT DISTINCT FROM lower(${i}::text)"
                if index.fold.get(col) == "lower"
                else f"{name} IS NOT DISTINCT FROM ${i}"
            )
        terms += [
            f"{sql_identifier(col)} IS {'NULL' if state == 'null' else 'NOT NULL'}"
            for col, state in index.when.items()
        ]
        where = " AND ".join(terms)
        sql = f"SELECT id, archived_at FROM {sql_identifier(table)} WHERE {where} ORDER BY id"
        out: dict[tuple, list[dict]] = {}
        for key in tuples:
            rows = await self._conn.fetch(sql, *key)
            if rows:
                out[tuple(key)] = [dict(r) for r in rows]
        return out

    async def dependent_ids(
        self, dependents: Mapping[str, Sequence[str]], ids: Sequence[str]
    ) -> dict[str, dict[str, list[str]]]:
        """Per id, the live rows of each dependent table naming it (#529) — the ids
        themselves, since the guard subtracts the ones this plan archives."""
        out: dict[str, dict[str, list[str]]] = {}
        for table, columns in dependents.items():
            match = " OR ".join(f"t.{sql_identifier(c)} = x.id" for c in columns)
            sql = (
                "SELECT x.id, t.id AS dependent FROM unnest($1::text[]) AS x(id)"
                f" JOIN {sql_identifier(table)} t ON t.archived_at IS NULL AND ({match})"
                " ORDER BY t.id"
            )
            for r in await self._conn.fetch(sql, list(ids)):
                out.setdefault(r["id"], {}).setdefault(table, []).append(r["dependent"])
        return out

    async def cascade_counts(
        self, cascades: Mapping[str, Sequence[str]], ids: Sequence[str]
    ) -> dict[str, dict[str, int]]:
        out: dict[str, dict[str, int]] = {}
        for table, columns in cascades.items():
            names = [sql_identifier(c) for c in columns]
            match = " OR ".join(f"t.{c} = x.id" for c in names)
            sql = (
                f"SELECT x.id, count(t.*) AS n FROM unnest($1::text[]) AS x(id)"
                f" JOIN {sql_identifier(table)} t ON t.archived_at IS NULL AND ({match})"
                " GROUP BY x.id"
            )
            for r in await self._conn.fetch(sql, list(ids)):
                out.setdefault(r["id"], {})[table] = r["n"]
        return out
