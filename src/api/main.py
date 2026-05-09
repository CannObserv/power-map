"""FastAPI application entry point."""

import importlib
import os
import pkgutil
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

import src.api.admin as admin_pkg
import src.core.db as db
from src.api.admin.assets import ASSET_VERSION, register_asset_version_global
from src.api.admin.router import admin_router
from src.api.public.router import router as public_router
from src.core.logging import configure_logging

configure_logging()


def _inject_asset_version_into_admin_templates() -> None:
    """Set ``asset_version`` on every Jinja2Templates instance in src.api.admin.

    Each admin sub-module owns its own ``templates = Jinja2Templates(...)``.
    Walking the package once at startup is cheaper and less invasive than
    refactoring all 28 modules to share a single instance.
    """
    for mod_info in pkgutil.iter_modules(admin_pkg.__path__):
        module = importlib.import_module(f"{admin_pkg.__name__}.{mod_info.name}")
        templates = getattr(module, "templates", None)
        if isinstance(templates, Jinja2Templates):
            register_asset_version_global(templates, version=ASSET_VERSION)


_inject_asset_version_into_admin_templates()


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
