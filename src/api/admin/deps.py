"""Admin dashboard dependencies."""

import time
from dataclasses import dataclass
from urllib.parse import quote

import asyncpg
from fastapi import Depends, Request
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
        query = request.url.query
        next_url = f"{path}?{query}" if query else path
        return RedirectResponse(
            f"/__exe.dev/login?redirect={quote(next_url)}", status_code=307
        )
    return AdminUser(id=user_id, email=email)


def check_auth(user: AdminUser | RedirectResponse):
    """Return (redirect, user) tuple. Return redirect immediately if unauthenticated."""
    if isinstance(user, RedirectResponse):
        return user, None
    return None, user


async def get_db(request: Request) -> asyncpg.Connection:
    """Yield a connection from the app-level asyncpg pool."""
    pool: asyncpg.Pool | None = getattr(request.app.state, "db_pool", None)
    if pool is None:
        raise RuntimeError("Database pool not initialized — is DATABASE_URL set?")
    async with pool.acquire() as conn:
        yield conn


_CANDIDATE_WHERE = """
    FROM organizations a
    JOIN organizations b ON b.id > a.id
    JOIN v_org_display_names dn_a ON dn_a.organization_id = a.id
    JOIN v_org_display_names dn_b ON dn_b.organization_id = b.id
    WHERE a.archived_at IS NULL AND b.archived_at IS NULL
      AND similarity(dn_a.display_name, dn_b.display_name) > 0.85
      AND NOT EXISTS (
          SELECT 1 FROM duplicate_dismissals
          WHERE entity_type = 'organization'
            AND entity_a_id = a.id AND entity_b_id = b.id
      )
"""

_DUP_COUNT_TTL = 300.0  # seconds
_dup_count_cache: dict = {"value": 0, "expires": 0.0}


def _invalidate_dup_count_cache() -> None:
    _dup_count_cache["expires"] = 0.0


async def count_org_duplicates(db) -> int:
    """Return count of non-dismissed near-duplicate org pairs (TTL-cached, 5 min)."""
    now = time.monotonic()
    if now < _dup_count_cache["expires"]:
        return _dup_count_cache["value"]
    count = await db.fetchval(f"SELECT count(*) {_CANDIDATE_WHERE}")
    _dup_count_cache["value"] = count
    _dup_count_cache["expires"] = now + _DUP_COUNT_TTL
    return count


async def get_org_dup_count(db=Depends(get_db)) -> int:
    """FastAPI dependency: cached org duplicate count, defaults to 0 on error."""
    try:
        return await count_org_duplicates(db)
    except Exception:
        return 0
