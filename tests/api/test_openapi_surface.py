"""The published OpenAPI schema is the public contract (#618).

``/openapi.json`` is what a generated client is built from, so it lists the
public surface (``/api/v1/*``) and ``/health`` only. ``/health`` carries the
deployed ``build`` a consumer compares with its pinned client version. The admin
dashboard and ``/ready`` (the operator's readiness probe, #343/#347) are still
served but are left out of the schema. Pure unit: no DB, no network.
"""

from fastapi.routing import APIRoute

from src.api.main import app


def _schema_paths() -> set[str]:
    return set(app.openapi()["paths"])


def test_schema_lists_only_public_routes_and_health():
    stray = sorted(p for p in _schema_paths() if not p.startswith("/api/v1/") and p != "/health")
    assert not stray, f"non-public paths in the published schema: {stray}"


def test_schema_keeps_health():
    assert "/health" in _schema_paths()


def test_schema_omits_ready():
    assert "/ready" not in _schema_paths()


def test_schema_omits_admin():
    assert not [p for p in _schema_paths() if p.startswith("/admin")]


def test_admin_and_ready_are_still_routed():
    """Out of the schema is not out of the app: both stay served."""
    routed = {r.path for r in app.routes if isinstance(r, APIRoute)}
    assert "/ready" in routed
    assert any(p.startswith("/admin") for p in routed)


def test_every_public_route_is_in_the_schema():
    """No ``/api/v1`` route is hidden by accident."""
    routed = {
        r.path
        for r in app.routes
        if isinstance(r, APIRoute) and r.path.startswith("/api/v1/") and "/_test/" not in r.path
    }
    assert routed <= _schema_paths()
