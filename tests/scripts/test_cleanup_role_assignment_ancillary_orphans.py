"""Integration tests for the #324 orphan cleanup script.

Verifies the per-dead-id heuristic dispositions (PDC filer, email→name→seat,
redundant-link purge, manual fallback) and that ``--execute`` re-homes/purges
while leaving manual rows untouched.
"""

import pytest
import pytest_asyncio

from scripts.cleanup_role_assignment_ancillary_orphans import apply_cleanup, plan_cleanup
from src.core.db import generate_id

pytestmark = [pytest.mark.integration]

_CHAMBER_IDTYPE = "01KVJWW7YCKYMY4ZEEZWPKZT5H"  # org_wa_legislature_chamber
_FILER_IDTYPE = "01KXK8AYH1MTCEZ9E5G2VR28KE"  # person_wa_pdc_filer
_RA_PDC_IDTYPE = "01KKZ3WGJSZF0F96SMYC000AVV"  # role_wa_pdc
_CHAMBER_LEADER = "01KX0000000000000000000004"  # role_type chamber_leader (no qualifier)
_LINK_TYPE = "01KKZ3WGJRPV2TDZV672NWFE8G"  # twitter


@pytest_asyncio.fixture(loop_scope="session")
async def db(db_pool):
    async with db_pool.acquire() as conn:
        tr = conn.transaction()
        await tr.start()
        try:
            yield conn
        finally:
            await tr.rollback()


async def _chamber_org(db) -> str:
    oid = generate_id()
    await db.execute("INSERT INTO organizations (id) VALUES ($1)", oid)
    await db.execute(
        "INSERT INTO organization_names (id, organization_id, name, is_canonical)"
        " VALUES ($1, $2, 'Test Chamber', TRUE)",
        generate_id(),
        oid,
    )
    await db.execute(
        "INSERT INTO identifiers (id, entity_id, entity_identifier_type_id, value)"
        " VALUES ($1, $2, $3, $4)",
        generate_id(),
        oid,
        _CHAMBER_IDTYPE,
        f"chamber_{oid[:8]}",
    )
    return oid


async def _person_with_seat(db, org_id, display_name) -> tuple[str, str]:
    """Person + a current typed seat on org_id. Returns (person_id, seat_assignment_id)."""
    pid = generate_id()
    await db.execute("INSERT INTO people (id) VALUES ($1)", pid)
    await db.execute(
        "INSERT INTO person_names (id, person_id, name, is_canonical) VALUES ($1, $2, $3, TRUE)",
        generate_id(),
        pid,
        display_name,
    )
    rid = generate_id()
    await db.execute(
        "INSERT INTO roles (id, organization_id, title, role_type_id) VALUES ($1, $2, $3, $4)",
        rid,
        org_id,
        f"Leader {rid}",
        _CHAMBER_LEADER,
    )
    seat = generate_id()
    await db.execute(
        "INSERT INTO role_assignments (id, person_id, role_id, is_current)"
        " VALUES ($1, $2, $3, TRUE)",
        seat,
        pid,
        rid,
    )
    return pid, seat


async def _orphan_contact(db, dead_id, value):
    rid = generate_id()
    await db.execute(
        "INSERT INTO contact_methods (id, entity_type, entity_id, contact_type, value)"
        " VALUES ($1, 'role_assignment', $2, 'email', $3)",
        rid,
        dead_id,
        value,
    )
    return rid


async def test_email_name_seat_rehome(db):
    org = await _chamber_org(db)
    _pid, seat = await _person_with_seat(db, org, "Alex Ramel")
    dead = generate_id()  # never inserted into role_assignments → orphan
    cid = await _orphan_contact(db, dead, "alex.ramel@leg.wa.gov")

    groups = await plan_cleanup(db)
    grp = next(g for g in groups if g.dead_id == dead)
    assert grp.target_id == seat
    assert grp.method == "email_name"

    await apply_cleanup(db, groups)
    assert await db.fetchval("SELECT entity_id FROM contact_methods WHERE id=$1", cid) == seat


async def test_pdc_filer_rehome(db):
    org = await _chamber_org(db)
    pid, seat = await _person_with_seat(db, org, "Michelle Caldier")
    await db.execute(
        "INSERT INTO identifiers (id, entity_id, entity_identifier_type_id, value)"
        " VALUES ($1, $2, $3, 'CALDM  366')",
        generate_id(),
        pid,
        _FILER_IDTYPE,
    )
    dead = generate_id()
    iid = generate_id()
    await db.execute(
        "INSERT INTO identifiers (id, entity_id, entity_identifier_type_id, value)"
        " VALUES ($1, $2, $3, $4)",
        iid,
        dead,
        _RA_PDC_IDTYPE,
        "https://www.pdc.wa.gov/browse/campaign-explorer/candidate?filer_id=CALDM%20%20366",
    )

    groups = await plan_cleanup(db)
    grp = next(g for g in groups if g.dead_id == dead)
    assert grp.target_id == seat
    assert grp.method == "pdc_filer"

    await apply_cleanup(db, groups)
    assert await db.fetchval("SELECT entity_id FROM identifiers WHERE id=$1", iid) == seat


async def test_redundant_link_purged(db):
    org = await _chamber_org(db)
    _pid, live_seat = await _person_with_seat(db, org, "Board Member")
    url = "https://board.example/our-board"
    # identical link on a LIVE assignment
    await db.execute(
        "INSERT INTO links (id, entity_type, entity_id, url, link_type_id)"
        " VALUES ($1, 'role_assignment', $2, $3, $4)",
        generate_id(),
        live_seat,
        url,
        _LINK_TYPE,
    )
    dead = generate_id()
    orphan_link = generate_id()
    await db.execute(
        "INSERT INTO links (id, entity_type, entity_id, url, link_type_id)"
        " VALUES ($1, 'role_assignment', $2, $3, $4)",
        orphan_link,
        dead,
        url,
        _LINK_TYPE,
    )

    groups = await plan_cleanup(db)
    grp = next(g for g in groups if g.dead_id == dead)
    assert grp.target_id is None  # no rehome heuristic

    await apply_cleanup(db, groups)
    assert await db.fetchval("SELECT count(*) FROM links WHERE id=$1", orphan_link) == 0


async def test_unmatched_left_manual(db):
    dead = generate_id()
    cid = await _orphan_contact(db, dead, "sethraydawson@gmail.com")  # no dotted name

    groups = await plan_cleanup(db)
    grp = next(g for g in groups if g.dead_id == dead)
    assert grp.target_id is None

    stats = await apply_cleanup(db, groups)
    assert stats["manual"] >= 1
    # untouched — still orphaned, awaiting human triage
    assert await db.fetchval("SELECT entity_id FROM contact_methods WHERE id=$1", cid) == dead


async def test_ambiguous_name_not_rehomed(db):
    org = await _chamber_org(db)
    await _person_with_seat(db, org, "Sam Jones")
    await _person_with_seat(db, org, "Sam Jones")  # two people, same display name
    dead = generate_id()
    await _orphan_contact(db, dead, "sam.jones@leg.wa.gov")

    groups = await plan_cleanup(db)
    grp = next(g for g in groups if g.dead_id == dead)
    assert grp.target_id is None  # ambiguous → refuse to auto-rehome
