"""Build the mapping project against a fixture store (#497).

The fixture store under ``fixtures/store/`` mirrors the puller's layout —
``<dataset>/<version>/data.csv`` — and is copied per test so a test can add or
remove a version. PM's two tables are written as Parquet from tuples, because
that is the shape the export step produces and the models read.
"""

import shutil
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path

import pytest

pytest.importorskip("dbt.adapters.duckdb")

import duckdb  # noqa: E402

from scripts.export_pm_tables import TABLES  # noqa: E402
from src.core.ingestion.crosswalk import PRODUCER_SOURCE  # noqa: E402
from src.core.ingestion.mapping import run_dbt  # noqa: E402
from src.core.ingestion.mapping.parquet import PM_EXPORT_DIR, write_parquet  # noqa: E402

FIXTURE_STORE = Path(__file__).parent / "fixtures" / "store"
FIXTURE_VERSION = "v1"
NOW = datetime(2026, 9, 10, tzinfo=UTC)


# The one dbt warning the fixture is built to raise: P7 is published with a
# blank name, as five real legislators are.
EXPECTED_WARNINGS = ["not_null_stg_usa_wa__persons_name_full"]


# Short, readable ids for fixture rows. Real ids are ULIDs; nothing here checks
# the format, only the joins.
def _ids(prefix: str, ns) -> list[str]:
    return [f"01{prefix}{n}000000000000000000000A" for n in ns]


P1, P2, P3, P4, P5, P6, P7 = _ids("P", range(1, 8))
PM1, PM2, PM4, PM5, PM6, PM7 = _ids("M", (1, 2, 4, 5, 6, 7))
O1, O2, O3, O4, O5, O6, O7, O8, O9 = _ids("O", range(1, 10))
MO1, MO2, MO3, MO4, MO5, MO6, MO7, MO8, MO9 = _ids("N", range(1, 10))
# O10 / MO10: a committee whose last biennium is 1999-00 — the century wrap.
O10, MO10 = "01O10000000000000000000000A", "01N10000000000000000000000A"
ORG_ANCHORS = [*zip(_ids("O", range(1, 10)), _ids("N", range(1, 10)), strict=True), (O10, MO10)]


def crosswalk_row(producer_id: str, pm_id: str | None, resolution: str, *, kind: str = "person"):
    """One producer_crosswalk tuple in export column order."""
    return (
        f"xw-{producer_id}",
        PRODUCER_SOURCE,
        kind,
        producer_id,
        pm_id or producer_id,
        pm_id,
        resolution,
        NOW,
        None,
        NOW,
        NOW,
    )


def overlay_row(
    entity_type: str, entity_id: str, field: str, value: str | None, *, archived_at=None
):
    """A `curation_overlay` export row; `archived_at` set is an unpinned pin (#498)."""
    row_id = f"ov-{entity_id}-{field}"
    return (row_id, entity_type, entity_id, field, value, None, None, NOW, NOW, archived_at)


def fixture_csv(
    dataset: str,
    *,
    drop: str | None = None,
    replace: Mapping[str, str] | None = None,
    add: Sequence[str] = (),
) -> str:
    """The fixture's CSV for ``dataset``, edited: lines containing ``drop`` removed,
    each ``replace`` key substituted, ``add`` lines appended. For ``build(datasets=…)``."""
    text = (FIXTURE_STORE / dataset / FIXTURE_VERSION / "data.csv").read_text()
    lines = [ln for ln in text.splitlines() if drop is None or drop not in ln]
    text = "\n".join([*lines, *add]) + "\n"
    for needle, replacement in (replace or {}).items():
        assert needle in text, f"{needle!r} is not in the {dataset} fixture"
        text = text.replace(needle, replacement)
    return text


DEFAULT_CROSSWALK = [
    crosswalk_row(P1, PM1, "live"),
    crosswalk_row(P2, PM2, "merged"),  # PM merged it; pm_id already points at the survivor
    crosswalk_row(P4, PM4, "live"),  # a usa-wa tombstone whose PM row still exists
    crosswalk_row(P5, PM5, "archived"),  # in the table, not in scope
    crosswalk_row(P6, PM6, "live"),  # two hops from its survivor
    crosswalk_row(P7, PM7, "live"),  # published with a blank name (real: 5 such rows)
    # P3 has no row: an unanchored producer person → a create
    *(crosswalk_row(o, m, "live", kind="organization") for o, m in ORG_ANCHORS),
]
DEFAULT_OVERLAY = [
    overlay_row("person", PM1, "name", "Curated One"),
    # PM parents the Senate committee elsewhere; the overlay must win over the
    # mapped Senate chamber.
    overlay_row("organization", MO5, "parent_id", MO1),
]


