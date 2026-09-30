"""The admin address forms tell the curator when standardization fell back (#589).

Crosses the seam: each router's ``_NORMALIZER`` is a real ``FallbackAddressNormalizer``
whose HTTP client fails, so the flash is driven by a genuine fallback result rather
than a hand-built one.
"""

import json
from unittest.mock import patch

import httpx
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from src.api.admin.deps import get_db
from src.api.main import app
from src.core.db import generate_id
from src.core.normalizers.address import AddressNormalizerConfig, FallbackAddressNormalizer
from tests.core.normalizers.http_mock import mock_http_client

pytestmark = [
    pytest.mark.integration,
]
AUTH_HEADERS = {"X-ExeDev-UserID": "usr_test", "X-ExeDev-Email": "admin@test.com"}
HTMX_HEADERS = {**AUTH_HEADERS, "HX-Request": "true"}
FORM = {
    "address_line_1": "123 Main St",
    "city": "Seattle",
    "region": "WA",
    "postal_code": "98101",
    "address_type": "mailing",
}
UNAVAILABLE = "Not standardized: the address service is unavailable."
REJECTED = "Not standardized: the address service couldn't read it."

# (url segment, entity_type, router module)
ENTITIES = [
    ("orgs", "organization", "orgs_addresses"),
    ("people", "person", "people_addresses"),
    ("jurisdictions", "jurisdiction", "jurisdictions_addresses"),
]


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


async def _insert_entity(db, entity_type: str) -> str:
    eid = generate_id()
    if entity_type == "organization":
        await db.execute("INSERT INTO organizations (id) VALUES ($1)", eid)
    elif entity_type == "person":
        await db.execute("INSERT INTO people (id) VALUES ($1)", eid)
    else:
        type_id = await db.fetchval(
            "SELECT id FROM jurisdiction_types WHERE slug='legislative_district'"
        )
        await db.execute(
            "INSERT INTO jurisdictions (id, slug, name, type_id) VALUES ($1, $2, $3, $4)",
            eid,
            f"ld-{eid[-8:].lower()}",
            "Test LD",
            type_id,
        )
    return eid


@pytest_asyncio.fixture(loop_scope="session", params=ENTITIES, ids=[e[0] for e in ENTITIES])
async def entity(request, db):
    """(base URL, existing entity_addresses id, router module) for one entity kind."""
    segment, entity_type, module = request.param
    eid = await _insert_entity(db, entity_type)
    aid, eaid = generate_id(), generate_id()
    await db.execute(
        "INSERT INTO addresses (id, address_line_1, city, region, postal_code, country)"
        " VALUES ($1, '1 Old Rd', 'Olympia', 'WA', '98501', 'US')",
        aid,
    )
    await db.execute(
        "INSERT INTO entity_addresses (id, entity_type, entity_id, address_id, address_type)"
        " VALUES ($1, $2, $3, $4, 'mailing')",
        eaid,
        entity_type,
        eid,
        aid,
    )
    yield f"/admin/{segment}/{eid}", eaid, module


def _failing_normalizer(module: str):
    """Swap the router's normalizer for a real one with retries off (no sleeps)."""
    normalizer = FallbackAddressNormalizer(
        AddressNormalizerConfig(api_key="test-key", transient_retries=0)
    )
    return patch(f"src.api.admin.{module}._NORMALIZER", normalizer)


def _flash(r) -> dict:
    return json.loads(r.headers["hx-trigger"])["showFlash"]


async def test_create_flashes_unavailable_notice(client, entity):
    base, _, module = entity
    with _failing_normalizer(module), mock_http_client(side_effect=httpx.ConnectError("x")):
        r = await client.post(f"{base}/addresses/", headers=HTMX_HEADERS, data=FORM)
    assert r.status_code == 200
    assert _flash(r) == {"level": "success", "body": f"Address added. {UNAVAILABLE}"}


async def test_create_flashes_rejected_notice(client, entity):
    base, _, module = entity
    rejected = httpx.Response(422, request=httpx.Request("POST", "https://av.test/"))
    with _failing_normalizer(module), mock_http_client(rejected):
        r = await client.post(f"{base}/addresses/", headers=HTMX_HEADERS, data=FORM)
    assert _flash(r)["body"] == f"Address added. {REJECTED}"


async def test_edit_flashes_unavailable_notice(client, entity):
    base, eaid, module = entity
    with _failing_normalizer(module), mock_http_client(side_effect=httpx.ConnectError("x")):
        r = await client.post(f"{base}/addresses/{eaid}/edit-row/", headers=HTMX_HEADERS, data=FORM)
    assert r.status_code == 200
    assert _flash(r) == {"level": "success", "body": f"Address saved. {UNAVAILABLE}"}


async def test_create_non_htmx_redirects_with_unstandardized_key(client, entity, db):
    base, _, module = entity
    with _failing_normalizer(module), mock_http_client(side_effect=httpx.ConnectError("x")):
        r = await client.post(f"{base}/addresses/", headers=AUTH_HEADERS, data=FORM)
    assert r.status_code == 303
    assert r.headers["location"] == f"{base}/?flash=saved_unstandardized"
    saved = await db.fetchval(
        "SELECT count(*) FROM addresses WHERE address_line_1 = '123 Main St'"
        " AND standardized IS NULL"
    )
    assert saved == 1


async def test_edit_non_htmx_redirects_with_unstandardized_key(client, entity):
    base, eaid, module = entity
    with _failing_normalizer(module), mock_http_client(side_effect=httpx.ConnectError("x")):
        r = await client.post(f"{base}/addresses/{eaid}/edit-row/", headers=AUTH_HEADERS, data=FORM)
    assert r.status_code == 303
    assert r.headers["location"] == f"{base}/?flash=saved_unstandardized"


async def test_keep_my_input_save_carries_no_notice(client, entity):
    """mode=save is the curator's own choice; nothing fell back."""
    base, _, _ = entity
    r = await client.post(f"{base}/addresses/", headers=HTMX_HEADERS, data={**FORM, "mode": "save"})
    assert _flash(r) == {"level": "success", "body": "Address added."}
