"""Admin dashboard dependencies."""

import json
from dataclasses import dataclass
from datetime import date
from urllib.parse import parse_qsl, quote, urlencode, urlsplit, urlunsplit

from fastapi import Depends, HTTPException, Request

from src.api.deps import get_db as get_db  # noqa: F401 — re-export for admin importers


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


def parse_validity_fields(valid_from: str, valid_until: str, errors: dict) -> tuple:
    """Parse ``valid_from``/``valid_until`` admin-form strings into dates.

    Records ``valid_from`` / ``valid_until`` keys in ``errors`` in-place for a
    malformed date or an inverted range. Returns ``(date | None, date | None)``.
    (Distinct from ``_addresses_shared.parse_validity``, which raises instead of
    collecting into an errors dict.)
    """
    vf = vu = None
    if valid_from.strip():
        try:
            vf = date.fromisoformat(valid_from.strip())
        except ValueError:
            errors["valid_from"] = "Invalid date (use YYYY-MM-DD)"
    if valid_until.strip():
        try:
            vu = date.fromisoformat(valid_until.strip())
        except ValueError:
            errors["valid_until"] = "Invalid date (use YYYY-MM-DD)"
    if vf and vu and vf > vu:
        errors["valid_until"] = "Valid-until must not precede valid-from"
    return vf, vu


def flash_trigger(level: str, body: str, extra: dict | None = None) -> dict[str, str]:
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


# Shared `docs/ADMIN.md` fallback-flash registry (#351). Non-HTMX mutation fallbacks across
# every admin section append one of these keys via ``with_flash`` so a successful
# create/edit/delete confirms like a Danger Zone action does. The registry is
# deliberately generic (the static ?flash= param can't carry a per-row value the
# HTMX HX-Trigger flash includes) and consulted by ``resolve_query_flash`` as a
# fallback after each route's own ``_FLASH_MESSAGES``.
#
# Flash-level taxonomy (#353), one convention per action class across every
# admin surface — HTMX HX-Trigger and non-HTMX fallback alike:
#   success — any mutation that changed state (create/edit/delete/archive/unarchive);
#             the *body text* ("Saved."/"Removed.") carries the create-vs-delete meaning
#   warning — rejected, nothing changed (bad input, uniqueness/409 conflict)
#   error   — unexpected operation failure only. None on this server-side
#             flash_trigger path; the sole `error` use is the client-side
#             clipboard-copy failure in admin/*/partials/_link_row.html — a
#             genuine operation failure, the exemplar of the reserved level.
#   info    — retired from mutation confirmations
# So `removed` flashes `success` (was `info` pre-#353) — a delete is a successful
# mutation, matching the Danger Zone `deleted` precedent. Keys:
#   saved   — a create or edit succeeded
#   removed — a delete/unlink succeeded
#   invalid — a create/edit was rejected for bad input (nothing changed)
#   exists  — a create/edit hit a uniqueness conflict (nothing changed)
SHARED_FLASH_MESSAGES: dict[str, tuple[str, str]] = {
    "saved": ("success", "Saved."),
    "removed": ("success", "Removed."),
    "invalid": ("warning", "Couldn't save — check your input."),
    "exists": ("warning", "That already exists."),
}


def with_flash(url: str, flash_key: str) -> str:
    """Return ``url`` with ``?flash=<flash_key>`` set, preserving path/query/fragment.

    Use on `docs/ADMIN.md` non-HTMX fallback redirects so the target detail/list route can
    surface a confirmation via ``resolve_query_flash`` (#351). Any pre-existing
    ``flash`` param is overwritten; other query params and the fragment survive.

    Callers pass query-less targets (bare detail/list URLs). Any pre-existing
    query is round-tripped through ``parse_qsl``/``urlencode`` and so may be
    re-encoded (e.g. a space renders as ``+``); harmless for equivalent URLs but
    worth knowing if a fallback ever redirects to a URL carrying filter state.
    """
    parts = urlsplit(url)
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    query["flash"] = flash_key
    return urlunsplit(parts._replace(query=urlencode(query)))


def resolve_query_flash(
    request: Request,
    flash_messages: dict[str, tuple[str, str]],
    flash_key: str | None,
) -> tuple[dict | None, dict]:
    """Resolve a ``?flash=<key>`` query param against a router-local registry.

    Returns ``(flash_msg, headers)``. ``flash_msg`` is ``{"level", "body"}`` or ``None``.
    Resolution order: the route-local ``flash_messages`` first, then the shared
    ``SHARED_FLASH_MESSAGES`` (the `docs/ADMIN.md` fallback keys, #351) — so a route need not
    re-declare the generic ancillary keys, and a route-local key of the same name
    still wins. When a flash is resolved on a non-HTMX request, ``headers`` carries
    an ``HX-Replace-Url`` entry that strips the ``flash`` query param so a refresh
    won't re-trigger the message. HTMX requests get an empty headers dict.
    """
    pair = flash_messages.get(flash_key) if flash_key else None
    if pair is None and flash_key:
        pair = SHARED_FLASH_MESSAGES.get(flash_key)
    flash_msg = {"level": pair[0], "body": pair[1]} if pair else None
    headers: dict[str, str] = {}
    if flash_msg and not is_htmx(request):
        headers["HX-Replace-Url"] = str(request.url.remove_query_params("flash"))
    return flash_msg, headers


def build_parts_summary(
    family: list[str] | None,
    given: list[str] | None,
    additional: list[str] | None,
) -> str | None:
    """One-line summary of structured-name parts for read-row subtitles.

    Format: ``"<family> · <given> · <additional>"`` (each space-joined,
    skipping empty arrays). Returns None when nothing structural is set
    so the template's ``{% if n.parts_summary %}`` guard hides the row.

    Single source of truth for both the person-detail handler
    (``src.api.admin.people``) and the post-mutation tbody re-render in
    ``src.api.admin._names_shared``.
    """
    family_s = " ".join(family or [])
    given_s = " ".join(given or [])
    additional_s = " ".join(additional or [])
    parts = [p for p in (family_s, given_s, additional_s) if p]
    return " · ".join(parts) if parts else None


def escape_like(s: str) -> str:
    r"""Escape LIKE/ILIKE special characters so user input is a literal substring.

    Use with ``ILIKE $N ESCAPE '\\'`` in queries::

        escaped = escape_like(q.strip())
        await conn.fetch("... ILIKE $1 ESCAPE '\\\\'", f"%{escaped}%")
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


async def provision_app_user(
    user: AdminUser = Depends(get_admin_user),
    db=Depends(get_db),
) -> AdminUser:
    """Upsert the app_users row for the current exe.dev user, then return the user.

    Use as a drop-in replacement for get_admin_user on routes that require the
    user to exist in the app_users table (e.g., API key management routes).
    """
    await db.execute(
        """
        INSERT INTO app_users (id, email) VALUES ($1, $2)
        ON CONFLICT (id) DO UPDATE SET email = EXCLUDED.email
        """,
        user.id,
        user.email,
    )
    return user
