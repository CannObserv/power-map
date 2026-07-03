"""Integration tests: seat-aware resolve_role (#261).

Districted seats match by (org, role_type, jurisdiction, qualifier); distinct
seats sharing a title must not collapse. Non-districted title matching is
unchanged.
"""

import pytest
import pytest_asyncio

from src.core.db import generate_id
from src.core.observation import Disposition, resolve_role

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


async def _org(db) -> str:
    oid = generate_id()
    await db.execute("INSERT INTO organizations (id) VALUES ($1)", oid)
    return oid


async def _jur(db) -> str:
    jid = generate_id()
    type_id = await db.fetchval(
        "SELECT id FROM jurisdiction_types WHERE slug='legislative_district'"
    )
    await db.execute(
        "INSERT INTO jurisdictions (id, slug, name, type_id) VALUES ($1,$2,$3,$4)",
        jid,
        f"ld-{jid[-8:].lower()}",
        "Test LD",
        type_id,
    )
    return jid


async def _rt(db, slug: str) -> str:
    return await db.fetchval("SELECT id FROM role_types WHERE slug=$1", slug)


async def test_distinct_qualifiers_create_distinct_seats(db):
    org, jur = await _org(db), await _jur(db)
    rt = await _rt(db, "state_representative")
    id1, disp1, _ = await resolve_role(
        db,
        org,
        "State Representative",
        role_type_id=rt,
        jurisdiction_id=jur,
        qualifier="Position 1",
    )
    id2, disp2, _ = await resolve_role(
        db,
        org,
        "State Representative",
        role_type_id=rt,
        jurisdiction_id=jur,
        qualifier="Position 2",
    )
    assert disp1 is Disposition.NEW
    assert disp2 is Disposition.NEW
    assert id1 != id2


async def test_same_seat_auto_attaches(db):
    org, jur = await _org(db), await _jur(db)
    rt = await _rt(db, "state_representative")
    kw = dict(role_type_id=rt, jurisdiction_id=jur, qualifier="Position 1")
    id1, disp1, _ = await resolve_role(db, org, "State Representative", **kw)
    id2, disp2, _ = await resolve_role(db, org, "State Representative", **kw)
    assert disp1 is Disposition.NEW
    assert disp2 is Disposition.AUTO_ATTACHED
    assert id1 == id2


async def test_senate_seat_null_qualifier_auto_attaches(db):
    org, jur = await _org(db), await _jur(db)
    rt = await _rt(db, "state_senator")
    id1, disp1, _ = await resolve_role(
        db, org, "State Senator", role_type_id=rt, jurisdiction_id=jur
    )
    id2, disp2, _ = await resolve_role(
        db, org, "State Senator", role_type_id=rt, jurisdiction_id=jur
    )
    assert disp1 is Disposition.NEW
    assert disp2 is Disposition.AUTO_ATTACHED
    assert id1 == id2


async def test_title_only_path_unchanged(db):
    org = await _org(db)
    id1, disp1, _ = await resolve_role(db, org, "Speaker")
    id2, disp2, _ = await resolve_role(db, org, "speaker")
    assert disp1 is Disposition.NEW
    assert disp2 is Disposition.AUTO_ATTACHED
    assert id1 == id2


async def test_title_observation_does_not_attach_to_seat(db):
    """A title-only resolve must not glue onto a districted seat of the same title."""
    org, jur = await _org(db), await _jur(db)
    rt = await _rt(db, "state_representative")
    seat_id, _, _ = await resolve_role(
        db,
        org,
        "State Representative",
        role_type_id=rt,
        jurisdiction_id=jur,
        qualifier="Position 1",
    )
    title_id, disp, _ = await resolve_role(db, org, "State Representative")
    assert disp is Disposition.NEW
    assert title_id != seat_id


async def test_districted_requires_role_type(db):
    org, jur = await _org(db), await _jur(db)
    role_id, disp, reason = await resolve_role(
        db, org, "State Representative", jurisdiction_id=jur, qualifier="Position 1"
    )
    assert disp is Disposition.REJECTED
    assert role_id == ""
    assert "role_type" in reason


async def test_unknown_jurisdiction_rejected(db):
    org = await _org(db)
    rt = await _rt(db, "state_representative")
    role_id, disp, reason = await resolve_role(
        db, org, "State Representative", role_type_id=rt, jurisdiction_id=generate_id()
    )
    assert disp is Disposition.REJECTED
    assert "jurisdiction_not_found" in reason


async def test_unknown_role_type_rejected(db):
    org, jur = await _org(db), await _jur(db)
    role_id, disp, reason = await resolve_role(
        db, org, "State Representative", role_type_id=generate_id(), jurisdiction_id=jur
    )
    assert disp is Disposition.REJECTED
    assert "role_type_not_found" in reason
