"""The models against the real landed snapshot (#497 step 7).

Fixtures prove the rules; this proves the rules meet the data. It asserts the
measurements the design doc rests on, so a divergence here is a finding for
#501, never something to fix in a model to make the number come out.

Needs the store the nightly puller lands plus the read-only crosswalk export
(`scripts/pull_datasets.py`, `scripts/export_pm_tables.py`); absent either, it
skips by name rather than passing vacuously. It never opens a database.
"""

import shutil
from pathlib import Path

import pytest

pytest.importorskip("dbt.adapters.duckdb")

import duckdb  # noqa: E402

from scripts.export_pm_tables import TABLES  # noqa: E402
from src.core.ingestion.mapping import USA_WA_SOURCES, run_dbt  # noqa: E402
from src.core.ingestion.mapping.parquet import PM_EXPORT_DIR, write_parquet  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[4]
# Path arithmetic that silently pointed elsewhere would skip "store absent"
# forever — a vacuous pass by another route (CR 8).
assert (REPO_ROOT / "pyproject.toml").exists(), f"not the repo root: {REPO_ROOT}"
STORE = REPO_ROOT / "data" / "usa_wa_snapshots"
CROSSWALK = STORE / PM_EXPORT_DIR / "producer_crosswalk.parquet"

pytestmark = pytest.mark.integration


def _missing() -> list[str]:
    absent = [name for name in USA_WA_SOURCES if not (STORE / name).is_dir()]
    if not CROSSWALK.exists():
        absent.append(str(CROSSWALK.relative_to(REPO_ROOT)))
    return absent


@pytest.fixture(scope="module")
def real(tmp_path_factory):
    if missing := _missing():
        pytest.skip(
            f"real snapshot store incomplete — absent: {', '.join(missing)}. "
            "Provision with `python -m scripts.pull_datasets` and "
            "`python -m scripts.export_pm_tables`."
        )
    root = tmp_path_factory.mktemp("real-store")
    shutil.rmtree(root)
    shutil.copytree(STORE, root)
    overlay = root / PM_EXPORT_DIR / "curation_overlay.parquet"
    if not overlay.exists():
        # Until schema.sql reaches production the export cannot write this;
        # an empty overlay is exactly what production holds before #498.
        write_parquet([], TABLES["curation_overlay"], overlay)
    db = root / "mapping.duckdb"
    result = run_dbt(["build"], snapshot_root=root, duckdb_path=str(db))
    assert result.success, getattr(result, "exception", None)
    statuses = {str(r.status) for r in result.result.results}
    assert statuses <= {"success", "pass", "warn"}, statuses
    warned = sorted(r.node.name for r in result.result.results if str(r.status) == "warn")
    assert warned == ["not_null_stg_usa_wa__persons_name_full"], warned
    con = duckdb.connect(str(db), read_only=True)
    yield con
    con.close()


def _count(con, sql: str) -> int:
    return con.execute(sql).fetchone()[0]


def test_persons_reproduce_the_design_measurements(real):
    anchored = _count(real, "SELECT count(*) FROM desired_people WHERE pm_id IS NOT NULL")
    creates = _count(real, "SELECT count(*) FROM desired_people WHERE pm_id IS NULL")
    names = _count(real, "SELECT count(*) FROM desired_person_names")

    nameless = _count(
        real,
        "SELECT count(*) FROM int_person_identity WHERE name_full IS NULL AND NOT is_tombstone",
    )

    assert anchored == 3118, f"anchored persons: {anchored}"
    assert creates == 17, f"person creates: {creates}"
    # Five anchored legislators are published with a blank name (usa-wa defect,
    # recorded on #497): identity lands, no name is asserted, PM's stands.
    assert nameless == 5, f"nameless producer persons: {nameless}"
    assert names == anchored + creates - nameless
    assert _count(real, "SELECT count(*) FROM desired_person_merges") == 0


def test_organizations_reproduce_the_design_measurements(real):
    anchored = _count(real, "SELECT count(*) FROM desired_organizations WHERE pm_id IS NOT NULL")
    creates = _count(real, "SELECT count(*) FROM desired_organizations WHERE pm_id IS NULL")

    assert anchored == 219, f"anchored orgs: {anchored}"
    assert creates == 0, f"org creates: {creates}"
    assert _count(real, "SELECT count(*) FROM desired_organization_names") == 219
    assert _count(real, "SELECT count(*) FROM desired_organization_acronyms") == 186
    assert _count(real, "SELECT count(*) FROM desired_organization_merges") == 0


def test_parents_are_claimed_for_house_senate_joint_and_chambers(real):
    """101 House + 85 Senate + 18 Joint + 2 chambers = 206; 'Other' and '' claim none."""
    claimed = _count(real, "SELECT count(*) FROM desired_organization_parents")

    assert claimed == 206, f"parent claims: {claimed}"


def test_dissolved_events_match_pm_and_the_current_biennium_is_live(real):
    events = _count(real, "SELECT count(*) FROM desired_entity_events")
    types = {
        r[0]
        for r in real.execute("SELECT DISTINCT event_type FROM desired_entity_events").fetchall()
    }
    still_live = _count(
        real,
        """SELECT count(*) FROM int_org_identity i
           WHERE i.last_biennium IS NOT NULL
             AND i.producer_id NOT IN (SELECT producer_id FROM desired_entity_events)""",
    )

    assert events == 152, f"dissolved events: {events}"
    assert types == {"dissolved"}
    assert still_live == 34, f"orgs at the current biennium: {still_live}"
