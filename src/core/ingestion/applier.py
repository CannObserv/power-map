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
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from src.core.ingestion.mapping import BUILD_INFO, Manifest
from src.core.ingestion.mapping.manifest import TableSpec
from src.core.ingestion.mapping.parquet import read_records

__all__ = [
    "ENTRY_KINDS",
    "IN_SCOPE",
    "ApplierError",
    "CrosswalkRow",
    "DesiredState",
    "Entry",
    "LiveStore",
    "Scope",
    "entry_id",
    "scope_rows",
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
# merge    a producer tombstone's re-point instruction — report-only (#514)
ENTRY_KINDS = ("noop", "create", "insert", "update", "retract", "stale", "conflict", "merge")


class ApplierError(RuntimeError):
    """The applier cannot proceed; the message says why and what to do."""


class LiveStore(Protocol):
    """What the engine reads from the database — and nothing else."""

    async def crosswalk(self, source: str, kinds: Sequence[str]) -> list[dict]: ...

    async def entity_rows(
        self, table: str, ids: Sequence[str], columns: Sequence[str]
    ) -> dict[str, dict]: ...

    async def child_rows(
        self, table: str, parent: str, parent_ids: Sequence[str], columns: Sequence[str]
    ) -> dict[str, list[dict]]: ...

    async def lookup(self, table: str, from_col: str, to_col: str) -> dict[str, str]: ...


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
    state must be rebuilt before anything is applied. Merge tables are report
    entries whatever the crosswalk says, so they pass through untouched.
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
