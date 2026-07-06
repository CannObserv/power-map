"""Integration tests for the WA legislative role importer (#263)."""

import sys

import pytest
import pytest_asyncio

from scripts.seed_roles import main, preview_roles, seed_roles
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


async def _chamber(db, chamber_value: str) -> str:
    oid = generate_id()
    await db.execute("INSERT INTO organizations (id) VALUES ($1)", oid)
    tid = await db.fetchval(
        "SELECT id FROM entity_identifier_types WHERE slug='org_wa_legislature_chamber'"
    )
    await db.execute(
        "INSERT INTO identifiers (id, entity_id, entity_identifier_type_id, value)"
        " VALUES ($1,$2,$3,$4)",
        generate_id(),
        oid,
        tid,
        chamber_value,
    )
    return oid


async def _district(db, slug: str) -> str:
    jid = generate_id()
    tid = await db.fetchval("SELECT id FROM jurisdiction_types WHERE slug='legislative_district'")
    await db.execute(
        "INSERT INTO jurisdictions (id, slug, name, type_id) VALUES ($1,$2,$3,$4)",
        jid,
        slug,
        "Test LD",
        tid,
    )
    return jid


def _role(chamber, role_type, slug, qualifier, title):
    return {
        "chamber": chamber,
        "role_type": role_type,
        "jurisdiction_slug": slug,
        "qualifier": qualifier,
        "title": title,
    }


async def test_seeds_and_is_idempotent(db):
    house = await _chamber(db, "usa_wa_house")
    senate = await _chamber(db, "usa_wa_senate")
    await _district(db, "usa-wa-ld-1")
    roles = [
        _role(
            "usa_wa_senate", "state_senator", "usa-wa-ld-1", None, "Washington State Senator, LD-1"
        ),
        _role(
            "usa_wa_house", "state_representative", "usa-wa-ld-1", "Position 1", "WA Rep LD-1 P1"
        ),
        _role(
            "usa_wa_house", "state_representative", "usa-wa-ld-1", "Position 2", "WA Rep LD-1 P2"
        ),
    ]
    first = await seed_roles(db, roles)
    assert first == {"new": 3, "attached": 0, "rejected": 0}

    # Idempotent: a second run attaches to the existing roles, creates nothing.
    second = await seed_roles(db, roles)
    assert second == {"new": 0, "attached": 3, "rejected": 0}

    # The House Position 1 role is a state_representative with a jurisdiction.
    hp1 = await db.fetchrow(
        "SELECT r.qualifier, r.jurisdiction_id, rt.slug AS role_type"
        " FROM roles r JOIN role_types rt ON rt.id = r.role_type_id"
        " WHERE r.organization_id=$1 AND r.qualifier='Position 1'",
        house,
    )
    assert hp1["role_type"] == "state_representative"
    assert hp1["jurisdiction_id"] is not None

    # The Senate role has a NULL qualifier and exactly one row.
    sen = await db.fetch("SELECT qualifier FROM roles WHERE organization_id=$1", senate)
    assert len(sen) == 1
    assert sen[0]["qualifier"] is None


async def test_rejects_unknown_chamber(db):
    await _district(db, "usa-wa-ld-2")
    roles = [_role("not_a_chamber", "state_senator", "usa-wa-ld-2", None, "X")]
    assert await seed_roles(db, roles) == {"new": 0, "attached": 0, "rejected": 1}


async def test_rejects_unknown_district(db):
    await _chamber(db, "usa_wa_senate")
    roles = [_role("usa_wa_senate", "state_senator", "usa-wa-ld-999", None, "X")]
    assert await seed_roles(db, roles) == {"new": 0, "attached": 0, "rejected": 1}


async def _archived_district(db, slug: str) -> str:
    jid = generate_id()
    tid = await db.fetchval("SELECT id FROM jurisdiction_types WHERE slug='legislative_district'")
    await db.execute(
        "INSERT INTO jurisdictions (id, slug, name, type_id, archived_at)"
        " VALUES ($1,$2,$3,$4, NOW())",
        jid,
        slug,
        "Archived LD",
        tid,
    )
    return jid


async def test_rejects_archived_jurisdiction(db):
    """resolve_role rejects an archived (soft-deleted) district → counted rejected."""
    await _chamber(db, "usa_wa_senate")
    await _archived_district(db, "usa-wa-ld-3")
    roles = [_role("usa_wa_senate", "state_senator", "usa-wa-ld-3", None, "X")]
    assert await seed_roles(db, roles) == {"new": 0, "attached": 0, "rejected": 1}


async def test_preview_reports_new_existing_and_unresolved(db):
    await _chamber(db, "usa_wa_senate")
    await _district(db, "usa-wa-ld-4")
    role = _role(
        "usa_wa_senate", "state_senator", "usa-wa-ld-4", None, "Washington State Senator, LD-4"
    )
    bad = _role("no_such_chamber", "state_senator", "usa-wa-ld-4", None, "X")

    # Before seeding: the good role would be created, the bad one is unresolved.
    assert await preview_roles(db, [role, bad]) == {
        "would_create": 1,
        "exists": 0,
        "unresolved": 1,
    }

    # After seeding: the good role now shows as existing (no writes from preview).
    await seed_roles(db, [role])
    assert await preview_roles(db, [role]) == {"would_create": 0, "exists": 1, "unresolved": 0}


async def test_preview_unknown_role_type_is_unresolved(db):
    """An unknown role_type slug previews as unresolved (matching --execute reject)."""
    await _chamber(db, "usa_wa_senate")
    await _district(db, "usa-wa-ld-5")
    role = _role("usa_wa_senate", "not_an_office", "usa-wa-ld-5", None, "X")
    assert await preview_roles(db, [role]) == {"would_create": 0, "exists": 0, "unresolved": 1}


def test_main_missing_file_exits(monkeypatch):
    """main() reports a friendly SystemExit (not a traceback) for a missing seed file."""
    monkeypatch.setattr(sys, "argv", ["seed_roles", "/no/such/seed.json"])
    with pytest.raises(SystemExit):
        main()
