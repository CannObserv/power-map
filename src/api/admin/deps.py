"""Admin dashboard dependencies."""

import json
from dataclasses import dataclass
from urllib.parse import quote

from fastapi import HTTPException, Request

import src.core.db as db


@dataclass
class AdminUser:
    """Authenticated exe.dev user."""

    id: str
    email: str


async def get_admin_user(request: Request) -> AdminUser:
    """Require exe.dev auth headers; raise 307 redirect to login if absent."""
    user_id = request.headers.get("X-ExeDev-UserID")
    email = request.headers.get("X-ExeDev-Email")
    if not user_id or not email:
        path = request.url.path
        query = request.url.query
        next_url = f"{path}?{query}" if query else path
        raise HTTPException(
            status_code=307,
            headers={"Location": f"/__exe.dev/login?redirect={quote(next_url)}"},
        )
    return AdminUser(id=user_id, email=email)


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


def resolve_query_flash(
    request: Request,
    flash_messages: dict[str, tuple[str, str]],
    flash_key: str | None,
) -> tuple[dict | None, dict]:
    """Resolve a ``?flash=<key>`` query param against a router-local registry.

    Returns ``(flash_msg, headers)``. ``flash_msg`` is ``{"level", "body"}`` or ``None``.
    When a flash is resolved on a non-HTMX request, ``headers`` carries an
    ``HX-Replace-Url`` entry that strips the ``flash`` query param so a refresh
    won't re-trigger the message. HTMX requests get an empty headers dict.
    """
    pair = flash_messages.get(flash_key) if flash_key else None
    flash_msg = {"level": pair[0], "body": pair[1]} if pair else None
    headers: dict = {}
    if flash_msg and not is_htmx(request):
        headers["HX-Replace-Url"] = str(request.url.remove_query_params("flash"))
    return flash_msg, headers


def escape_like(s: str) -> str:
    r"""Escape LIKE/ILIKE special characters so user input is a literal substring.

    Use with ``ILIKE $N ESCAPE '\\'`` in queries::

        escaped = escape_like(q.strip())
        await db.fetch("... ILIKE $1 ESCAPE '\\\\'", f"%{escaped}%")
    """
    return s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


async def person_header_extra(person_id: str, db) -> dict:
    """Return extra dict for flash_trigger with the current person display name.

    Queries v_person_display_names and falls back to person_id when display_name is NULL.
    Pass as extra= to flash_trigger on any HTMX mutation route that may change the
    person's canonical name.
    """
    row = await db.fetchrow(
        "SELECT display_name FROM v_person_display_names WHERE person_id=$1", person_id
    )
    display = row["display_name"] if row and row["display_name"] else person_id
    return {"updatePersonHeader": {"display": display}}


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


async def get_db():
    """Yield a connection from the global asyncpg pool."""
    pool = db.get_pool()
    async with pool.acquire() as conn:
        yield conn
