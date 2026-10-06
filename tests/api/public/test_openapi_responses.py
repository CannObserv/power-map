"""OpenAPI declares the throttling and conditional-request contract (#292 CR).

Pure unit — inspects ``app.openapi()`` without touching the DB. Every public
route can 429 (the limiter runs in the shared auth dep), so the 429 response
is declared once at the router level; the conditional GET endpoints listed in
``_CONDITIONAL_GETS`` additionally declare 304.
"""

import ast
import inspect
import textwrap

from fastapi.routing import APIRoute

from src.api.main import app

_CONDITIONAL_GETS = [
    "/api/v1/assignments/{assignment_id}",
    "/api/v1/people/{person_id}",
    "/api/v1/people/{person_id}/events",
    "/api/v1/orgs/{org_id}",
    "/api/v1/orgs/{org_id}/events",
    "/api/v1/roles/{role_id}",
    "/api/v1/jurisdictions/{jurisdiction_id}",
    # #392 PR-B — sub-resources (watermark validator) + catalogs (content hash)
    "/api/v1/citations/{entity_type}/{entity_id}",
    "/api/v1/assignments/{pm_assignment_id}/relationships",
    "/api/v1/role-types",
    "/api/v1/link-types",
    "/api/v1/entity-event-types",
    # #459 — the fourth observation vocabulary catalog
    "/api/v1/entity-identifier-types",
    # #392 PR-C — needed jurisdiction_relationships.updated_at first
    "/api/v1/jurisdictions/{jurisdiction_id}/relationships",
    "/api/v1/jurisdictions/{jurisdiction_id}/lineage",
]


def test_all_public_routes_document_429():
    schema = app.openapi()
    missing = [
        f"{method.upper()} {path}"
        for path, ops in schema["paths"].items()
        if path.startswith("/api/v1")
        for method, op in ops.items()
        if "429" not in op.get("responses", {})
    ]
    assert not missing, f"routes missing 429 in OpenAPI: {missing}"


def test_conditional_get_endpoints_document_304():
    schema = app.openapi()
    for path in _CONDITIONAL_GETS:
        responses = schema["paths"][path]["get"]["responses"]
        assert "304" in responses, f"GET {path} missing 304 in OpenAPI"


# ---------------------------------------------------------------------------
# Typed errors (#618): a generated client types every declared error body, and
# returns an undeclared one untyped. So each status a route can answer with is
# declared, with the body it carries.
# ---------------------------------------------------------------------------

_ERROR_DETAIL_REF = "#/components/schemas/ErrorDetail"


def _public_operations():
    for path, ops in app.openapi()["paths"].items():
        if path.startswith("/api/v1"):
            for method, op in ops.items():
                yield f"{method.upper()} {path}", op


def _body_ref(response: dict) -> str | None:
    schema = response.get("content", {}).get("application/json", {}).get("schema", {})
    return schema.get("$ref")


def test_error_detail_schema_is_a_detail_string():
    schema = app.openapi()["components"]["schemas"]["ErrorDetail"]
    assert schema["required"] == ["detail"]
    assert schema["properties"]["detail"]["type"] == "string"


def test_all_public_routes_type_auth_and_throttle_errors():
    """401 (bad key), 403 (no key / missing scope) and 429 come from the shared
    auth dep, so every route can answer with them, each a ``{detail}`` body."""
    wrong = [
        f"{name} {status}"
        for name, op in _public_operations()
        for status in ("401", "403", "429")
        if _body_ref(op["responses"].get(status, {})) != _ERROR_DETAIL_REF
    ]
    assert not wrong, f"auth/throttle errors not typed as ErrorDetail: {wrong}"


def _declared_statuses_by_endpoint() -> dict:
    """endpoint function → (operation name, declared response statuses)."""
    schema = app.openapi()
    out = {}
    for route in app.routes:
        if not isinstance(route, APIRoute) or not route.include_in_schema:
            continue
        if not route.path.startswith("/api/v1"):
            continue
        for method in route.methods:
            op = schema["paths"][route.path][method.lower()]
            out[route.endpoint] = (f"{method} {route.path}", set(op["responses"]))
    return out


