"""Text Postgres cannot store answers 422 on every public input surface (#663).

NUL (U+0000) and a lone surrogate (U+D800–U+DFFF) once reached asyncpg and
failed the request with a 500. The rule: the API rejects such text at the
boundary with the standard validation body, wherever it arrives.

The sweep derives its cases from ``app.routes``, so a new route is covered
without editing this file. Over real HTTP a lone surrogate can only arrive in a
JSON body (uvicorn and Starlette decode the path, query and forms with
``errors="replace"``), so the path and query cases carry NUL only.

The key carries every scope so a case reaches the route's SQL rather than its
scope gate: without the rule, the sweep is the #663 probe.
"""

import hashlib
import json
import os

import pytest
import pytest_asyncio
from fastapi.dependencies.utils import get_flat_dependant
from fastapi.routing import APIRoute

from src.api.main import app
from src.core.db import generate_id
from src.core.embedding_registry import EmbeddingRegistry, ModelMeta

pytestmark = pytest.mark.integration

_NUL = "x\x00y"
_SURROGATE = "\ud800"
_DUMMY_ID = "01J00000000000000000000000"
_PUBLIC = [r for r in app.routes if isinstance(r, APIRoute) and not r.path.startswith("/admin")]


def _path_cases():
    for route in _PUBLIC:
        for method in sorted(route.methods):
            for name in route.param_convertors:
                yield pytest.param(route, method, name, id=f"{method} {route.path} [{name}]")


def _query_cases():
    for route in _PUBLIC:
        names = [f.alias for f in get_flat_dependant(route.dependant).query_params] or ["probe"]
        for method in sorted(route.methods):
            for name in names:
                yield pytest.param(route, method, name, id=f"{method} {route.path} ?{name}")


def _body_cases():
    for route in _PUBLIC:
        if route.body_field is None:
            continue
        for method in sorted(route.methods):
            for label, text in (("nul", _NUL), ("surrogate", _SURROGATE)):
                yield pytest.param(route, method, text, id=f"{method} {route.path} {label}")


def _fill(route: APIRoute, bad: str | None = None) -> str:
    """The route's path with every param filled; ``bad`` gets a NUL (``%00``)."""
    path = route.path
    for name in route.param_convertors:
        value = "x%00y" if name == bad else _DUMMY_ID
        if name == "entity_type":
            value = "x%00y" if name == bad else "person"
        path = path.replace("{" + name + "}", value)
    return path


def _json_bytes(payload) -> bytes:
    """ASCII JSON, so a lone surrogate travels as its ``\\ud800`` escape."""
    return json.dumps(payload).encode("ascii")


def _assert_unstorable_422(r, loc: list):
    assert r.status_code == 422, (r.status_code, r.text[:300])
    detail = r.json()["detail"]  # the 422 renders: it parses as JSON
    assert isinstance(detail, list), detail
    assert detail[0]["type"] == "string_unstorable", detail
    assert detail[0]["loc"] == loc, detail
    json.dumps(detail, ensure_ascii=False).encode("utf-8")


@pytest_asyncio.fixture(loop_scope="session")
async def all_scopes_key(db):
    """A key holding every scope, so no case stops at a scope gate."""
    uid, kid = generate_id(), generate_id()
    raw = "pm_" + os.urandom(16).hex()
    await db.execute("INSERT INTO app_users (id, email) VALUES ($1,$2)", uid, "nul663@test.com")
    await db.execute(
        "INSERT INTO api_keys (id, user_id, label, key_prefix, key_hash) VALUES ($1,$2,$3,$4,$5)",
        kid,
        uid,
        "Unstorable Probe Key",
        raw[:8],
        hashlib.sha256(raw.encode()).hexdigest(),
    )
    await db.execute(
        "INSERT INTO api_key_scopes (api_key_id, scope_id) SELECT $1, id FROM api_key_scope_types",
        kid,
    )
    return raw


