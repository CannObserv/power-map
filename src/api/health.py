"""Unauthenticated liveness/readiness probes (#343).

Root-level, outside ``/api/v1`` — ``RequestLogMiddleware`` and the rate
limiter are both ``/api/v1``-scoped, so probes generate no telemetry rows and
burn no rate-limit buckets.

With ``--workers 2`` each uvicorn worker holds its own pool; a probe samples
whichever worker accepted the connection. Fine for uptime checks — not
authoritative across workers.
"""

from importlib.metadata import version

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel

import src.core.db as db
from src.core.logging import get_logger

logger = get_logger(__name__)

# Single source for the app version (feeds FastAPI metadata and /health.build).
# Derived, never hardcoded: main.py carried a stale "0.1.0" copy that the
# check-version-sync hook (pyproject <-> package.json only) could not catch.
APP_VERSION = version("power-map")

health_router = APIRouter(tags=["health"])


class HealthResponse(BaseModel):
    """Liveness payload."""

    status: str
    build: str


class ReadyResponse(BaseModel):
    """Readiness payload."""

    status: str


class NotReadyResponse(BaseModel):
    """Readiness failure payload — generic reason slug, no error detail."""

    status: str
    reason: str


@health_router.get("/health", response_model=HealthResponse, operation_id="healthLiveness")
async def health() -> HealthResponse:
    """Liveness: the process is up. No external calls."""
    return HealthResponse(status="ok", build=APP_VERSION)


@health_router.get(
    "/ready",
    response_model=ReadyResponse,
    operation_id="healthReadiness",
    responses={503: {"model": NotReadyResponse, "description": "Dependency not ready"}},
)
async def ready() -> ReadyResponse | JSONResponse:
    """Readiness: bounded pool acquire + ``SELECT 1``; 503 on failure.

    Failure bodies carry only a generic reason slug — this endpoint is
    unauthenticated, so DB error detail goes to the log, never the response.
    """
    try:
        await db.check_ready()
    except RuntimeError:
        reason = "no_pool"
        logger.warning("readiness probe failed: pool not initialised")
    except TimeoutError:
        reason = "pool_timeout"
        logger.warning("readiness probe failed: pool acquire timed out")
    except Exception:
        reason = "db_error"
        logger.exception("readiness probe failed: database error")
    else:
        return ReadyResponse(status="ok")
    return JSONResponse(status_code=503, content={"status": "unavailable", "reason": reason})
