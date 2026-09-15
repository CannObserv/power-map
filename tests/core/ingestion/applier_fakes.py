"""A dict-backed `LiveStore` for the applier's unit tier (#499).

The engine's matching lives in Python, so a fake that serves rows from dicts
is faithful by construction; what it cannot prove — triggers, cycles, the
outbox — the integration tier proves on a real database. It also records every
request, which is how "a row outside the crosswalk is never read" is asserted.
"""

from collections.abc import Iterable, Mapping, Sequence

import yaml

from src.core.ingestion.crosswalk import PRODUCER_SOURCE
from src.core.ingestion.mapping.manifest import MANIFEST_PATH, parse_manifest

# Seeded vocabularies the real database always holds; a fake without them would
# make the engine's lookup read look like a defect in every org test.
DEFAULT_LOOKUPS: dict[tuple[str, str, str], dict[str, str]] = {
    ("entity_event_types", "slug", "id"): {"dissolved": "EVT_DISSOLVED", "founded": "EVT_FOUNDED"},
}


class FakeLiveStore:
    def __init__(
        self,
        *,
        crosswalk: Iterable[Mapping] = (),
        tables: Mapping[str, Iterable[Mapping]] | None = None,
        lookups: Mapping[tuple[str, str, str], Mapping[str, str]] | None = None,
        previews: Mapping[tuple[str, str], dict] | None = None,
    ):
        self._crosswalk = [dict(r) for r in crosswalk]
        # (loser, survivor) → what `merge_preview` answers; the real preview is the
        # db tier's to prove (tests/core/test_person_merge.py).
        self._previews = dict(previews or {})
        self.tables: dict[str, list[dict]] = {
            name: [dict(r) for r in rows] for name, rows in (tables or {}).items()
        }
        self._lookups = {k: dict(v) for k, v in {**DEFAULT_LOOKUPS, **(lookups or {})}.items()}
        self.requested: list[tuple] = []

    async def crosswalk(self, source: str, kinds: Sequence[str]) -> list[dict]:
        self.requested.append(("crosswalk", source, tuple(kinds)))
        return [
            r
            for r in self._crosswalk
            if r.get("source", PRODUCER_SOURCE) == source and r["kind"] in kinds
        ]

    async def entity_rows(
        self, table: str, ids: Sequence[str], columns: Sequence[str]
    ) -> dict[str, dict]:
        self.requested.append(("entity_rows", table, tuple(ids), tuple(columns)))
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

    async def tombstones(self, entity_type: str, ids: Sequence[str]) -> dict[str, str | None]:
        """``deleted_entities`` rows for ``ids``: entity id → merged_into (None = no survivor)."""
        self.requested.append(("tombstones", entity_type, tuple(ids)))
        wanted = set(ids)
        return {
            r["entity_id"]: r.get("merged_into")
            for r in self.tables.get("deleted_entities", [])
            if r["entity_type"] == entity_type and r["entity_id"] in wanted
        }

    async def merge_preview(self, primitive: str, loser_id: str, survivor_id: str) -> dict:
        self.requested.append(("merge_preview", primitive, loser_id, survivor_id))
        return dict(self._previews.get((loser_id, survivor_id), {}))

    async def slot_holders(
        self, table: str, columns: Sequence[str], tuples: Sequence[tuple]
    ) -> dict[tuple, list[dict]]:
        """Rows holding each tuple of ``columns`` — NULLs equal, like the index."""
        self.requested.append(("slot_holders", table, tuple(columns), tuple(tuples)))
        wanted = set(tuples)
        out: dict[tuple, list[dict]] = {}
        for r in self.tables.get(table, []):
            key = tuple(r.get(c) for c in columns)
            if key in wanted:
                out.setdefault(key, []).append({"id": r["id"], "archived_at": r.get("archived_at")})
        return out

    async def cascade_counts(
        self, cascades: Mapping[str, Sequence[str]], ids: Sequence[str]
    ) -> dict[str, dict[str, int]]:
        """Per id, the unarchived rows of each cascade table naming it in any column."""
        self.requested.append(("cascade_counts", tuple(cascades), tuple(ids)))
        wanted = set(ids)
        out: dict[str, dict[str, int]] = {}
        for table, columns in cascades.items():
            for r in self.tables.get(table, []):
                if r.get("archived_at") is not None:
                    continue
                for pm_id in {r.get(c) for c in columns} & wanted:
                    counts = out.setdefault(pm_id, {})
                    counts[table] = counts.get(table, 0) + 1
        return out

    async def value_matches(
        self, table: str, column: str, values: Sequence, parent: str
    ) -> dict[object, list[str]]:
        """Parents in ``table`` whose ``column`` carries each value — the create hint."""
        self.requested.append(("value_matches", table, column, tuple(values)))
        wanted = set(values)
        out: dict[object, list[str]] = {}
        for r in self.tables.get(table, []):
            if r.get(column) in wanted:
                out.setdefault(r[column], []).append(r[parent])
        return out


