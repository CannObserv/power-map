"""Integration tests for jurisdiction addresses CRUD (#280 non-HTMX confirm persistence)."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from src.api.admin.deps import get_db
from src.api.main import app
from src.core.db import generate_id

pytestmark = [
    pytest.mark.integration,
]
AUTH_HEADERS = {"X-ExeDev-UserID": "usr_test", "X-ExeDev-Email": "admin@test.com"}
HTMX_HEADERS = {**AUTH_HEADERS, "HX-Request": "true"}


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
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test", follow_redirects=True
    ) as c:
        yield c
    app.dependency_overrides.pop(get_db, None)


@pytest_asyncio.fixture(loop_scope="session")
async def jurisdiction_and_address(db):
    jid = generate_id()
    aid = generate_id()
    eaid = generate_id()

    type_id = await db.fetchval(
        "SELECT id FROM jurisdiction_types WHERE slug='legislative_district'"
    )
    await db.execute(
        "INSERT INTO jurisdictions (id, slug, name, type_id) VALUES ($1, $2, $3, $4)",
        jid,
        f"ld-{jid[-8:].lower()}",
        "Test LD",
        type_id,
    )
    await db.execute(
        "INSERT INTO addresses (id, address_line_1, city, region, postal_code, country)"
        " VALUES ($1, '123 Main St', 'Olympia', 'WA', '98501', 'US')",
        aid,
    )
    await db.execute(
        "INSERT INTO entity_addresses"
        " (id, entity_type, entity_id, address_id, address_type)"
        " VALUES ($1, 'jurisdiction', $2, $3, 'mailing')",
        eaid,
        jid,
        aid,
    )

    yield jid, eaid


async def test_addresses_create_saves(client, jurisdiction_and_address, db):
    jid, existing_eaid = jurisdiction_and_address
    r = await client.post(
        f"/admin/jurisdictions/{jid}/addresses/",
        headers=HTMX_HEADERS,
        data={
            "address_line_1": "456 Oak Ave",
            "city": "Seattle",
            "region": "WA",
            "postal_code": "98101",
            "address_type": "physical",
            "mode": "save",
        },
    )
    assert r.status_code == 200

    row = await db.fetchrow(
        "SELECT a.address_line_1 FROM entity_addresses ea"
        " JOIN addresses a ON a.id = ea.address_id"
        " WHERE ea.entity_id=$1 AND ea.id != $2",
        jid,
        existing_eaid,
    )
    assert row is not None
    assert row["address_line_1"] == "456 Oak Ave"


@patch("src.api.admin.jurisdictions_addresses._NORMALIZER")
async def test_address_create_confirm_non_htmx_persists_and_redirects(
    mock_normalizer, client, jurisdiction_and_address, db
):
    """#280: a non-HTMX confirm submit persists the normalized address, no data loss."""
    jid, existing_eaid = jurisdiction_and_address
    mock_normalizer.normalize = AsyncMock(
        return_value=MagicMock(
            skipped=False,
            value={
                "address_line_1": "123 MAIN ST",
                "address_line_2": None,
                "city": "SEATTLE",
                "region": "WA",
                "postal_code": "98101",
                "country": "US",
                "standardized": "123 MAIN ST SEATTLE WA 98101",
                "latitude": None,
                "longitude": None,
                "components": None,
            },
            validation_detail=None,
        )
    )
    r = await client.post(
        f"/admin/jurisdictions/{jid}/addresses/",
        headers=AUTH_HEADERS,  # no HX-Request
        data={
            "address_line_1": "123 Main St",
            "city": "Seattle",
            "region": "WA",
            "postal_code": "98101",
            "address_type": "mailing",
        },
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert r.headers["location"] == f"/admin/jurisdictions/{jid}/"

    row = await db.fetchrow(
        "SELECT a.address_line_1, a.city, a.region, a.postal_code, a.standardized"
        " FROM entity_addresses ea JOIN addresses a ON a.id = ea.address_id"
        " WHERE ea.entity_id=$1 AND ea.id != $2",
        jid,
        existing_eaid,
    )
    assert row is not None
    assert row["address_line_1"] == "123 MAIN ST"
    assert row["standardized"] == "123 MAIN ST SEATTLE WA 98101"


@patch("src.api.admin.jurisdictions_addresses._NORMALIZER")
async def test_address_edit_confirm_non_htmx_persists_and_redirects(
    mock_normalizer, client, jurisdiction_and_address, db
):
    """#280: a non-HTMX confirm edit submit persists the normalized address."""
    jid, eaid = jurisdiction_and_address
    mock_normalizer.normalize = AsyncMock(
        return_value=MagicMock(
            skipped=False,
            value={
                "address_line_1": "123 MAIN ST",
                "address_line_2": None,
                "city": "OLYMPIA",
                "region": "WA",
                "postal_code": "98501",
                "country": "US",
                "standardized": "123 MAIN ST OLYMPIA WA 98501",
                "latitude": None,
                "longitude": None,
                "components": None,
            },
            validation_detail=None,
        )
    )
    r = await client.post(
        f"/admin/jurisdictions/{jid}/addresses/{eaid}/edit-row/",
        headers=AUTH_HEADERS,  # no HX-Request
        data={
            "address_line_1": "123 Main St",
            "city": "Olympia",
            "region": "WA",
            "postal_code": "98501",
            "address_type": "mailing",
        },
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert r.headers["location"] == f"/admin/jurisdictions/{jid}/"

    row = await db.fetchrow(
        "SELECT a.address_line_1, a.city, a.standardized"
        " FROM entity_addresses ea JOIN addresses a ON a.id = ea.address_id"
        " WHERE ea.id=$1",
        eaid,
    )
    assert row is not None
    assert row["address_line_1"] == "123 MAIN ST"
    assert row["standardized"] == "123 MAIN ST OLYMPIA WA 98501"
