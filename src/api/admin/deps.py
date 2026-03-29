"""Admin dashboard dependencies."""

import json
from dataclasses import dataclass
from urllib.parse import quote

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


def is_htmx(request: Request) -> bool:
    """Return True for HTMX non-boosted requests (for partial template selection)."""
    return bool(request.headers.get("HX-Request") and not request.headers.get("HX-Boosted"))


def flash_trigger(
    level: str, body: str, extra: dict | None = None
) -> dict[str, str]:
    """Return an HX-Trigger header dict that dispatches a showFlash event on the client.

    Pass directly as the headers argument to TemplateResponse on HTMX mutation routes:

        return templates.TemplateResponse(
            request, "partial.html", ctx,
            headers=flash_trigger("success", f"Saved <strong>{escape(name)}</strong>."),
        )

    HTMX processes the HX-Trigger header and fires a showFlash DOM event. The flash.js
    listener catches it and injects the flash into #flash-region — no OOB element needed.
    Always escape DB-derived values in body with markupsafe.escape() before calling.

    Pass extra to merge additional event keys into the same HX-Trigger header, e.g.:
        flash_trigger("success", "Saved.", extra={"updateOrgHeader": {"display": name}})
    """
    payload: dict = {"showFlash": {"level": level, "body": body}}
    if extra:
        payload.update(extra)
    return {"HX-Trigger": json.dumps(payload)}


async def org_header_extra(org_id: str, db) -> dict:
    """Return extra dict for flash_trigger with the current org display name.

    Queries v_org_display_names and falls back to org_id when display_name is NULL
    (e.g. multiple names, none canonical). Pass as extra= to flash_trigger on any
    HTMX mutation route that may change the org's canonical name or acronym.
    """
    row = await db.fetchrow(
        "SELECT display_name FROM v_org_display_names WHERE organization_id=$1", org_id
    )
    display = row["display_name"] if row and row["display_name"] else org_id
    return {"updateOrgHeader": {"display": display}}


async def get_db(request: Request) -> asyncpg.Connection:
    """Yield a connection from the app-level asyncpg pool."""
    pool: asyncpg.Pool | None = getattr(request.app.state, "db_pool", None)
    if pool is None:
        raise RuntimeError("Database pool not initialized — is DATABASE_URL set?")
    async with pool.acquire() as conn:
        yield conn
