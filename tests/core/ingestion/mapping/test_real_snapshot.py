"""The models against the real landed snapshot (#497 step 7).

Fixtures prove the rules; this proves the rules meet the data. The design doc's
measurements seeded these pins; #655 re-measured them after producer fixes and
curator pins moved the store. A divergence is a finding — trace it to a producer
or curation change before re-pinning, never fix a model to make the number come
out.

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
from src.core.ingestion.mapping import (  # noqa: E402
    USA_WA_SOURCES,
    check_contracts,
    held_contracts,
    run_dbt,
)
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
        # An export taken before curation_overlay existed on its target has no
        # overlay file; an empty one is what production holds until #498 writes
        # rows, so the measurements below are the same either way.
        write_parquet([], TABLES["curation_overlay"], overlay)
    pm_orgs = root / PM_EXPORT_DIR / "organizations.parquet"
    if not pm_orgs.exists():
        # An export taken before #592 carries no PM flags: the hint compares nothing.
        write_parquet([], TABLES["organizations"], pm_orgs)
    db = root / "mapping.duckdb"
    result = run_dbt(["build"], snapshot_root=root, duckdb_path=str(db))
    assert result.success, getattr(result, "exception", None)
    statuses = {str(r.status) for r in result.result.results}
    assert statuses <= {"success", "pass", "warn"}, statuses
    # usa-wa#364 filled the blank legislator names, so the build warns on nothing.
    warned = sorted(r.node.name for r in result.result.results if str(r.status) == "warn")
    assert warned == [], warned
    con = duckdb.connect(str(db), read_only=True)
    yield con
    con.close()


def _count(con, sql: str) -> int:
    return con.execute(sql).fetchone()[0]


def test_persons_match_the_measured_store(real):
    anchored = _count(real, "SELECT count(*) FROM desired_people WHERE pm_id IS NOT NULL")
    creates = _count(real, "SELECT count(*) FROM desired_people WHERE pm_id IS NULL")
    names = _count(real, "SELECT count(*) FROM desired_person_names")

    nameless = _count(
        real,
        "SELECT count(*) FROM int_person_identity WHERE name_full IS NULL AND NOT is_tombstone",
    )

    # usa-wa#378 tombstoned 18 duplicates (#501): the 17 creates, all twins of
    # anchored people, and one anchored loser, now resolved `merged`.
    assert anchored == 3117, f"anchored persons: {anchored}"
    assert creates == 0, f"person creates: {creates}"
    # A blank published name would land identity and assert no name, leaving
    # PM's to stand; usa-wa#364 fixed the five the design measured.
    assert nameless == 0, f"nameless producer persons: {nameless}"
    assert names == anchored + creates - nameless
    assert _count(real, "SELECT count(*) FROM desired_person_merges") == 0


def test_organizations_match_the_measured_store(real):
    anchored = _count(real, "SELECT count(*) FROM desired_organizations WHERE pm_id IS NOT NULL")
    creates = _count(real, "SELECT count(*) FROM desired_organizations WHERE pm_id IS NULL")

    # 220: the re-established Joint Select Committee on Civic Health (#591).
    assert anchored == 220, f"anchored orgs: {anchored}"
    assert creates == 0, f"org creates: {creates}"
    assert _count(real, "SELECT count(*) FROM desired_organization_names") == 220
    assert _count(real, "SELECT count(*) FROM desired_organization_acronyms") == 186
    assert _count(real, "SELECT count(*) FROM desired_organization_merges") == 0


def test_parents_are_claimed_for_house_senate_joint_and_chambers(real):
    """101 House + 85 Senate + 19 Joint + 2 chambers = 207; 'Other' and '' claim none.

    The 19th Joint is the re-established Civic Health committee (#591).
    """
    claimed = _count(real, "SELECT count(*) FROM desired_organization_parents")

    assert claimed == 207, f"parent claims: {claimed}"


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

    # 152 produced + the old Civic Health committee's overlay pin, dissolved 2026
    # (#591). Neither Civic Health row has a biennium, so the live count holds.
    assert events == 153, f"dissolved events: {events}"
    assert types == {"dissolved"}
    assert still_live == 34, f"orgs at the current biennium: {still_live}"


def test_every_published_link_resolves_to_an_anchored_pair(real):
    """#592: org_lineage 1.0.0 measured 162 links, each end a live anchor, all dated;
    PM already held every one (the Civic Health year aside)."""
    links = real.execute(
        "SELECT event_type, count(*) FROM desired_organization_lineage GROUP BY 1"
    ).fetchall()
    published = _count(real, "SELECT count(*) FROM stg_usa_wa__org_lineage")
    undated = _count(
        real, "SELECT count(*) FROM desired_organization_lineage WHERE event_year IS NULL"
    )

    assert sum(n for _, n in links) == published, "a published link failed to resolve"
    assert {t for t, _ in links} <= {"succeeded_by", "merged_with", "split_from"}
    assert undated == 0


def test_the_landed_store_matches_the_pins_it_was_landed_under():
    """#553's acceptance: the store as it stands builds, with nothing re-minted.

    Four of the six sources landed before #536 and record no `contract_hash` in
    `snapshot.json`; usa-wa#385's baseline re-mint published it in their
    `datapackage.json`, which is the fallback this exercises. A failure here is
    a re-pin awaiting a pull, not something to fix in the check.
    """
    if missing := _missing():
        pytest.skip(f"real snapshot store incomplete — absent: {', '.join(missing)}")

    assert check_contracts(STORE) == []
    held = held_contracts(STORE)
    assert set(held) == set(USA_WA_SOURCES)
    assert all(held.values()), f"no contract stated anywhere: {held}"
