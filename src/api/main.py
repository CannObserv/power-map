"""FastAPI application entry point."""

import os
from contextlib import asynccontextmanager

import asyncpg
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from src.api.admin.router import admin_router
from src.core.logging import configure_logging

configure_logging()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Create and close the asyncpg connection pool."""
    dsn = os.environ.get("DATABASE_URL")
    if dsn:
        app.state.db_pool = await asyncpg.create_pool(dsn)
    else:
        app.state.db_pool = None
    yield
    if getattr(app.state, "db_pool", None):
        await app.state.db_pool.close()


app = FastAPI(title="power-map", version="0.1.0", lifespan=lifespan)

app.include_router(admin_router)

# Static files and templates — directories created in Task 5
# app.mount("/static/admin", StaticFiles(directory="src/static/admin"), name="admin-static")
templates = Jinja2Templates(directory="src/templates")
