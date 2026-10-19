"""The request-validation boundary (#663, #299). Pure unit: no DB.

The unstorable-text rule runs ahead of every route's own dependencies, so these
requests are answered before ``get_db`` is ever called: a mocked ``get_db``
proves it.
"""

import json
import logging
import math
from unittest.mock import AsyncMock
from urllib.parse import unquote

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
    TOO_DEEP_INPUT,
    TOO_DEEP_TYPE,
    UNSTORABLE_TYPE,
    reject_unstorable_text,
    unstorable_text_backstop,
    validation_error_response,
)
from src.core.json_depth import MAX_JSON_DEPTH
from src.core.storable_text import escape_unstorable_deep


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


def test_form_field_name(client):
    c, _ = client
    r = c.post(
        "/admin/orgs/new/",
        content=b"x%00y=1&name=ok",
        headers={"content-type": "application/x-www-form-urlencoded"},
    )
    assert _assert_422(r, ["body", "x\\u0000y"])["input"] == "x\\u0000y"


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


async def test_percent_encoded_surrogate_arrives_as_replacement_chars():
    # %ED%A0%80 is U+D800 as UTF-8 bytes. uvicorn decodes the path with
    # urllib.parse.unquote and Starlette the query with parse_qsl, both
    # errors="replace", so the app sees U+FFFD x3: storable text the rule passes.
    path_value = unquote("%ED%A0%80")
    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": f"/api/v1/orgs/{path_value}",
            "path_params": {"org_id": path_value},
            "headers": [],
            "query_string": b"q=%ED%A0%80",
        }
    )
    assert path_value == request.query_params["q"] == "\ufffd" * 3
    await reject_unstorable_text(request)  # does not raise


@pytest.mark.parametrize("url", ["/api/v1/orgs/%ED%A0%80", "/api/v1/orgs/search?q=%ED%A0%80"])
def test_percent_encoded_surrogate_passes_the_rule(client, url):
    c, _ = client
    assert c.get(url).status_code == 403  # reached auth (no key): the rule let it through


def test_public_htmx_header_is_ignored(client):
    # The flash shape is the admin's: a public caller always gets JSON.
    c, _ = client
    r = c.get("/api/v1/orgs/x%00y", headers={"HX-Request": "true"})
    _assert_422(r, ["path", "org_id"])


# ---------------------------------------------------------------------------
# A JSON body nested too deep to walk (#674)
# ---------------------------------------------------------------------------

_BATCH = "/api/v1/orgs/batch-get"
_JSON = {"content-type": "application/json"}
_LEAVES = {"ok": b'"ok"', "nul": b'"\\u0000"'}


def _deep_body(depth: int, leaf: bytes) -> bytes:
    """``{"ids": [[…leaf…]]}``: ``leaf`` in ``depth`` lists, ``depth + 1`` containers in all."""
    return b'{"ids": ' + b"[" * depth + leaf + b"]" * depth + b"}"


def _expected(depth: int, leaf: str, keyed: bool) -> str | int:
    if depth + 1 > MAX_JSON_DEPTH:
        return TOO_DEEP_TYPE  # the rule runs before auth and before the unstorable walk
    if leaf == "nul":
        return UNSTORABLE_TYPE
    return "string_type" if keyed else 403  # Pydantic runs after auth


@pytest.mark.parametrize("keyed", [False, True], ids=["no-key", "key"])
@pytest.mark.parametrize("leaf", sorted(_LEAVES))
@pytest.mark.parametrize("depth", [10, 500, 971, 972, 5000])
def test_depth_sweep_never_5xx(client, monkeypatch, depth, leaf, keyed):
    # From 972 levels jsonable_encoder recursed past the limit echoing the
    # input; with a NUL leaf, find_unstorable did so from 971, before auth.
    c, db = client
    if keyed:
        monkeypatch.setitem(app.dependency_overrides, require_api_key, lambda: "key")
    r = c.post(_BATCH, content=_deep_body(depth, _LEAVES[leaf]), headers=_JSON)
    expected = _expected(depth, leaf, keyed)
    if expected == 403:
        assert r.status_code == 403, (r.status_code, r.text[:300])
        return
    assert r.status_code == 422, (r.status_code, r.text[:300])
    (err,) = r.json()["detail"]  # the 422 renders: it parses as JSON
    assert err["type"] == expected
    db.assert_not_called()


@pytest.mark.parametrize("leaf", sorted(_LEAVES))
def test_body_at_the_bound_passes_the_rule(client, monkeypatch, leaf):
    c, _ = client
    monkeypatch.setitem(app.dependency_overrides, require_api_key, lambda: "key")
    r = c.post(_BATCH, content=_deep_body(MAX_JSON_DEPTH - 1, _LEAVES[leaf]), headers=_JSON)
    assert r.status_code == 422, r.text
    (err,) = r.json()["detail"]
    assert err["type"] == _expected(MAX_JSON_DEPTH - 1, leaf, keyed=True)


