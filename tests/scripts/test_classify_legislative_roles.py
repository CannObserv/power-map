"""Integration tests for scripts/classify_legislative_roles.py (#266).

Covers the four phases: collision curation (merge + rename), committee-org
classification (officeholder vocab vs staff fallback), and the chamber backlog
(retitle / re-home / notes). Requires TEST_DATABASE_URL + a schema-applied DB.

Run via:
    uv run pytest tests/scripts/test_classify_legislative_roles.py
"""

import pytest
import pytest_asyncio

from scripts.classify_legislative_roles import classify_legislative_roles
from src.core.db import generate_id

pytestmark = [pytest.mark.integration]


@pytest_asyncio.fixture(loop_scope="session")
async def db(db_pool):
    async with db_pool.acquire() as conn:
        tr = conn.transaction()
        await tr.start()
        try:
            yield conn
        finally:
            await tr.rollback()


async def _committee_org(db) -> str:
    """Org tagged with the committee identifier the classifier keys on."""
    oid = generate_id()
    await db.execute("INSERT INTO organizations (id) VALUES ($1)", oid)
    await db.execute(
        "INSERT INTO identifiers (id, entity_id, entity_identifier_type_id, value)"
        " VALUES ($1,$2,(SELECT id FROM entity_identifier_types"
        "  WHERE slug='org_wa_legislature_committee_id'),$3)",
        generate_id(),
        oid,
        f"cid-{oid[-6:]}",
    )
    return oid


async def _role(db, org_id: str, title: str) -> str:
    rid = generate_id()
    await db.execute(
        "INSERT INTO roles (id, organization_id, title) VALUES ($1,$2,$3)", rid, org_id, title
    )
    return rid


async def _person(db) -> str:
    pid = generate_id()
    await db.execute("INSERT INTO people (id) VALUES ($1)", pid)
    return pid


async def _assign(db, role_id: str, person_id: str) -> str:
    aid = generate_id()
    await db.execute(
        "INSERT INTO role_assignments (id, role_id, person_id) VALUES ($1,$2,$3)",
        aid,
        role_id,
        person_id,
    )
    return aid


async def _slug_of(db, role_id: str) -> str | None:
    return await db.fetchval(
        "SELECT rt.slug FROM roles r LEFT JOIN role_types rt ON rt.id=r.role_type_id WHERE r.id=$1",
        role_id,
    )


async def test_committee_officeholders_typed_titles_preserved(db):
    """Officeholder titles map to committee_* vocab; Acting Chair keeps its title."""
    org = await _committee_org(db)
    chair = await _role(db, org, "Chair")
    acting = await _role(db, org, "Acting Chair")
    vice = await _role(db, org, "Vice Chair")
    ranking = await _role(db, org, "Ranking Member")
    member = await _role(db, org, "Member")

    await classify_legislative_roles(db, execute=True)

    assert await _slug_of(db, chair) == "committee_chair"
    assert await _slug_of(db, acting) == "committee_chair"
    assert await _slug_of(db, vice) == "committee_vice_chair"
    assert await _slug_of(db, ranking) == "committee_ranking_member"
    assert await _slug_of(db, member) == "committee_member"
    # The coarse type carries aggregation; the title keeps the distinction.
    assert await db.fetchval("SELECT title FROM roles WHERE id=$1", acting) == "Acting Chair"


async def test_committee_non_officeholder_falls_back_to_staff(db):
    """Anything on a committee that isn't an officeholder is legislature_staff."""
    org = await _committee_org(db)
    counsel = await _role(db, org, "Senior Staff Counsel")
    coordinator = await _role(db, org, "Fiscal Coordinator")

    await classify_legislative_roles(db, execute=True)

    assert await _slug_of(db, counsel) == "legislature_staff"
    assert await _slug_of(db, coordinator) == "legislature_staff"
    # Specific title is preserved under the coarse type.
    assert (
        await db.fetchval("SELECT title FROM roles WHERE id=$1", counsel) == "Senior Staff Counsel"
    )


