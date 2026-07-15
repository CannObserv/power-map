"""OpenAPI declares the throttling and conditional-request contract (#292 CR).

Pure unit — inspects ``app.openapi()`` without touching the DB. Every public
route can 429 (the limiter runs in the shared auth dep), so the 429 response
is declared once at the router level; the seven conditional GET endpoints
additionally declare 304.
"""

from src.api.main import app

_CONDITIONAL_GETS = [
    "/api/v1/assignments/{assignment_id}",
    "/api/v1/people/{person_id}",
    "/api/v1/people/{person_id}/events",
    "/api/v1/orgs/{org_id}",
    "/api/v1/orgs/{org_id}/events",
    "/api/v1/roles/{role_id}",
    "/api/v1/jurisdictions/{jurisdiction_id}",
]


def test_all_public_routes_document_429():
    schema = app.openapi()
    missing = [
        f"{method.upper()} {path}"
        for path, ops in schema["paths"].items()
        # `_test/` routes are scaffolding mounted by other test modules (e.g.
        # test_require_scope.py) — not part of the public surface.
        if path.startswith("/api/v1") and "/_test/" not in path
        for method, op in ops.items()
        if "429" not in op.get("responses", {})
    ]
    assert not missing, f"routes missing 429 in OpenAPI: {missing}"


def test_conditional_get_endpoints_document_304():
    schema = app.openapi()
    for path in _CONDITIONAL_GETS:
        responses = schema["paths"][path]["get"]["responses"]
        assert "304" in responses, f"GET {path} missing 304 in OpenAPI"
