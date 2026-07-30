"""Tests for the unauthenticated liveness/readiness probes (#343).

Pure-unit route tests: ``TestClient(app)`` without ``with`` so the lifespan
(real pool creation) never runs — which also proves ``/health`` needs no DB.
``/ready`` tests patch ``db.check_ready`` at the module attribute.
"""

from importlib.metadata import version
from unittest.mock import AsyncMock, patch

import asyncpg
from fastapi.testclient import TestClient

import src.core.db as db
from src.api.main import app

client = TestClient(app)

# ---------------------------------------------------------------------------
# /health — liveness
# ---------------------------------------------------------------------------


def test_health_returns_ok_and_build():
    """Liveness returns 200 with the package version as build id — no auth, no DB.

    No lifespan ran, so no pool exists: a 200 here proves /health makes no
    external calls.
    """
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok", "build": version("power-map")}


def test_app_version_derived_from_package():
    """FastAPI version metadata tracks pyproject via importlib.metadata.

    Guards the stale-copy bug: main.py hardcoded ``version="0.1.0"`` while
    pyproject was at 0.16.0 — the check-version-sync hook never covered it.
    """
    assert app.version == version("power-map")


# ---------------------------------------------------------------------------
# /ready — readiness
# ---------------------------------------------------------------------------


def test_ready_ok_when_pool_healthy():
    with patch.object(db, "check_ready", AsyncMock(return_value=None)) as probe:
        resp = client.get("/ready")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}
    probe.assert_awaited_once()


def test_ready_503_no_pool():
    """No pool (DATABASE_URL unset / pre-lifespan) → 503, not a 500."""
    with patch.object(db, "check_ready", AsyncMock(side_effect=RuntimeError("no pool"))):
        resp = client.get("/ready")
    assert resp.status_code == 503
    assert resp.json() == {"status": "unavailable", "reason": "no_pool"}


def test_ready_503_pool_timeout():
    """Exhausted pool surfaces as a distinct 503 reason instead of hanging."""
    with patch.object(db, "check_ready", AsyncMock(side_effect=TimeoutError())):
        resp = client.get("/ready")
    assert resp.status_code == 503
    assert resp.json() == {"status": "unavailable", "reason": "pool_timeout"}


def test_ready_503_db_error_is_generic():
    """DB failure → 503 with a generic slug; no error detail leaks unauthenticated."""
    err = asyncpg.PostgresError("FATAL: password authentication failed for user 'x'")
    with patch.object(db, "check_ready", AsyncMock(side_effect=err)):
        resp = client.get("/ready")
    assert resp.status_code == 503
    assert resp.json() == {"status": "unavailable", "reason": "db_error"}
    assert "password" not in resp.text