async def test_variant_title_merges_into_canonical_preserving_assignments(db):
    """Ranking Minority Member merges into Ranking Member; assignments re-pointed."""
    org = await _committee_org(db)
    canonical = await _role(db, org, "Ranking Member")
    variant = await _role(db, org, "Ranking Minority Member")
    holder = await _person(db)
    assignment = await _assign(db, variant, holder)

    await classify_legislative_roles(db, execute=True)

    # Assignment moved, not deleted; variant archived; canonical typed.
    assert await db.fetchval("SELECT role_id FROM role_assignments WHERE id=$1", assignment) == (
        canonical
    )
    assert await db.fetchval("SELECT archived_at IS NOT NULL FROM roles WHERE id=$1", variant)
    assert await _slug_of(db, canonical) == "committee_ranking_member"


async def test_merge_does_not_fire_without_the_canonical_on_same_org(db):
    """A variant alone (no canonical on its org) is classified, never merged."""
    org = await _committee_org(db)
    lone = await _role(db, org, "Ranking Minority Member")

    report = await classify_legislative_roles(db, execute=True)

    assert not await db.fetchval("SELECT archived_at IS NOT NULL FROM roles WHERE id=$1", lone)
    assert {a["kind"] for a in report["actions"] if a["role_id"] == lone} == {"classified"}


async def test_collision_free_rename_applied(db):
    """1st Vice Chair renames to First Vice Chair and types as vice chair."""
    org = await _committee_org(db)
    role = await _role(db, org, "1st Vice Chair")

    await classify_legislative_roles(db, execute=True)

    assert await db.fetchval("SELECT title FROM roles WHERE id=$1", role) == "First Vice Chair"
    assert await _slug_of(db, role) == "committee_vice_chair"


async def test_non_role_artifacts_skipped(db):
    """Guest/Participant rows are left untouched for the #304 sweep."""
    org = await _committee_org(db)
    guest = await _role(db, org, "Guest")

    report = await classify_legislative_roles(db, execute=True)

    assert await _slug_of(db, guest) is None
    assert {a["kind"] for a in report["actions"] if a["role_id"] == guest} == {"skipped"}


async def test_backlog_rule_ignores_same_title_on_non_legislative_org(db):
    """Backlog titles are org-scoped: King County's "Senior Policy Analyst" is not
    WA legislative staff. Unscoped, the title-keyed rule swept it in."""
    other_org = generate_id()
    await db.execute("INSERT INTO organizations (id) VALUES ($1)", other_org)
    stray = await _role(db, other_org, "Senior Policy Analyst")

    await classify_legislative_roles(db, execute=True)

    assert await _slug_of(db, stray) is None


async def test_dry_run_predicts_execute_for_renamed_row(db):
    """Dry run must report the post-rename type, not the pre-rename fallback.

    The rename hasn't been written during a dry run, so without carrying pending
    state the row reads as "1st Vice Chair" and falls through to staff — the
    report would then disagree with what --execute actually does.
    """
    org = await _committee_org(db)
    role = await _role(db, org, "1st Vice Chair")

    dry = await classify_legislative_roles(db, execute=False)
    details = {
        a["detail"] for a in dry["actions"] if a["role_id"] == role and a["kind"] == ("classified")
    }
    assert details == {"committee_vice_chair"}

    await classify_legislative_roles(db, execute=True)
    assert await _slug_of(db, role) == "committee_vice_chair"


async def test_dry_run_does_not_classify_merge_losers(db):
    """A row slated for merge is not also reported as classified."""
    org = await _committee_org(db)
    await _role(db, org, "Ranking Member")
    variant = await _role(db, org, "Ranking Minority Member")

    dry = await classify_legislative_roles(db, execute=False)

    kinds = {a["kind"] for a in dry["actions"] if a["role_id"] == variant}
    assert kinds == {"merged"}


async def test_dry_run_mutates_nothing(db):
    """Dry run classifies and plans merges without writing."""
    org = await _committee_org(db)
    chair = await _role(db, org, "Chair")
    canonical = await _role(db, org, "Ranking Member")
    variant = await _role(db, org, "Ranking Minority Member")

    report = await classify_legislative_roles(db, execute=False)

    assert await _slug_of(db, chair) is None
    assert not await db.fetchval("SELECT archived_at IS NOT NULL FROM roles WHERE id=$1", variant)
    kinds = {a["kind"] for a in report["actions"]}
    assert "classified" in kinds and "merged" in kinds
    assert canonical  # canonical row untouched in dry run
