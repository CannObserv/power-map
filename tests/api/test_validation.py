"""The request-validation boundary (#663, #299). Pure unit: no DB.

The unstorable-text rule runs ahead of every route's own dependencies, so these
requests are answered before ``get_db`` is ever called: a mocked ``get_db``
proves it.
"""

import json
import logging
import math
from unittest.mock import AsyncMock

import asyncpg
import pytest
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from starlette.requests import Request

from src.api.deps import get_db
from src.api.main import app
from src.api.public.deps import require_api_key
from src.api.validation import (
    UNSTORABLE_TYPE,
    reject_unstorable_text,
    unstorable_text_backstop,
    validation_error_response,
)


@pytest.fixture
def client():
    """Lifespan-less client whose get_db must never be reached."""
    db = AsyncMock()

    async def _db():
        yield db

    app.dependency_overrides[get_db] = _db
    try:
        yield TestClient(app, raise_server_exceptions=False), db
    finally:
        app.dependency_overrides.pop(get_db, None)


def _request(path: str = "/api/v1/orgs/x", headers: dict | None = None) -> Request:
    raw = [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()]
    return Request(
        {"type": "http", "method": "GET", "path": path, "headers": raw, "query_string": b""}
    )


# ---------------------------------------------------------------------------
# Every route carries the rule
# ---------------------------------------------------------------------------


def test_every_route_carries_the_rule():
    missing = [
        f"{sorted(r.methods)} {r.path}"
        for r in app.routes
        if isinstance(r, APIRoute)
        and reject_unstorable_text not in {d.call for d in r.dependant.dependencies}
    ]
    assert not missing, missing


def test_the_rule_runs_before_the_routes_own_dependencies():
    for r in app.routes:
        if isinstance(r, APIRoute) and r.dependant.dependencies:
            assert r.dependant.dependencies[0].call is reject_unstorable_text, r.path


# ---------------------------------------------------------------------------
# Each surface, through the real app
# ---------------------------------------------------------------------------


def _assert_422(r, loc):
    assert r.status_code == 422, r.text
    (err,) = r.json()["detail"]
    assert err["type"] == UNSTORABLE_TYPE
    assert err["loc"] == loc
    assert "cannot be stored" in err["msg"]
    return err


def test_path_param(client):
    c, db = client
    err = _assert_422(c.get("/api/v1/orgs/x%00y"), ["path", "org_id"])
    assert err["input"] == "x\\u0000y"
    db.assert_not_called()


def test_query_param(client):
    c, _ = client
    _assert_422(c.get("/api/v1/orgs/search", params={"q": "x\x00y"}), ["query", "q"])


def test_undeclared_query_param(client):
    c, _ = client
    _assert_422(c.get("/api/v1/role-types", params={"junk": "\x00"}), ["query", "junk"])


def test_query_param_name(client):
    c, _ = client
    _assert_422(c.get("/api/v1/role-types?a%00b=1"), ["query", "a\\u0000b"])


def test_json_body_escape(client):
    c, _ = client
    r = c.post(
        "/api/v1/orgs/batch-get",
        content=b'{"ids": ["ok", "\\ud800"]}',
        headers={"content-type": "application/json"},
    )
    assert _assert_422(r, ["body", "ids", 1])["input"] == "\\ud800"


def test_plus_json_content_type(client):
    c, _ = client
    r = c.post(
        "/api/v1/orgs/batch-get",
        content=b'{"ids": ["\\u0000"]}',
        headers={"content-type": "application/vnd.probe+json; charset=utf-8"},
    )
    _assert_422(r, ["body", "ids", 0])


def test_form_body(client):
    c, _ = client
    r = c.post("/admin/orgs/new/", data={"name": "x\x00y"})
    _assert_422(r, ["body", "name"])


def test_multipart_form_body(client):
    c, _ = client
    r = c.post("/admin/orgs/new/", files={"name": (None, b"x\x00y")})
    _assert_422(r, ["body", "name"])


@pytest.mark.parametrize(
    "body",
    [
        b'{"ids": ["\\ud83d\\ude00"]}',  # a well-formed pair
        b'{"ids": ["\\\\u0000"]}',  # a literal backslash-u
        b'{"ids": ["\\u00e9"]}',
    ],
)
def test_storable_lookalikes_reach_the_route(client, body, monkeypatch):
    c, db = client
    db.fetch.return_value = []  # no org rows, no tombstones: every id is not_found
    monkeypatch.setitem(app.dependency_overrides, require_api_key, lambda: "key")
    r = c.post("/api/v1/orgs/batch-get", content=body, headers={"content-type": "application/json"})
    assert r.status_code == 200, r.text
    assert [x["outcome"] for x in r.json()["results"]] == ["not_found"]