class Built:
    """A built project: query any model by name."""

    def __init__(self, duckdb_path: Path, result):
        self.duckdb_path = duckdb_path
        self.result = result

    def rows(self, model: str, *, order_by: str = "1") -> list[tuple]:
        con = duckdb.connect(str(self.duckdb_path), read_only=True)
        try:
            con.execute("SET TimeZone = 'UTC'")
            return con.execute(f"SELECT * FROM {model} ORDER BY {order_by}").fetchall()
        finally:
            con.close()

    @property
    def statuses(self) -> set[str]:
        return {str(r.status) for r in self.result.result.results}

    @property
    def warnings(self) -> list[str]:
        """Names of dbt tests that warned — a warning is by design, but only the ones we name."""
        return sorted(r.node.name for r in self.result.result.results if str(r.status) == "warn")

    @property
    def failures(self) -> list[str]:
        """Names of nodes that failed or errored (a build asked to tolerate failure)."""
        return sorted(
            r.node.name for r in self.result.result.results if str(r.status) in ("fail", "error")
        )

    def columns(self, model: str) -> list[str]:
        con = duckdb.connect(str(self.duckdb_path), read_only=True)
        try:
            return [c[0] for c in con.execute(f"DESCRIBE {model}").fetchall()]
        finally:
            con.close()


@pytest.fixture(scope="session")
def _build_cache(tmp_path_factory):
    """One build per distinct (crosswalk, overlay, select) for the whole session.

    A full `dbt build` is ~2.5s and most tests build the identical default
    project; unshared, the tier took the unit gate from 12s to 93s (CR 6).
    Sharing is safe because tests only ever open the built file read-only.
    """
    cache: dict[tuple, Built] = {}

    def _build(
        *,
        crosswalk: Sequence[tuple] = DEFAULT_CROSSWALK,
        overlay: Sequence[tuple] = DEFAULT_OVERLAY,
        datasets: Mapping[str, str] | None = None,
        select: str | None = None,
        must_succeed: bool = True,
    ) -> Built:
        """``datasets`` replaces a usa-wa dataset's CSV (see `fixture_csv`); ``must_succeed=False``
        returns a failed build for a test that asserts *what* failed."""
        edits = tuple(sorted((datasets or {}).items()))
        key = (tuple(crosswalk), tuple(overlay), edits, select, must_succeed)
        if key in cache:
            return cache[key]
        root = tmp_path_factory.mktemp("mapping") / "store"
        shutil.copytree(FIXTURE_STORE, root)
        for dataset, text in edits:
            (root / dataset / FIXTURE_VERSION / "data.csv").write_text(text)
        pm = root / PM_EXPORT_DIR
        write_parquet(crosswalk, TABLES["producer_crosswalk"], pm / "producer_crosswalk.parquet")
        write_parquet(overlay, TABLES["curation_overlay"], pm / "curation_overlay.parquet")
        db = root.parent / "mapping.duckdb"
        args = ["build"] + (["--select", select] if select else [])
        result = run_dbt(args, snapshot_root=root, duckdb_path=str(db))
        if must_succeed:
            assert result.success, getattr(result, "exception", None) or _failures(result)
        else:
            assert result.result is not None, getattr(result, "exception", None)
        # A selector naming no model is a successful no-op to dbt; here it is
        # a test that would go on to assert against nothing.
        assert result.result.results, f"nothing built — does {select!r} name a model?"
        cache[key] = Built(db, result)
        return cache[key]

    return _build


@pytest.fixture
def build(_build_cache):
    """Build (or reuse) the fixture project; see `_build_cache`."""
    return _build_cache


def _failures(result) -> str:
    try:
        return "; ".join(
            f"{r.node.name}: {r.status} {r.message}"
            for r in result.result.results
            if str(r.status) not in ("success", "pass")
        )
    except AttributeError:
        return repr(result)