# ---------------------------------------------------------------------------
# Sweep: every route, every surface
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(("route", "method", "name"), list(_path_cases()))
async def test_nul_in_path_param_is_422(client, all_scopes_key, route, method, name):
    r = await client.request(method, _fill(route, bad=name), headers={"X-API-Key": all_scopes_key})
    _assert_unstorable_422(r, ["path", name])


@pytest.mark.parametrize(("route", "method", "name"), list(_query_cases()))
async def test_nul_in_query_param_is_422(client, all_scopes_key, route, method, name):
    r = await client.request(
        method, _fill(route), params={name: _NUL}, headers={"X-API-Key": all_scopes_key}
    )
    _assert_unstorable_422(r, ["query", name])


@pytest.mark.parametrize(("route", "method", "text"), list(_body_cases()))
async def test_unstorable_text_in_json_body_is_422(client, all_scopes_key, route, method, text):
    r = await client.request(
        method,
        _fill(route),
        content=_json_bytes({"probe": text}),
        headers={"X-API-Key": all_scopes_key, "content-type": "application/json"},
    )
    _assert_unstorable_422(r, ["body", "probe"])


def test_sweep_covers_every_public_route():
    # A route with no path, query or body input still gets the undeclared-query case.
    swept = {p.values[0].path for p in _query_cases()}
    assert swept == {r.path for r in _PUBLIC}


# ---------------------------------------------------------------------------
# Real bodies: the requests that used to reach SQL with the bad text
# ---------------------------------------------------------------------------

_OBSERVATION_BODIES = [
    pytest.param(
        "/api/v1/orgs/observations",
        {"identifier_type": "org_ubi", "identifier_value": "{text}"},
        ["body", "identifier_value"],
        id="org observation identifier",
    ),
    pytest.param(
        "/api/v1/people/observations",
        {
            "identifier_type": "person_wa_pdc",
            "identifier_value": "nul663-person",
            "names": [{"name": "{text}", "name_type": "legal"}],
        },
        ["body", "names", 0, "name"],
        id="person observation name",
    ),
    pytest.param(
        "/api/v1/orgs/batch-get",
        {"ids": ["01J00000000000000000000001", "{text}"]},
        ["body", "ids", 1],
        id="batch-get id",
    ),
    pytest.param(
        "/api/v1/orgs/batch-get",
        {"ids": ["a"], "etags": {"{text}": "W/x"}},
        None,  # a key: the loc ends in the escaped key itself
        id="batch-get etags key",
    ),
]


def _substitute(obj, text: str):
    if isinstance(obj, str):
        return obj.replace("{text}", text)
    if isinstance(obj, dict):
        return {_substitute(k, text): _substitute(v, text) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_substitute(v, text) for v in obj]
    return obj


@pytest.mark.parametrize("text", [_NUL, _SURROGATE], ids=["nul", "surrogate"])
@pytest.mark.parametrize(("url", "body", "loc"), _OBSERVATION_BODIES)
async def test_real_body_with_unstorable_text_is_422(client, all_scopes_key, url, body, loc, text):
    r = await client.post(
        url,
        content=_json_bytes(_substitute(body, text)),
        headers={"X-API-Key": all_scopes_key, "content-type": "application/json"},
    )
    if loc is None:
        escaped = "x\\u0000y" if text == _NUL else "\\ud800"
        loc = ["body", "etags", escaped]
    _assert_unstorable_422(r, loc)


async def test_raw_surrogate_bytes_in_json_body_are_422(client, all_scopes_key):
    # json.loads(bytes) decodes with surrogatepass: raw ED A0 80 is a lone surrogate.
    r = await client.post(
        "/api/v1/orgs/batch-get",
        content=b'{"ids": ["\xed\xa0\x80"]}',
        headers={"X-API-Key": all_scopes_key, "content-type": "application/json"},
    )
    _assert_unstorable_422(r, ["body", "ids", 0])