def test_body_of_a_route_that_reads_none_is_not_checked(client):
    # A route with no body field never reads the body, so its text cannot reach
    # the DB; parsing it anyway would leak a multipart form's spooled files.
    c, _ = client
    r = c.post(
        f"/api/v1/people/{'0' * 26}/embeddings/{'0' * 26}/restore?model_id=m",
        content=b'{"probe": "\\u0000"}',
        headers={"content-type": "application/json"},
    )
    assert r.status_code == 403  # reached auth (no key): the rule let it through


def test_admin_htmx_gets_a_flash(client):
    c, _ = client
    r = c.get("/admin/orgs/search/?q=x%00y", headers={"HX-Request": "true"})
    assert r.status_code == 200
    assert r.headers["HX-Reswap"] == "none"
    assert json.loads(r.headers["HX-Trigger"])["showFlash"]["level"] == "warning"


def test_public_htmx_header_is_ignored(client):
    # The flash shape is the admin's: a public caller always gets JSON.
    c, _ = client
    r = c.get("/api/v1/orgs/x%00y", headers={"HX-Request": "true"})
    _assert_422(r, ["path", "org_id"])


# ---------------------------------------------------------------------------
# The 422 always renders
# ---------------------------------------------------------------------------


def test_validation_response_escapes_unstorable_echoes():
    exc = RequestValidationError(
        [{"type": "missing", "loc": ("body", "k\x00"), "msg": "m", "input": {"a": "\ud800"}}]
    )
    r = validation_error_response(exc)
    body = json.loads(r.body)
    assert body["detail"][0]["input"] == {"a": "\\ud800"}
    assert body["detail"][0]["loc"] == ["body", "k\\u0000"]


def test_validation_response_keeps_nonfinite_floats_renderable():
    exc = RequestValidationError(
        [{"type": "x", "loc": ("body",), "msg": "m", "input": [math.nan, math.inf]}]
    )
    assert json.loads(validation_error_response(exc).body)["detail"][0]["input"] == ["nan", "inf"]


# ---------------------------------------------------------------------------
# Backstop: the database refused the text, so the rule missed a surface
# ---------------------------------------------------------------------------


def _surrogate_data_error() -> asyncpg.exceptions.DataError:
    exc = asyncpg.exceptions.DataError("invalid input for query argument $1")
    try:
        "\ud800".encode()
    except UnicodeEncodeError as cause:
        exc.__cause__ = cause
    return exc


@pytest.mark.parametrize(
    "exc",
    [
        asyncpg.exceptions.CharacterNotInRepertoireError("invalid byte sequence: 0x00"),
        _surrogate_data_error(),
    ],
    ids=["nul", "surrogate"],
)
async def test_backstop_answers_422_and_logs_an_error(exc, caplog):
    with caplog.at_level(logging.ERROR, logger="src.api.validation"):
        r = await unstorable_text_backstop(_request("/api/v1/orgs/x\x00"), exc)
    assert r.status_code == 422
    (err,) = json.loads(r.body)["detail"]
    assert err["type"] == UNSTORABLE_TYPE
    assert err["loc"] == []
    (record,) = [r for r in caplog.records if r.name == "src.api.validation"]
    assert record.levelno == logging.ERROR
    assert "x\\u0000" in record.getMessage()


async def test_backstop_flashes_for_admin_htmx():
    exc = asyncpg.exceptions.CharacterNotInRepertoireError("0x00")
    r = await unstorable_text_backstop(_request("/admin/orgs/", {"HX-Request": "true"}), exc)
    assert r.status_code == 200
    assert r.headers["HX-Reswap"] == "none"


async def test_backstop_reraises_other_data_errors():
    exc = asyncpg.exceptions.NumericValueOutOfRangeError("out of range")
    with pytest.raises(asyncpg.exceptions.NumericValueOutOfRangeError):
        await unstorable_text_backstop(_request(), exc)


def test_backstop_is_registered():
    assert app.exception_handlers[asyncpg.exceptions.DataError] is unstorable_text_backstop
