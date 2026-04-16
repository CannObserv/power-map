"""FastAPI application entry point."""

import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

import src.core.db as db
from src.api.admin.router import admin_router
from src.api.public.router import router as public_router
from src.core.logging import configure_logging

configure_logging()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Create and close the asyncpg connection pool."""
    dsn = os.environ.get("DATABASE_URL")
    if dsn:
        await db.create_pool(dsn)
    yield
    await db.close_pool()


app = FastAPI(title="power-map", version="0.1.0", lifespan=lifespan)

app.include_router(admin_router)
app.include_router(public_router)

app.mount("/static/admin", StaticFiles(directory="src/static/admin"), name="admin-static")
app.mount("/static/images", StaticFiles(directory="src/static/images"), name="static-images")