async def test_422_echoes_the_input_escaped(client, all_scopes_key):
    r = await client.post(
        "/api/v1/orgs/batch-get",
        content=_json_bytes({"ids": [_SURROGATE]}),
        headers={"X-API-Key": all_scopes_key, "content-type": "application/json"},
    )
    assert r.status_code == 422
    assert r.json()["detail"][0]["input"] == "\\ud800"


async def test_storable_lookalikes_pass_the_rule(client, all_scopes_key):
    # A surrogate *pair* and a literal backslash-u are storable: the batch answers.
    r = await client.post(
        "/api/v1/orgs/batch-get",
        content=b'{"ids": ["\\ud83d\\ude00", "\\\\u0000", "\\u00e9"]}',
        headers={"X-API-Key": all_scopes_key, "content-type": "application/json"},
    )
    assert r.status_code == 200, r.text
    assert [x["outcome"] for x in r.json()["results"]] == ["not_found"] * 3


async def test_unauthenticated_unstorable_request_is_422_not_401(client):
    # The rule runs ahead of the route's dependencies (auth, get_db): no DB work.
    r = await client.get("/api/v1/orgs/x%00y")
    _assert_unstorable_422(r, ["path", "org_id"])


# ---------------------------------------------------------------------------
# Embedding routes: scope-gated, so #663's probe never reached their SQL
# ---------------------------------------------------------------------------

_MODEL_ID = "pyannote-community-1-embed"
_DIM = 256
_REGISTRY = EmbeddingRegistry(
    {
        _MODEL_ID: ModelMeta(
            model_id=_MODEL_ID,
            table_name="person_embeddings_pyannote_community_1_embed",
            dimension=_DIM,
            metric="cosine",
            accepts_writes=True,
            is_queryable=True,
            operator="<=>",
        )
    }
)
_VECTOR = [1.0] + [0.0] * (_DIM - 1)
_SOURCE = {"service": "probe", "job_id": "j", "segment": 0, "recorded_at": "2026-01-01T00:00:00Z"}

_EMBEDDING_CASES = [
    pytest.param("GET", "/api/v1/people/x%00y/embeddings", None, ["path", "person_id"], id="list"),
    pytest.param(
        "POST",
        "/api/v1/people/x%00y/embeddings",
        {
            "model_id": _MODEL_ID,
            "embedding": _VECTOR,
            "activity_ms": 1,
            "audio_sample_rate_hz": 16000,
            "source": _SOURCE,
        },
        ["path", "person_id"],
        id="write path",
    ),
    pytest.param(
        "POST",
        f"/api/v1/people/{_DUMMY_ID}/embeddings",
        {
            "model_id": _MODEL_ID,
            "embedding": _VECTOR,
            "activity_ms": 1,
            "audio_sample_rate_hz": 16000,
            "source": {**_SOURCE, "job_id": "\ud800"},
        },
        ["body", "source", "job_id"],
        id="write source job_id",
    ),
    pytest.param(
        "POST",
        "/api/v1/people/verify",
        {"model_id": _MODEL_ID, "embedding": _VECTOR, "person_ids": ["\ud800"]},
        ["body", "person_ids", 0],
        id="verify person_ids",
    ),
    pytest.param(
        "POST",
        "/api/v1/people/embeddings/presence",
        {"model_id": _MODEL_ID, "person_ids": ["x\x00y"]},
        ["body", "person_ids", 0],
        id="presence person_ids",
    ),
]


@pytest.mark.parametrize(("method", "url", "body", "loc"), _EMBEDDING_CASES)
async def test_embedding_route_with_unstorable_text_is_422(
    client, all_scopes_key, monkeypatch, method, url, body, loc
):
    monkeypatch.setattr(app.state, "embedding_registry", _REGISTRY)
    r = await client.request(
        method,
        url,
        params={"model_id": _MODEL_ID} if method == "GET" else None,
        content=None if body is None else _json_bytes(body),
        headers={"X-API-Key": all_scopes_key, "content-type": "application/json"},
    )
    _assert_unstorable_422(r, loc)
