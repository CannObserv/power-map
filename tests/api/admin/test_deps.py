"""Tests for admin auth dependency."""

import asyncio
import json
from unittest.mock import MagicMock

import pytest
import pytest_asyncio
from fastapi import HTTPException
from fastapi.testclient import TestClient
from httpx import ASGITransport, AsyncClient

from src.api.admin.deps import (
    SHARED_FLASH_MESSAGES,
    AdminUser,
    flash_trigger,
    get_admin_user,
    get_db,
    is_htmx,
    parse_validity_fields,
    resolve_query_flash,
    with_flash,
)
from src.api.main import app
from tests.api.admin.conftest import AUTH_HEADERS

# Module-level client WITHOUT `with` → no lifespan → no app pool. The sync
# unit tests below never touch the DB (auth/redirect/helper logic only).
_client = TestClient(app, raise_server_exceptions=False)


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


def test_admin_root_redirects_when_unauthenticated():
    """GET /admin/ without exe.dev headers must redirect to login."""
    response = _client.get("/admin/", follow_redirects=False)
    assert response.status_code in (302, 307)
    assert "/__exe.dev/login" in response.headers["location"]


def test_admin_root_redirects_when_only_user_id_present():
    response = _client.get("/admin/", headers={"X-ExeDev-UserID": "usr123"}, follow_redirects=False)
    assert response.status_code in (302, 307)
    assert "/__exe.dev/login" in response.headers["location"]


def test_admin_root_redirects_when_only_email_present():
    response = _client.get("/admin/", headers={"X-ExeDev-Email": "a@b.com"}, follow_redirects=False)
    assert response.status_code in (302, 307)
    assert "/__exe.dev/login" in response.headers["location"]


def test_admin_user_dataclass():
    user = AdminUser(id="usr123", email="a@b.com")
    assert user.id == "usr123"
    assert user.email == "a@b.com"


def test_get_admin_user_returns_user_when_headers_present():
    """get_admin_user must extract headers into AdminUser."""
    request = MagicMock()
    request.headers = {
        "X-ExeDev-UserID": "usr_abc",
        "X-ExeDev-Email": "test@example.com",
    }
    request.url.path = "/admin/"

    result = asyncio.run(get_admin_user(request))
    assert isinstance(result, AdminUser)
    assert result.id == "usr_abc"
    assert result.email == "test@example.com"


def test_get_admin_user_raises_307_when_headers_absent():
    """get_admin_user must raise HTTPException(307) with login Location when headers absent."""
    request = MagicMock()
    request.headers = {}
    request.url.path = "/admin/"
    request.url.query = ""

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(get_admin_user(request))
    assert exc_info.value.status_code == 307
    assert "/__exe.dev/login" in exc_info.value.headers["Location"]


def test_admin_root_redirect_preserves_query_string():
    response = _client.get("/admin/?page=2&q=foo", follow_redirects=False)
    assert response.status_code in (302, 307)
    location = response.headers["location"]
    assert "redirect=/admin/%3Fpage%3D2%26q%3Dfoo" in location


def test_is_htmx_returns_true_for_htmx_non_boosted_request():
    request = MagicMock()
    request.headers = {"HX-Request": "true"}
    assert is_htmx(request) is True


def test_is_htmx_returns_false_for_non_htmx_request():
    request = MagicMock()
    request.headers = {}
    assert is_htmx(request) is False


def test_is_htmx_returns_false_for_boosted_request():
    """HX-Boosted is set on sidebar navigation; must return False so full page is rendered."""
    request = MagicMock()
    request.headers = {"HX-Request": "true", "HX-Boosted": "true"}
    assert is_htmx(request) is False


def test_flash_trigger_returns_hx_trigger_header():
    headers = flash_trigger("success", "Saved.")
    assert "HX-Trigger" in headers


def test_flash_trigger_value_is_valid_json():
    headers = flash_trigger("info", "Done.")
    json.loads(headers["HX-Trigger"])  # must not raise


def test_flash_trigger_payload_shape():
    headers = flash_trigger("error", "<strong>Oops</strong>")
    payload = json.loads(headers["HX-Trigger"])
    assert "showFlash" in payload
    assert payload["showFlash"]["level"] == "error"
    assert payload["showFlash"]["body"] == "<strong>Oops</strong>"


def test_flash_trigger_returns_only_hx_trigger_key():
    """Helper must return exactly one header key regardless of extra payload.

    All events (showFlash and any extra keys) are encoded inside the single
    HX-Trigger JSON value — callers spread this one-key dict into TemplateResponse.
    """
    headers = flash_trigger("warning", "Watch out.")
    assert list(headers.keys()) == ["HX-Trigger"]


def test_flash_trigger_extra_merges_into_payload():
    """extra dict keys must be merged alongside showFlash in the HX-Trigger JSON."""
    headers = flash_trigger(
        "success", "Saved.", extra={"updateOrgHeader": {"display": "Acme Corp"}}
    )
    payload = json.loads(headers["HX-Trigger"])
    assert "showFlash" in payload
    assert "updateOrgHeader" in payload
    assert payload["updateOrgHeader"]["display"] == "Acme Corp"


