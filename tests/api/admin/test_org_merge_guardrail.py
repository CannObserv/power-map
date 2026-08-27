"""Merge guardrail (#469): same-external-type, distinct-value identifier conflicts.

Two orgs carrying different values of one external identifier type are two
*source records* — merging them makes that identifier→org mapping N:1 and
breaks the documented one-key-one-org contract. The preview modal must say so,
demote the merge behind an explicit acknowledgement, and offer the
link-as-successors verb as the primary alternative.
"""

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from src.api.admin.deps import get_db
from src.api.main import app
from src.core.db import generate_id

pytestmark = [
    pytest.mark.integration,
]

AUTH_HEADERS = {
    "X-ExeDev-UserID": "usr_test",
    "X-ExeDev-Email": "admin@test.com",
}


@pytest_asyncio.fixture(loop_scope="session")
async def db(db_pool):
    """Pool-acquired connection wrapped in a rolled-back transaction."""
    async with db_pool.acquire() as conn:
        tr = conn.transaction()
        await tr.start()
        try:
            yield conn
        finally:
            await tr.rollback()


@pytest_asyncio.fixture(loop_scope="session")
async def client(db):
    """AsyncClient with app, overriding get_db to use the test connection."""

    async def _get_db_override():
        yield db

    app.dependency_overrides[get_db] = _get_db_override
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c
    app.dependency_overrides.pop(get_db, None)


async def _mk_org(db, name):
    oid = generate_id()
    await db.execute("INSERT INTO organizations (id) VALUES ($1)", oid)
    await db.execute(
        "INSERT INTO organization_names (id, organization_id, name, is_canonical)"
        " VALUES ($1, $2, $3, TRUE)",
        generate_id(),
        oid,
        name,
    )
    return oid


async def _type_id(db, slug, *, internal=False):
    row = await db.fetchrow("SELECT id FROM entity_identifier_types WHERE slug=$1", slug)
    if row:
        return row["id"]
    tid = generate_id()
    await db.execute(
        "INSERT INTO entity_identifier_types"
        " (id, entity_type, slug, display_name, full_name, is_internal)"
        " VALUES ($1,'organization',$2,$3,$3,$4)",
        tid,
        slug,
        slug,
        internal,
    )
    return tid


async def _attach(db, org_id, type_id, value):
    await db.execute(
        "INSERT INTO identifiers (id, entity_id, entity_identifier_type_id, value)"
        " VALUES ($1,$2,$3,$4)",
        generate_id(),
        org_id,
        type_id,
        value,
    )


async def _preview(client, id_a, id_b):
    return await client.get(
        f"/admin/orgs/{id_a}/merge-preview/{id_b}/?winner={id_a}", headers=AUTH_HEADERS
    )


async def test_conflicting_external_identifiers_raise_the_guardrail(client, db):
    a = await _mk_org(db, "Guardrail Committee Alpha")
    b = await _mk_org(db, "Guardrail Committee Beta")
    tid = await _type_id(db, "org_wa_legislature_committee_id")
    await _attach(db, a, tid, "31651")
    await _attach(db, b, tid, "3532")
    r = await _preview(client, a, b)
    assert r.status_code == 200
    text = r.text
    assert 'class="merge-identifier-conflict-cb"' in text
    assert "org_wa_legislature_committee_id" in text
    assert "31651" in text and "3532" in text
    # Alternative verb offered from inside the preview.
    assert f"/admin/orgs/{a}/link-successor-preview/{b}/" in text
    # Merge demoted: execute ships disabled until the acknowledgement is checked.
    assert 'id="merge-execute-btn" disabled' in " ".join(text.split())


async def test_shared_identifier_value_is_not_a_conflict(client, db):
    a = await _mk_org(db, "Guardrail Committee Gamma")
    b = await _mk_org(db, "Guardrail Committee Delta")
    tid = await _type_id(db, "org_wa_legislature_committee_id")
    await _attach(db, a, tid, "20900")
    await _attach(db, b, tid, "20900")
    r = await _preview(client, a, b)
    assert r.status_code == 200
    assert 'class="merge-identifier-conflict-cb"' not in r.text


async def test_internal_identifier_types_do_not_trigger(client, db):
    a = await _mk_org(db, "Guardrail Committee Epsilon")
    b = await _mk_org(db, "Guardrail Committee Zeta")
    tid = await _type_id(db, "pm_guardrail_test_internal", internal=True)
    await _attach(db, a, tid, "AAA")
    await _attach(db, b, tid, "BBB")
    r = await _preview(client, a, b)
    assert r.status_code == 200
    assert 'class="merge-identifier-conflict-cb"' not in r.text


async def test_no_identifiers_no_guardrail(client, db):
    a = await _mk_org(db, "Guardrail Committee Eta")
    b = await _mk_org(db, "Guardrail Committee Theta")
    r = await _preview(client, a, b)
    assert r.status_code == 200
    assert 'class="merge-identifier-conflict-cb"' not in r.text
