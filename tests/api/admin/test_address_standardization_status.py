"""The admin address forms record why a row has, or lacks, a standardized form (#595).

Each router's ``_NORMALIZER`` is swapped for a real ``FallbackAddressNormalizer``
over a mocked HTTP client, so the recorded status comes from a genuine result.
"Keep my input" must record ``declined``: the one status a sweep never retries.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import httpx
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from src.api.admin.deps import get_db
from src.api.main import app
from src.core.db import generate_id
from src.core.normalizers.address import AddressNormalizerConfig, FallbackAddressNormalizer
from tests.core.normalizers.http_mock import mock_http_client

pytestmark = [pytest.mark.integration]
AUTH_HEADERS = {"X-ExeDev-UserID": "usr_test", "X-ExeDev-Email": "admin@test.com"}
HTMX_HEADERS = {**AUTH_HEADERS, "HX-Request": "true"}
FORM = {
    "address_line_1": "123 Main St",
    "city": "Seattle",
    "region": "WA",
    "postal_code": "98101",
    "address_type": "mailing",
}
STORED = {
    "address_line_1": "1 Old Rd",
    "city": "Olympia",
    "region": "WA",
    "postal_code": "98501",
    "address_type": "mailing",
}
ENTITIES = [
    ("orgs", "organization", "orgs_addresses"),
    ("people", "person", "people_addresses"),
    ("jurisdictions", "jurisdiction", "jurisdictions_addresses"),
]


@pytest_asyncio.fixture(loop_scope="session")
async def db(db_pool):
    async with db_pool.acquire() as conn:
        tr = conn.transaction()
        await tr.start()
        try:
            yield conn
        finally:
            await tr.rollback()


@pytest_asyncio.fixture(loop_scope="session")
async def client(db):
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
    """One entity kind with one stored, standardized address."""
    segment, entity_type, module = request.param
    eid = await _insert_entity(db, entity_type)
    aid, eaid = generate_id(), generate_id()
    await db.execute(
        "INSERT INTO addresses (id, address_line_1, city, region, postal_code, country,"
        "  standardized, standardization_status)"
        " VALUES ($1, '1 Old Rd', 'Olympia', 'WA', '98501', 'US',"
        "  '1 OLD RD OLYMPIA WA 98501', 'standardized')",
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
    yield SimpleNamespace(
        base=f"/admin/{segment}/{eid}", eid=eid, aid=aid, eaid=eaid, module=module
    )


def _normalizer(module: str):
    """A real external-backed normalizer, retries off (no sleeps)."""
    normalizer = FallbackAddressNormalizer(
        AddressNormalizerConfig(api_key="test-key", transient_retries=0)
    )
    return patch(f"src.api.admin.{module}._NORMALIZER", normalizer)


def _local_normalizer(module: str):
    return patch(f"src.api.admin.{module}._NORMALIZER", FallbackAddressNormalizer(config=None))


def _ok_response():
    r = MagicMock()
    r.status_code = 200
    r.json.return_value = {
        "address_line_1": "123 MAIN ST",
        "city": "SEATTLE",
        "region": "WA",
        "postal_code": "98101",
        "country": "US",
        "standardized": "123 MAIN ST SEATTLE WA 98101",
        "warnings": [],
    }
    return r


def _no_form_response():
    r = MagicMock()
    r.status_code = 200
    r.json.return_value = {"address_line_1": None, "country": "US", "warnings": []}
    return r


async def _new_row(db, eid: str):
    return await db.fetchrow(
        "SELECT a.standardization_status, a.standardization_checked_at"
        " FROM entity_addresses ea JOIN addresses a ON a.id = ea.address_id"
        " WHERE ea.entity_id=$1 AND a.address_line_1 IN ('123 Main St', '123 MAIN ST')",
        eid,
    )


async def _stored_status(db, aid: str) -> str | None:
    return await db.fetchval("SELECT standardization_status FROM addresses WHERE id=$1", aid)


# ── create ───────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("failure", "status"),
    [
        (httpx.ConnectError("x"), "unavailable"),
        (httpx.Response(422, request=httpx.Request("POST", "https://av.test/")), "rejected"),
        (
            httpx.Response(
                422,
                json={"error": "country_not_supported", "message": "x"},
                request=httpx.Request("POST", "https://av.test/"),
            ),
            "unsupported",
        ),
    ],
    ids=["unavailable", "rejected", "unsupported"],
)
async def test_create_records_the_fallback_reason(client, entity, db, failure, status):
    with _normalizer(entity.module), mock_http_client(side_effect=[failure]):
        r = await client.post(f"{entity.base}/addresses/", headers=HTMX_HEADERS, data=FORM)
    assert r.status_code == 200
    row = await _new_row(db, entity.eid)
    assert row["standardization_status"] == status
    assert row["standardization_checked_at"] is not None


async def test_create_records_no_result(client, entity, db):
    with _normalizer(entity.module), mock_http_client(_no_form_response()):
        await client.post(f"{entity.base}/addresses/", headers=HTMX_HEADERS, data=FORM)
    assert (await _new_row(db, entity.eid))["standardization_status"] == "no_result"


async def test_create_records_local_for_a_config_less_normalizer(client, entity, db):
    with _local_normalizer(entity.module):
        await client.post(f"{entity.base}/addresses/", headers=HTMX_HEADERS, data=FORM)
    assert (await _new_row(db, entity.eid))["standardization_status"] == "local"


async def test_keep_my_input_records_declined(client, entity, db):
    """The modal's "Keep my input": mode=save without a standardized form."""
    r = await client.post(
        f"{entity.base}/addresses/", headers=HTMX_HEADERS, data={**FORM, "mode": "save"}
    )
    assert r.status_code == 200
    row = await _new_row(db, entity.eid)
    assert row["standardization_status"] == "declined"
    assert row["standardization_checked_at"] is not None


