"""Integration tests for the WA legislative seat importer (#263)."""

import pytest
import pytest_asyncio

from scripts.seed_role_seats import seed_seats
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


def _seat(chamber, role_type, slug, qualifier, title):
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
    seats = [
        _seat(
            "usa_wa_senate", "state_senator", "usa-wa-ld-1", None, "Washington State Senator, LD-1"
        ),
        _seat(
            "usa_wa_house", "state_representative", "usa-wa-ld-1", "Position 1", "WA Rep LD-1 P1"
        ),
        _seat(
            "usa_wa_house", "state_representative", "usa-wa-ld-1", "Position 2", "WA Rep LD-1 P2"
        ),
    ]
    first = await seed_seats(db, seats)
    assert first == {"new": 3, "attached": 0, "rejected": 0}

    # Idempotent: a second run attaches to the existing seats, creates nothing.
    second = await seed_seats(db, seats)
    assert second == {"new": 0, "attached": 3, "rejected": 0}

    # House Position 1 seat is a districted state_representative.
    hp1 = await db.fetchrow(
        "SELECT r.qualifier, r.jurisdiction_id, rt.slug AS role_type"
        " FROM roles r JOIN role_types rt ON rt.id = r.role_type_id"
        " WHERE r.organization_id=$1 AND r.qualifier='Position 1'",
        house,
    )
    assert hp1["role_type"] == "state_representative"
    assert hp1["jurisdiction_id"] is not None

    # Senate seat has a NULL qualifier and exactly one row.
    sen = await db.fetch("SELECT qualifier FROM roles WHERE organization_id=$1", senate)
    assert len(sen) == 1
    assert sen[0]["qualifier"] is None


async def test_rejects_unknown_chamber(db):
    await _district(db, "usa-wa-ld-2")
    seats = [_seat("not_a_chamber", "state_senator", "usa-wa-ld-2", None, "X")]
    assert await seed_seats(db, seats) == {"new": 0, "attached": 0, "rejected": 1}


async def test_rejects_unknown_district(db):
    await _chamber(db, "usa_wa_senate")
    seats = [_seat("usa_wa_senate", "state_senator", "usa-wa-ld-999", None, "X")]
    assert await seed_seats(db, seats) == {"new": 0, "attached": 0, "rejected": 1}
