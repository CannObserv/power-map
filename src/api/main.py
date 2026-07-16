"""FastAPI application entry point."""

import math
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

import src.core.db as db
from src.api.admin.assets import (
    inject_array_cap_into_admin_templates,
    inject_asset_version_into_admin_templates,
    inject_non_decomposable_types_into_admin_templates,
)
from src.api.admin.router import admin_router
from src.api.public.middleware import RequestLogMiddleware, drain_pending_writes
from src.api.public.router import router as public_router
from src.core.embedding_registry import EmbeddingRegistry
from src.core.logging import configure_logging

configure_logging()
inject_asset_version_into_admin_templates()
inject_array_cap_into_admin_templates()
inject_non_decomposable_types_into_admin_templates()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Create and close the asyncpg connection pool."""
    dsn = os.environ.get("DATABASE_URL")
    if dsn:
        pool = await db.create_pool(dsn)
        async with pool.acquire() as conn:
            app.state.embedding_registry = await EmbeddingRegistry.load(conn)
    else:
        app.state.embedding_registry = EmbeddingRegistry({})
    yield
    # Flush in-flight fire-and-forget api_request_log writes before the pool
    # closes (#286) — they acquire pool connections, so drain must precede close.
    await drain_pending_writes()
    await db.close_pool()


app = FastAPI(title="power-map", version="0.1.0", lifespan=lifespan)


def _sanitize_nonfinite(obj: object) -> object:
    """Replace non-finite floats with their string form for JSON encoding.

    Starlette's JSONResponse renders with ``allow_nan=False``, so a 422 whose
    echoed ``input`` contains NaN/Infinity (json.loads accepts those literals,
    #299) would crash the error response into a 500.
    """
    if isinstance(obj, float) and not math.isfinite(obj):
        return str(obj)
    if isinstance(obj, list):
        return [_sanitize_nonfinite(x) for x in obj]
    if isinstance(obj, dict):
        return {k: _sanitize_nonfinite(v) for k, v in obj.items()}
    return obj


@app.exception_handler(RequestValidationError)
async def _validation_error_handler(request: Request, exc: RequestValidationError):
    """Default 422 shape, with non-finite floats in the payload made JSON-safe."""
    return JSONResponse(
        status_code=422,
        content={"detail": _sanitize_nonfinite(jsonable_encoder(exc.errors()))},
    )


# Capture public API request/response telemetry (#260). Early-returns for any
# non-/api/v1 path, so admin/static traffic is untouched.
app.add_middleware(RequestLogMiddleware)

app.include_router(admin_router)
app.include_router(public_router)

app.mount("/static/admin", StaticFiles(directory="src/static/admin"), name="admin-static")
app.mount("/static/images", StaticFiles(directory="src/static/images"), name="static-images")