async def test_accept_records_standardized(client, entity, db):
    """The modal's Accept: mode=save carrying the standardized form."""
    data = {**FORM, "mode": "save", "standardized": "123 MAIN ST SEATTLE WA 98101"}
    await client.post(f"{entity.base}/addresses/", headers=HTMX_HEADERS, data=data)
    assert (await _new_row(db, entity.eid))["standardization_status"] == "standardized"


async def test_non_htmx_auto_accept_records_standardized(client, entity, db):
    """#280: a non-HTMX client takes the standardized form without a modal."""
    with _normalizer(entity.module), mock_http_client(_ok_response()):
        r = await client.post(f"{entity.base}/addresses/", headers=AUTH_HEADERS, data=FORM)
    assert r.status_code == 303
    assert (await _new_row(db, entity.eid))["standardization_status"] == "standardized"


# ── edit ─────────────────────────────────────────────────────────────────────


async def test_edit_keep_my_input_records_declined(client, entity, db):
    r = await client.post(
        f"{entity.base}/addresses/{entity.eaid}/edit-row/",
        headers=HTMX_HEADERS,
        data={**FORM, "mode": "save"},
    )
    assert r.status_code == 200
    assert await _stored_status(db, entity.aid) == "declined"


async def test_edit_changed_address_during_outage_records_unavailable(client, entity, db):
    with _normalizer(entity.module), mock_http_client(side_effect=httpx.ConnectError("x")):
        await client.post(
            f"{entity.base}/addresses/{entity.eaid}/edit-row/", headers=HTMX_HEADERS, data=FORM
        )
    assert await _stored_status(db, entity.aid) == "unavailable"


async def test_edit_unchanged_address_during_outage_keeps_the_stored_status(client, entity, db):
    """#589 CR 1's keep_stored path leaves the row, status included, alone."""
    with _normalizer(entity.module), mock_http_client(side_effect=httpx.ConnectError("x")):
        await client.post(
            f"{entity.base}/addresses/{entity.eaid}/edit-row/",
            headers=HTMX_HEADERS,
            data={**STORED, "display_name": "Front office"},
        )
    assert await _stored_status(db, entity.aid) == "standardized"