def test_body_past_the_bound_is_json_too_deep(client):
    c, db = client
    r = c.post(_BATCH, content=_deep_body(MAX_JSON_DEPTH, b'"ok"'), headers=_JSON)
    assert r.status_code == 422, r.text  # not 403: no key, and the rule ran first
    assert r.json()["detail"] == [
        {
            "type": TOO_DEEP_TYPE,
            "loc": ["body"],
            "msg": f"JSON body is nested deeper than {MAX_JSON_DEPTH} levels",
            "input": {},
            "ctx": {"max_depth": MAX_JSON_DEPTH},
        }
    ]
    db.assert_not_called()


def test_plus_json_body_past_the_bound_is_json_too_deep(client):
    c, _ = client
    r = c.post(
        _BATCH,
        content=_deep_body(MAX_JSON_DEPTH, b'"ok"'),
        headers={"content-type": "application/vnd.probe+json"},
    )
    assert r.json()["detail"][0]["type"] == TOO_DEEP_TYPE


def test_brackets_inside_strings_reach_the_route(client, monkeypatch):
    # The byte pre-screen's candidate is confirmed on the parsed body.
    c, db = client
    db.fetch.return_value = []
    monkeypatch.setitem(app.dependency_overrides, require_api_key, lambda: "key")
    body = json.dumps({"ids": ["[" * (MAX_JSON_DEPTH + 1) + "{" * 10]}).encode()
    r = c.post(_BATCH, content=body, headers=_JSON)
    assert r.status_code == 200, r.text
    assert [x["outcome"] for x in r.json()["results"]] == ["not_found"]


def _schema_depth(schema: dict, components: dict, seen: frozenset = frozenset()) -> float:
    """Containers a JSON value of ``schema`` can nest; ``inf`` when unbounded."""
    if "$ref" in schema:
        name = schema["$ref"].rsplit("/", 1)[-1]
        if name in seen:
            return math.inf
        return _schema_depth(components[name], components, seen | {name})
    variants = [s for k in ("anyOf", "oneOf", "allOf") for s in schema.get(k, [])]
    depth = max((_schema_depth(s, components, seen) for s in variants), default=0)
    if "items" in schema:
        depth = max(depth, 1 + _schema_depth(schema["items"], components, seen))
    if schema.get("type") == "object" or "properties" in schema:
        extra = schema.get("additionalProperties")
        inner = [_schema_depth(s, components, seen) for s in schema.get("properties", {}).values()]
        inner.append(math.inf if extra is True else _schema_depth(extra or {}, components, seen))
        depth = max(depth, 1 + max(inner))
    return depth


def test_every_declared_json_body_fits_the_bound():
    # The bound rejects only bodies no route accepts (deepest today: 5). A
    # schema past it, or a recursive one, needs the bound revisited first.
    spec = app.openapi()
    depths = {
        f"{method.upper()} {path}": _schema_depth(media["schema"], spec["components"]["schemas"])
        for path, ops in spec["paths"].items()
        for method, op in ops.items()
        for content_type, media in op.get("requestBody", {}).get("content", {}).items()
        if "json" in content_type
    }
    assert depths, "no JSON bodies found: the walk is broken"
    assert {k: d for k, d in depths.items() if d > MAX_JSON_DEPTH} == {}


def test_body_too_deep_to_parse_is_fastapis_400(client):
    # Accepted limitation: past json.loads's own limit (9997) FastAPI fails the
    # parse itself, before any dependency runs. A 4xx all the same.
    c, _ = client
    r = c.post(_BATCH, content=_deep_body(10_000, b'"\\u0000"'), headers=_JSON)
    assert r.status_code == 400, r.text


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


def _nested(depth: int) -> object:
    value: object = "\x00"
    for _ in range(depth):
        value = [value]
    return value


@pytest.mark.parametrize("depth", [MAX_JSON_DEPTH + 1, 972, 5000])
def test_validation_response_replaces_a_too_deep_echo(depth):
    # The backstop for whatever the boundary rule misses (#674).
    exc = RequestValidationError(
        [{"type": "string_type", "loc": ("body", "ids", 0), "msg": "m", "input": _nested(depth)}]
    )
    (err,) = json.loads(validation_error_response(exc).body)["detail"]
    assert err["input"] == TOO_DEEP_INPUT
    assert err["loc"] == ["body", "ids", 0]


def test_validation_response_echoes_input_at_the_bound():
    exc = RequestValidationError(
        [{"type": "x", "loc": ("body",), "msg": "m", "input": _nested(MAX_JSON_DEPTH)}]
    )
    echoed = json.loads(validation_error_response(exc).body)["detail"][0]["input"]
    assert json.dumps(echoed) == json.dumps(escape_unstorable_deep(_nested(MAX_JSON_DEPTH)))


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
