"""A dict-backed `LiveStore` for the applier's unit tier (#499).

The engine's matching lives in Python, so a fake that serves rows from dicts
is faithful by construction; what it cannot prove — triggers, cycles, the
outbox — the integration tier proves on a real database. It also records every
request, which is how "a row outside the crosswalk is never read" is asserted.
"""

from collections.abc import Iterable, Mapping, Sequence


class FakeLiveStore:
    def __init__(
        self,
        *,
        crosswalk: Iterable[Mapping] = (),
        tables: Mapping[str, Iterable[Mapping]] | None = None,
        lookups: Mapping[tuple[str, str, str], Mapping[str, str]] | None = None,
    ):
        self._crosswalk = [dict(r) for r in crosswalk]
        self.tables: dict[str, list[dict]] = {
            name: [dict(r) for r in rows] for name, rows in (tables or {}).items()
        }
        self._lookups = {k: dict(v) for k, v in (lookups or {}).items()}
        self.requested: list[tuple] = []

    async def crosswalk(self, source: str, kinds: Sequence[str]) -> list[dict]:
        self.requested.append(("crosswalk", source, tuple(kinds)))
        return [
            r for r in self._crosswalk if r.get("source", "usa-wa") == source and r["kind"] in kinds
        ]

    async def entity_rows(
        self, table: str, ids: Sequence[str], columns: Sequence[str]
    ) -> dict[str, dict]:
        self.requested.append(("entity_rows", table, tuple(ids)))
        wanted = set(ids)
        return {
            r["id"]: {c: r.get(c) for c in ("id", "archived_at", *columns)}
            for r in self.tables.get(table, [])
            if r["id"] in wanted
        }

    async def child_rows(
        self, table: str, parent: str, parent_ids: Sequence[str], columns: Sequence[str]
    ) -> dict[str, list[dict]]:
        self.requested.append(("child_rows", table, tuple(parent_ids)))
        wanted = set(parent_ids)
        out: dict[str, list[dict]] = {}
        for r in self.tables.get(table, []):
            if r.get(parent) in wanted:
                out.setdefault(r[parent], []).append(dict(r))
        return out

    async def lookup(self, table: str, from_col: str, to_col: str) -> dict[str, str]:
        self.requested.append(("lookup", table, (from_col, to_col)))
        return dict(self._lookups[(table, from_col, to_col)])
