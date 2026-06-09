"""FastAPI application entry point."""

import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

import src.core.db as db
from src.api.admin.assets import (
    inject_array_cap_into_admin_templates,
    inject_asset_version_into_admin_templates,
    inject_non_decomposable_types_into_admin_templates,
)
from src.api.admin.router import admin_router
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
    await db.close_pool()


app = FastAPI(title="power-map", version="0.1.0", lifespan=lifespan)

app.include_router(admin_router)
app.include_router(public_router)

app.mount("/static/admin", StaticFiles(directory="src/static/admin"), name="admin-static")
app.mount("/static/images", StaticFiles(directory="src/static/images"), name="static-images")