def test_flash_trigger_extra_none_leaves_payload_unchanged():
    """Passing extra=None must produce the same output as omitting extra."""
    headers = flash_trigger("info", "Done.", extra=None)
    payload = json.loads(headers["HX-Trigger"])
    assert list(payload.keys()) == ["showFlash"]


def test_flash_trigger_extra_multiple_keys():
    """extra may contain multiple keys; all must appear in the payload."""
    headers = flash_trigger("info", "x", extra={"a": 1, "b": 2})
    payload = json.loads(headers["HX-Trigger"])
    assert payload["a"] == 1
    assert payload["b"] == 2


@pytest.mark.integration
async def test_admin_dashboard_returns_200_when_authenticated(client):
    response = await client.get("/admin/", headers=AUTH_HEADERS)
    assert response.status_code == 200
    assert "Power Map" in response.text


# --- with_flash (append ?flash= to a §32 fallback redirect URL, #351) --------


def test_with_flash_appends_query_param_to_bare_path():
    assert with_flash("/admin/orgs/org_123/", "saved") == "/admin/orgs/org_123/?flash=saved"


def test_with_flash_preserves_existing_query_params():
    out = with_flash("/admin/orgs/?status=active&q=foo", "removed")
    # flash added; existing params retained
    assert "flash=removed" in out
    assert "status=active" in out
    assert "q=foo" in out


def test_with_flash_overwrites_existing_flash_param():
    out = with_flash("/admin/orgs/org_1/?flash=stale", "saved")
    assert out.count("flash=") == 1
    assert "flash=saved" in out
    assert "stale" not in out


def test_with_flash_preserves_fragment():
    out = with_flash("/admin/orgs/org_1/#links", "saved")
    assert out.endswith("#links")
    assert "flash=saved" in out


def test_with_flash_keys_are_registered():
    """Every key with_flash callers use must resolve; guard against typos."""
    for key in ("saved", "removed", "invalid", "exists"):
        assert key in SHARED_FLASH_MESSAGES


# --- resolve_query_flash falls back to the shared registry (#351) ------------


def _req(htmx: bool = False, url: str = "http://test/admin/orgs/org_1/?flash=saved"):
    from starlette.datastructures import URL

    request = MagicMock()
    request.headers = {"HX-Request": "true"} if htmx else {}
    request.url = URL(url)
    return request


def test_resolve_query_flash_resolves_shared_key_absent_from_route_dict():
    """A §32 fallback key lives in SHARED_FLASH_MESSAGES, not the route-local dict."""
    flash_msg, _ = resolve_query_flash(_req(), {"archived": ("success", "Archived.")}, "saved")
    assert flash_msg is not None
    assert flash_msg["level"] == SHARED_FLASH_MESSAGES["saved"][0]
    assert flash_msg["body"] == SHARED_FLASH_MESSAGES["saved"][1]


def test_resolve_query_flash_route_dict_wins_over_shared():
    """A route-local key of the same name must take precedence over the shared one."""
    flash_msg, _ = resolve_query_flash(_req(), {"saved": ("info", "Route-specific.")}, "saved")
    assert flash_msg == {"level": "info", "body": "Route-specific."}


def test_resolve_query_flash_unknown_key_returns_none():
    flash_msg, headers = resolve_query_flash(_req(), {}, "not-a-real-key")
    assert flash_msg is None
    assert headers == {}


def test_resolve_query_flash_shared_key_strips_param_on_non_htmx():
    _, headers = resolve_query_flash(_req(htmx=False), {}, "removed")
    assert "HX-Replace-Url" in headers
    assert "flash" not in headers["HX-Replace-Url"]


# --- parse_validity_fields (shared admin-form validity parser) ---------------


def test_parse_validity_fields_valid_dates():
    errors = {}
    vf, vu = parse_validity_fields("2001-01-01", "2020-12-31", errors)
    assert vf.isoformat() == "2001-01-01"
    assert vu.isoformat() == "2020-12-31"
    assert errors == {}


def test_parse_validity_fields_blank_returns_none_without_error():
    errors = {}
    assert parse_validity_fields("", "  ", errors) == (None, None)
    assert errors == {}


def test_parse_validity_fields_malformed_date_records_error():
    errors = {}
    vf, _ = parse_validity_fields("not-a-date", "", errors)
    assert vf is None
    assert "valid_from" in errors


def test_parse_validity_fields_inverted_range_records_error():
    errors = {}
    parse_validity_fields("2020-01-01", "2010-01-01", errors)
    assert "valid_until" in errors


def test_parse_validity_fields_one_sided_window_is_ok():
    errors = {}
    vf, vu = parse_validity_fields("1990-05-01", "", errors)
    assert vf.isoformat() == "1990-05-01"
    assert vu is None
    assert errors == {}
