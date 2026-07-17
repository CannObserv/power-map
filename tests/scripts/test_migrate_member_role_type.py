"""Integration tests for scripts/migrate_member_role_type.py (#266).

Splits the coarse `member` role_type into committee_member (committee orgs) +
party_member (party orgs) by structural org identifier. Requires
TEST_DATABASE_URL + a schema-applied DB.

Run via:
    uv run pytest tests/scripts/test_migrate_member_role_type.py
"""

import pytest
import pytest_asyncio

from scripts.migrate_member_role_type import migrate_member_role_type
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


async def _id_type(db, slug: str) -> str:
    return await db.fetchval("SELECT id FROM entity_identifier_types WHERE slug=$1", slug)


async def _org(db, *, id_type_slug: str | None = None, value: str = "x") -> str:
    oid = generate_id()
    await db.execute("INSERT INTO organizations (id) VALUES ($1)", oid)
    if id_type_slug is not None:
        await db.execute(
            "INSERT INTO identifiers (id, entity_id, entity_identifier_type_id, value)"
            " VALUES ($1,$2,$3,$4)",
            generate_id(),
            oid,
            await _id_type(db, id_type_slug),
            value,
        )
    return oid


async def _member_role(db, org_id: str, title: str = "Member") -> str:
    rid = generate_id()
    member_type = await db.fetchval("SELECT id FROM role_types WHERE slug='member'")
    await db.execute(
        "INSERT INTO roles (id, organization_id, title, role_type_id) VALUES ($1,$2,$3,$4)",
        rid,
        org_id,
        title,
        member_type,
    )
    return rid


async def _slug_of(db, role_id: str) -> str | None:
    return await db.fetchval(
        "SELECT rt.slug FROM roles r LEFT JOIN role_types rt ON rt.id=r.role_type_id WHERE r.id=$1",
        role_id,
    )


async def test_dry_run_classifies_without_mutating(db):
    committee_org = await _org(db, id_type_slug="org_wa_legislature_committee_id", value="20900")
    party_org = await _org(db, id_type_slug="org_wa_party", value="democratic")
    c_role = await _member_role(db, committee_org)
    p_role = await _member_role(db, party_org)

    report = await migrate_member_role_type(db, execute=False)
    by_role = {a["role_id"]: a for a in report["actions"]}
    assert by_role[c_role]["target"] == "committee_member"
    assert by_role[p_role]["target"] == "party_member"
    # Dry run mutates nothing.
    assert await _slug_of(db, c_role) == "member"
    assert await _slug_of(db, p_role) == "member"


async def test_execute_reassigns_by_org_kind(db):
    committee_org = await _org(db, id_type_slug="org_wa_legislature_committee_id", value="31639")
    party_org = await _org(db, id_type_slug="org_wa_party", value="republican")
    c_role = await _member_role(db, committee_org)
    p_role = await _member_role(db, party_org)

    await migrate_member_role_type(db, execute=True)
    assert await _slug_of(db, c_role) == "committee_member"
    assert await _slug_of(db, p_role) == "party_member"


async def test_org_without_discriminator_skipped(db):
    plain_org = await _org(db)  # no committee/party identifier
    role = await _member_role(db, plain_org)

    report = await migrate_member_role_type(db, execute=True)
    by_role = {a["role_id"]: a for a in report["actions"]}
    assert by_role[role]["target"] == "skipped"
    assert await _slug_of(db, role) == "member"  # untouched


async def test_idempotent_rerun_is_noop(db):
    committee_org = await _org(db, id_type_slug="org_wa_legislature_committee_id", value="20900")
    role = await _member_role(db, committee_org)

    await migrate_member_role_type(db, execute=True)
    assert await _slug_of(db, role) == "committee_member"
    # Re-run: the role no longer matches `member`, so it isn't in the actions.
    report = await migrate_member_role_type(db, execute=True)
    assert role not in {a["role_id"] for a in report["actions"]}
    assert await _slug_of(db, role) == "committee_member"