def _raised_statuses(func) -> set[str]:
    """Statuses a handler answers with directly: a literal ``HTTPException``
    status, plus 404 and 410 when it calls ``not_found_or_gone``."""
    tree = ast.parse(textwrap.dedent(inspect.getsource(func)))
    found: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
        if name == "HTTPException":
            for kw in node.keywords:
                if kw.arg == "status_code" and isinstance(kw.value, ast.Constant):
                    found.add(str(kw.value.value))
        elif name == "not_found_or_gone":
            found |= {"404", "410"}
    return found


def test_raised_statuses_are_declared():
    """Sweep: a status a handler raises is declared on its operation."""
    missing = []
    for endpoint, (name, declared) in _declared_statuses_by_endpoint().items():
        undeclared = _raised_statuses(endpoint) - declared
        if undeclared:
            missing.append(f"{name}: {sorted(undeclared)}")
    assert not missing, f"raised but not declared in OpenAPI: {missing}"


def test_sweep_sees_raise_sites():
    """Guards the sweep itself: it would pass vacuously if it found nothing."""
    raised = {
        name: _raised_statuses(endpoint)
        for endpoint, (name, _) in _declared_statuses_by_endpoint().items()
    }
    assert {"404", "410"} <= raised["GET /api/v1/orgs/{org_id}"]
    assert {"404", "409", "422"} <= raised["POST /api/v1/people/{person_id}/embeddings"]


def test_not_found_and_conflict_bodies_are_typed():
    wrong = [
        f"{name} {status}"
        for name, op in _public_operations()
        for status in ("404", "409")
        if status in op["responses"] and _body_ref(op["responses"][status]) != _ERROR_DETAIL_REF
    ]
    assert not wrong, f"404/409 not typed as ErrorDetail: {wrong}"


def test_validation_error_detail_may_be_a_string():
    """FastAPI's 422 body is ``{detail: [ValidationError]}``, but a route that
    rejects a request itself raises ``HTTPException(422, detail="...")`` (an
    embedding dimension mismatch, an unknown identifier type). The published
    model admits both, so a generated client parses either."""
    detail = app.openapi()["components"]["schemas"]["HTTPValidationError"]["properties"]["detail"]
    kinds = {branch.get("type") for branch in detail["anyOf"]}
    assert kinds == {"array", "string"}


def test_conditional_gets_accept_if_none_match():
    """A route that can answer 304 publishes the request header that earns it,
    so a generated client exposes it as an argument."""
    schema = app.openapi()
    missing = []
    for path in _CONDITIONAL_GETS:
        params = schema["paths"][path]["get"].get("parameters", [])
        header = [p for p in params if p["in"] == "header" and p["name"] == "If-None-Match"]
        if len(header) != 1 or header[0].get("required", False):
            missing.append(path)
    assert not missing, f"conditional GETs without an optional If-None-Match: {missing}"


def test_only_conditional_gets_accept_if_none_match():
    """The header is derived from the 304 declaration, never added blanket."""
    extra = [
        name
        for name, op in _public_operations()
        if "304" not in op["responses"]
        and any(p["name"] == "If-None-Match" for p in op.get("parameters", []))
    ]
    assert not extra


def test_api_root_is_named_and_typed():
    op = app.openapi()["paths"]["/api/v1/"]["get"]
    assert op["operationId"] == "getApiRoot"
    assert _body_ref(op["responses"]["200"]) == "#/components/schemas/ApiRootResponse"


def test_identify_similarity_is_a_required_number():
    """``IdentifyMatch.similarity`` is never null (#618 item 3).

    A zero-norm vector would make the cosine NaN, which serializes as ``null``.
    Since #299 every embedding that enters the API is rejected with 422 when it
    is zero or non-finite, both as a query and as a write, so the published
    type stays a required, non-null number. ``VerifyResult.similarity`` is
    nullable on purpose: null there means "no enrollment".
    """
    match = app.openapi()["components"]["schemas"]["IdentifyMatch"]
    assert "similarity" in match["required"]
    assert match["properties"]["similarity"]["type"] == "number"
