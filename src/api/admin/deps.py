"""Admin dashboard dependencies."""

from dataclasses import dataclass

import asyncpg
from fastapi import Request
from fastapi.responses import RedirectResponse


@dataclass
class AdminUser:
    """Authenticated exe.dev user."""

    id: str
    email: str


async def get_admin_user(request: Request) -> AdminUser | RedirectResponse:
    """Require exe.dev auth headers; redirect to login if absent."""
    user_id = request.headers.get("X-ExeDev-UserID")
    email = request.headers.get("X-ExeDev-Email")
    if not user_id or not email:
        path = request.url.path
        return RedirectResponse(
            f"/__exe.dev/login?redirect={path}", status_code=307
        )
    return AdminUser(id=user_id, email=email)


async def get_db(request: Request) -> asyncpg.Connection:
    """Yield a connection from the app-level asyncpg pool."""
    pool: asyncpg.Pool | None = getattr(request.app.state, "db_pool", None)
    if pool is None:
        raise RuntimeError("Database pool not initialized — is DATABASE_URL set?")
    async with pool.acquire() as conn:
        yield conn
