"""The asyncpg-backed `LiveStore` (#499 step 7).

Every read the engine makes, as SQL: the live crosswalk, entity rows by id,
child rows by parent, a lookup table, the same-value probe behind create
hints, merge tombstones, and a merge primitive's read-only preview (#514).
Table and column names arrive from the manifest and are checked as plain
identifiers before they reach a statement; values are always bound. Reads are
scoped by the ids the engine asks for, which are always in scope.
"""

from collections.abc import Sequence

from src.core.ingestion.applier import sql_identifier
from src.core.ingestion.applier_merge import MERGE_PRIMITIVES

__all__ = ["PostgresLiveStore"]

_CROSSWALK_SQL = (
    "SELECT kind, producer_id, pm_id, resolution FROM producer_crosswalk"
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

    async def value_matches(
        self, table: str, column: str, values: Sequence, parent: str
    ) -> dict[object, list[str]]:
        sql = (
            f"SELECT {sql_identifier(column)} AS value, {sql_identifier(parent)} AS parent"
            f" FROM {sql_identifier(table)} WHERE {sql_identifier(column)} = ANY($1::text[])"
        )
        out: dict[object, list[str]] = {}
        for r in await self._conn.fetch(sql, list(values)):
            out.setdefault(r["value"], []).append(r["parent"])
        return out
