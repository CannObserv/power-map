"""Admin input Postgres cannot store never 500s (#663).

Every admin route, path and query and form alike, derived from ``app.routes``.
A non-HTMX request gets the standard JSON 422; an HTMX request gets the admin's
form-error shape (``_names_shared._form_error_response``): 200, a warning
flash, and ``HX-Reswap: none`` so the page stays put.

Over real HTTP a lone surrogate cannot reach a path, query or form (each is
decoded with ``errors="replace"``), so NUL is the value swept here.
"""

import json
import logging

import pytest
import pytest_asyncio
from fastapi.dependencies.utils import get_flat_dependant
from fastapi.routing import APIRoute
from httpx import ASGITransport, AsyncClient
from starlette.convertors import StringConvertor

from src.api.admin.deps import get_db
from src.api.main import app
from src.api.validation import reject_unstorable_text

pytestmark = pytest.mark.integration

AUTH_HEADERS = {"X-ExeDev-UserID": "usr_test", "X-ExeDev-Email": "admin@test.com"}
HTMX_HEADERS = {**AUTH_HEADERS, "HX-Request": "true"}

_NUL = "x\x00y"
_DUMMY_ID = "01J00000000000000000000000"
_ADMIN = [r for r in app.routes if isinstance(r, APIRoute) and r.path.startswith("/admin")]


def _text_params(route: APIRoute) -> list[str]:
    """Path params that can carry text (an ``:int`` param 404s on it instead)."""
    return [n for n, c in route.param_convertors.items() if isinstance(c, StringConvertor)]


def _fill(route: APIRoute, bad: str | None = None) -> str:
    path = route.path
    for name, convertor in route.param_convertors.items():
        value = "1" if not isinstance(convertor, StringConvertor) else _DUMMY_ID
        if name == bad:
            value = "x%00y"
        path = path.replace("{" + name + "}", value)
        path = path.replace("{" + name + ":int}", value)
    return path


def _path_cases():
    for route in _ADMIN:
        for method in sorted(route.methods):
            for name in _text_params(route):
                yield pytest.param(route, method, name, id=f"{method} {route.path} [{name}]")


def _query_cases():
    for route in _ADMIN:
        names = [f.alias for f in get_flat_dependant(route.dependant).query_params] or ["probe"]
        for method in sorted(route.methods):
            for name in names:
                yield pytest.param(route, method, name, id=f"{method} {route.path} ?{name}")


def _form_cases():
    for route in _ADMIN:
        fields = [f.alias for f in get_flat_dependant(route.dependant).body_params]
        if not fields:
            continue
        for method in sorted(route.methods):
            yield pytest.param(route, method, fields, id=f"{method} {route.path}")


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
    """AsyncClient over the app; get_db yields the rolled-back test connection."""

    async def _get_db_override():
        yield db

    app.dependency_overrides[get_db] = _get_db_override
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c
    app.dependency_overrides.pop(get_db, None)


def _assert_json_422(r, loc: list):
    assert r.status_code == 422, (r.status_code, r.text[:300])
    detail = r.json()["detail"]
    assert isinstance(detail, list), detail
    assert detail[0]["type"] == "string_unstorable", detail
    assert detail[0]["loc"] == loc, detail


@pytest.mark.parametrize(("route", "method", "name"), list(_path_cases()))
async def test_nul_in_admin_path_param_is_422(client, route, method, name):
    r = await client.request(method, _fill(route, bad=name), headers=AUTH_HEADERS)
    _assert_json_422(r, ["path", name])


@pytest.mark.parametrize(("route", "method", "name"), list(_query_cases()))
async def test_nul_in_admin_query_param_is_422(client, route, method, name):
    r = await client.request(method, _fill(route), params={name: _NUL}, headers=AUTH_HEADERS)
    _assert_json_422(r, ["query", name])


@pytest.mark.parametrize(("route", "method", "fields"), list(_form_cases()))
async def test_nul_in_admin_form_field_is_422(client, route, method, fields):
    r = await client.request(
        method, _fill(route), data={f: _NUL for f in fields}, headers=AUTH_HEADERS
    )
    _assert_json_422(r, ["body", fields[0]])


def test_sweep_covers_every_admin_route():
    assert {p.values[0].path for p in _query_cases()} == {r.path for r in _ADMIN}


# ---------------------------------------------------------------------------
# HTMX: the admin's form-error shape, not a JSON body htmx would drop
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("method", "url", "data"),
    [
        pytest.param("GET", "/admin/orgs/search/?q=x%00y", None, id="org search query"),
        pytest.param("GET", f"/admin/orgs/{_DUMMY_ID[:-1]}%00/", None, id="org detail path"),
        pytest.param("POST", "/admin/orgs/new/", {"name": _NUL}, id="org create form"),
    ],
)
async def test_htmx_request_gets_a_warning_flash(client, method, url, data):
    r = await client.request(method, url, data=data, headers=HTMX_HEADERS)
    assert r.status_code == 200, (r.status_code, r.text[:300])
    assert r.headers["HX-Reswap"] == "none"
    flash = json.loads(r.headers["HX-Trigger"])["showFlash"]
    assert flash["level"] == "warning"
    assert "cannot be stored" in flash["body"]
    assert r.text == ""


async def test_boosted_request_gets_json_422(client):
    # is_htmx() excludes boosted navigation: a boosted page load is a page load.
    r = await client.get(
        "/admin/orgs/search/?q=x%00y", headers={**HTMX_HEADERS, "HX-Boosted": "true"}
    )
    _assert_json_422(r, ["query", "q"])


# ---------------------------------------------------------------------------
# Backstop: with the rule switched off, the database's refusal still answers 422
# ---------------------------------------------------------------------------


async def test_backstop_answers_through_a_real_route(client, caplog):
    """The asyncpg error crosses get_db's yield and still reaches the handler."""

    async def _rule_off():
        return None

    app.dependency_overrides[reject_unstorable_text] = _rule_off
    try:
        with caplog.at_level(logging.ERROR, logger="src.api.validation"):
            r = await client.get("/admin/orgs/x%00y/", headers=AUTH_HEADERS)
    finally:
        app.dependency_overrides.pop(reject_unstorable_text, None)
    _assert_json_422_loc_empty(r)
    assert any(rec.levelno == logging.ERROR for rec in caplog.records), caplog.text


def _assert_json_422_loc_empty(r):
    assert r.status_code == 422, (r.status_code, r.text[:300])
    (err,) = r.json()["detail"]
    assert err["type"] == "string_unstorable"
    assert err["loc"] == []