class FakeConn:
    """Records every statement and the transaction outcome; executes nothing."""

    def __init__(self):
        self.statements: list[tuple[str, tuple]] = []
        self.events: list[str] = []

    async def execute(self, sql: str, *args) -> None:
        self.statements.append((sql, args))

    async def fetch(self, sql: str, *args) -> list:
        """A statement with RETURNING (the crosswalk re-point, #514); matches nothing."""
        self.statements.append((sql, args))
        return []

    def transaction(self):
        return _FakeTransaction(self)


class _FakeTransaction:
    def __init__(self, conn: FakeConn):
        self._conn = conn

    async def __aenter__(self):
        self._conn.events.append("begin")
        return self

    async def __aexit__(self, exc_type, exc, tb):
        self._conn.events.append("rollback" if exc_type else "commit")
        return False


# The desired-state tables' shapes, as the marts materialise them — for tests
# that need a directory the applier can load (`write_desired`).
DESIRED_SPECS: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    "desired_people": (("pm_id", "producer_id"), ("TEXT", "TEXT")),
    "desired_person_names": (("pm_id", "producer_id", "name", "name_type"), ("TEXT",) * 4),
    "desired_person_merges": (
        ("loser_pm_id", "survivor_pm_id", "loser_producer_id", "survivor_producer_id"),
        ("TEXT",) * 4,
    ),
    "desired_organizations": (("pm_id", "producer_id"), ("TEXT", "TEXT")),
    "desired_organization_parents": (("pm_id", "parent_pm_id", "producer_id"), ("TEXT",) * 3),
    "desired_organization_names": (("pm_id", "producer_id", "name", "name_type"), ("TEXT",) * 4),
    "desired_organization_acronyms": (("pm_id", "producer_id", "acronym"), ("TEXT",) * 3),
    "desired_organization_merges": (
        ("loser_pm_id", "survivor_pm_id", "loser_producer_id", "survivor_producer_id"),
        ("TEXT",) * 4,
    ),
    "desired_entity_events": (
        ("pm_id", "producer_id", "entity_type", "event_type", "event_year"),
        ("TEXT", "TEXT", "TEXT", "TEXT", "INTEGER"),
    ),
}


def write_desired(directory, **rows_by_table) -> None:
    """Write every desired-state table under ``directory`` (empty unless given rows as dicts)."""
    from src.core.ingestion.mapping.parquet import TableSpec, write_parquet

    for table, (columns, types) in DESIRED_SPECS.items():
        rows = [tuple(r.get(c) for c in columns) for r in rows_by_table.get(table, [])]
        write_parquet(rows, TableSpec(table, columns, types), directory / f"{table}.parquet")


# #527: the two assignment bindings the design names, as the manifest will carry
# them. Tests parse them beside production's tables until step 8 adopts them;
# production's own definition wins once it exists.
ASSIGNMENT_TABLES: dict[str, dict] = {
    "desired_role_assignments": {
        "entity": "assignment",
        "key": ["producer_id"],
        "pm_key": "pm_id",
        "retraction": "archive",
        "owned_columns": [],
        "target": {
            "shape": "entity",
            "table": "role_assignments",
            "identity": {
                "person_producer_id": {"column": "person_id", "entity": "person"},
                "role_producer_id": {"column": "role_id", "entity": "role"},
                "start_date": "start_date",
            },
            "unique_live": ["person_id", "role_id", "start_date"],
            "supersession": ["person_id", "role_id"],
            "cascades": {
                "role_assignment_relationships": ["from_assignment_id", "to_assignment_id"]
            },
        },
    },
    "desired_role_assignment_dates": {
        "entity": "assignment",
        "key": ["producer_id"],
        "pm_key": "pm_id",
        "retraction": "none",
        "owned_columns": ["start_date", "end_date", "is_current"],
        "overlay": {"start_date": "start_date", "end_date": "end_date", "is_current": "is_current"},
        "target": {
            "shape": "column",
            "table": "role_assignments",
            "columns": {
                "start_date": "start_date",
                "end_date": "end_date",
                "is_current": "is_current",
            },
            "asserts_null": ["end_date"],
        },
    },
}


def raw_with_assignments() -> dict:
    """Production's manifest document plus the assignment bindings (deep copies)."""
    with MANIFEST_PATH.open() as f:
        raw = yaml.safe_load(f)
    for name, spec in yaml.safe_load(yaml.safe_dump(ASSIGNMENT_TABLES)).items():
        raw["tables"].setdefault(name, spec)
    return raw


def manifest_with_assignments():
    """The manifest the assignment tests diff against."""
    return parse_manifest(raw_with_assignments())
