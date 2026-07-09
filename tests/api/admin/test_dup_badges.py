"""Tests for GET /admin/_dup-badge/{type}/ async badge endpoint."""

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from src.api.admin.deps import get_db
from src.api.admin.org_dups import get_org_dup_count
from src.api.admin.people_dups import get_person_dup_count
from src.api.main import app

pytestmark = pytest.mark.integration

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


# ---------------------------------------------------------------------------
# People — card variant
# ---------------------------------------------------------------------------


async def test_people_card_returns_link_when_nonzero(client):
    app.dependency_overrides[get_person_dup_count] = lambda: 4
    try:
        resp = await client.get("/admin/_dup-badge/people/?variant=card", headers=HTMX_HEADERS)
    finally:
        app.dependency_overrides.pop(get_person_dup_count, None)
    assert resp.status_code == 200
    assert "4 duplicate" in resp.text
    assert "/admin/people/duplicates/" in resp.text


async def test_people_card_empty_when_zero(client):
    app.dependency_overrides[get_person_dup_count] = lambda: 0
    try:
        resp = await client.get("/admin/_dup-badge/people/?variant=card", headers=HTMX_HEADERS)
    finally:
        app.dependency_overrides.pop(get_person_dup_count, None)
    assert resp.status_code == 200
    assert resp.text.strip() == ""


async def test_people_card_singular_label(client):
    app.dependency_overrides[get_person_dup_count] = lambda: 1
    try:
        resp = await client.get("/admin/_dup-badge/people/?variant=card", headers=HTMX_HEADERS)
    finally:
        app.dependency_overrides.pop(get_person_dup_count, None)
    assert resp.status_code == 200
    assert "1 duplicate →" in resp.text
    assert "1 duplicates" not in resp.text


# ---------------------------------------------------------------------------
# People — banner variant
# ---------------------------------------------------------------------------


async def test_people_banner_returns_alert_when_nonzero(client):
    app.dependency_overrides[get_person_dup_count] = lambda: 3
    try:
        resp = await client.get("/admin/_dup-badge/people/?variant=banner", headers=HTMX_HEADERS)
    finally:
        app.dependency_overrides.pop(get_person_dup_count, None)
    assert resp.status_code == 200
    assert "possible duplicate" in resp.text.lower()
    assert "/admin/people/duplicates/" in resp.text


async def test_people_banner_empty_when_zero(client):
    app.dependency_overrides[get_person_dup_count] = lambda: 0
    try:
        resp = await client.get("/admin/_dup-badge/people/?variant=banner", headers=HTMX_HEADERS)
    finally:
        app.dependency_overrides.pop(get_person_dup_count, None)
    assert resp.status_code == 200
    assert resp.text.strip() == ""


# ---------------------------------------------------------------------------
# Orgs — card variant
# ---------------------------------------------------------------------------


async def test_orgs_card_returns_link_when_nonzero(client):
    app.dependency_overrides[get_org_dup_count] = lambda: 7
    try:
        resp = await client.get("/admin/_dup-badge/orgs/?variant=card", headers=HTMX_HEADERS)
    finally:
        app.dependency_overrides.pop(get_org_dup_count, None)
    assert resp.status_code == 200
    assert "7 duplicate" in resp.text
    assert "/admin/orgs/duplicates/" in resp.text


async def test_orgs_card_empty_when_zero(client):
    app.dependency_overrides[get_org_dup_count] = lambda: 0
    try:
        resp = await client.get("/admin/_dup-badge/orgs/?variant=card", headers=HTMX_HEADERS)
    finally:
        app.dependency_overrides.pop(get_org_dup_count, None)
    assert resp.status_code == 200
    assert resp.text.strip() == ""


# ---------------------------------------------------------------------------
# Orgs — banner variant
# ---------------------------------------------------------------------------


async def test_orgs_banner_returns_alert_when_nonzero(client):
    app.dependency_overrides[get_org_dup_count] = lambda: 2
    try:
        resp = await client.get("/admin/_dup-badge/orgs/?variant=banner", headers=HTMX_HEADERS)
    finally:
        app.dependency_overrides.pop(get_org_dup_count, None)
    assert resp.status_code == 200
    assert "possible duplicate" in resp.text.lower()
    assert "/admin/orgs/duplicates/" in resp.text


async def test_orgs_banner_empty_when_zero(client):
    app.dependency_overrides[get_org_dup_count] = lambda: 0
    try:
        resp = await client.get("/admin/_dup-badge/orgs/?variant=banner", headers=HTMX_HEADERS)
    finally:
        app.dependency_overrides.pop(get_org_dup_count, None)
    assert resp.status_code == 200
    assert resp.text.strip() == ""


# ---------------------------------------------------------------------------
# Guard: non-HTMX and unknown type
# ---------------------------------------------------------------------------


async def test_rejects_non_htmx_request(client):
    resp = await client.get("/admin/_dup-badge/people/?variant=card", headers=AUTH_HEADERS)
    assert resp.status_code == 400


async def test_unknown_type_returns_404(client):
    resp = await client.get("/admin/_dup-badge/invalid/?variant=card", headers=HTMX_HEADERS)
    assert resp.status_code == 404


async def test_unknown_variant_returns_400(client):
    resp = await client.get("/admin/_dup-badge/people/?variant=invalid", headers=HTMX_HEADERS)
    assert resp.status_code == 400


async def test_missing_variant_returns_422(client):
    resp = await client.get("/admin/_dup-badge/people/", headers=HTMX_HEADERS)
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Isolation: each endpoint must not invoke the other entity's count dep
# ---------------------------------------------------------------------------


async def test_people_badge_does_not_call_org_dup_count(client):
    """People badge must not invoke get_org_dup_count; split routes enforce this."""

    def _raise():
        raise AssertionError("get_org_dup_count must not be called for people badge")

    app.dependency_overrides[get_person_dup_count] = lambda: 2
    app.dependency_overrides[get_org_dup_count] = _raise
    try:
        resp = await client.get("/admin/_dup-badge/people/?variant=card", headers=HTMX_HEADERS)
    finally:
        app.dependency_overrides.pop(get_person_dup_count, None)
        app.dependency_overrides.pop(get_org_dup_count, None)
    assert resp.status_code == 200


async def test_orgs_badge_does_not_call_person_dup_count(client):
    """Orgs badge must not invoke get_person_dup_count; split routes enforce this."""

    def _raise():
        raise AssertionError("get_person_dup_count must not be called for orgs badge")

    app.dependency_overrides[get_org_dup_count] = lambda: 5
    app.dependency_overrides[get_person_dup_count] = _raise
    try:
        resp = await client.get("/admin/_dup-badge/orgs/?variant=card", headers=HTMX_HEADERS)
    finally:
        app.dependency_overrides.pop(get_org_dup_count, None)
        app.dependency_overrides.pop(get_person_dup_count, None)
    assert resp.status_code == 200
