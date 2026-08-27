"""Admin views for linking two orgs as a succession pair (#469).

The dedup flow's alternative to merge when the candidates are two source-keyed
manifestations of one institution (an upstream re-key): pick a direction,
optionally a date, and write one ``succeeded_by`` event on the predecessor.
The chain exclusion in duplicate detection then retires the pair without
collapsing rows, so every external identifier keeps its 1:1 org anchor.
"""

import datetime

import asyncpg
from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from markupsafe import escape

from src.api.admin.deps import (
    AdminUser,
    flash_trigger,
    get_admin_user,
    get_db,
    is_htmx,
    with_flash,
)
from src.api.admin.org_dups import fetch_duplicate_pairs, invalidate_dup_count_cache
from src.core.db import generate_id
from src.core.logging import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/orgs", tags=["admin-orgs-succession"])

templates = Jinja2Templates(directory="src/templates")


async def _org_display_name(db, org_id: str) -> str | None:
    return await db.fetchval(
        "SELECT display_name FROM v_org_display_names WHERE organization_id=$1", org_id
    )


async def _latest_activity(db, org_id: str) -> datetime.date | None:
    """Latest assignment activity for an org: an is_current row counts as today."""
    return await db.fetchval(
        """SELECT max(CASE WHEN ra.is_current THEN CURRENT_DATE
                           ELSE COALESCE(ra.end_date, ra.start_date) END)
           FROM role_assignments ra
           JOIN roles r ON r.id = ra.role_id
           WHERE r.organization_id = $1 AND ra.archived_at IS NULL""",
        org_id,
    )


async def _infer_predecessor(db, id_a: str, id_b: str) -> str:
    """Default direction: the org whose assignment activity ended earlier.

    Falls back to the older row (created_at) when either side has no
    assignments or the activity ties — a suggestion the curator can flip.
    """
    act_a = await _latest_activity(db, id_a)
    act_b = await _latest_activity(db, id_b)
    if act_a is not None and act_b is not None and act_a != act_b:
        return id_a if act_a < act_b else id_b
    created = await db.fetch(
        "SELECT id FROM organizations WHERE id IN ($1, $2) ORDER BY created_at, id",
        id_a,
        id_b,
    )
    return created[0]["id"]


async def _in_same_chain(db, id_a: str, id_b: str) -> bool:
    return bool(
        await db.fetchval(
            "SELECT 1 FROM v_org_succession_pairs WHERE org_a = $1 AND org_b = $2",
            id_a,
            id_b,
        )
    )


@router.get("/{id_a}/link-successor-preview/{id_b}/")
async def link_successor_preview(
    id_a: str,
    id_b: str,
    request: Request,
    ctx: str = "",
    user: AdminUser = Depends(get_admin_user),
    db=Depends(get_db),
):
    """Modal: choose predecessor → successor direction and an optional date.

    ``ctx="duplicates"`` (opened from the dedup review screen) makes the form
    target ``#orgs-duplicates-region``; anywhere else that element does not
    exist and a dangling ``hx-target`` would make HTMX abort the POST with
    ``htmx:targetError`` — so the form falls back to ``hx-swap="none"``
    (flash headers still process; the shared script closes the modal).
    """
    if id_a == id_b:
        raise HTTPException(status_code=400, detail="Cannot link an organization to itself")
    for oid in (id_a, id_b):
        exists = await db.fetchval(
            "SELECT 1 FROM organizations WHERE id=$1 AND archived_at IS NULL", oid
        )
        if not exists:
            raise HTTPException(status_code=404)

    default_pred = await _infer_predecessor(db, id_a, id_b)
    orgs = [
        {
            "id": oid,
            "name": await _org_display_name(db, oid),
            "latest_activity": await _latest_activity(db, oid),
        }
        for oid in (id_a, id_b)
    ]
    return templates.TemplateResponse(
        request,
        "admin/orgs/_link_successor_modal.html",
        {
            "orgs": orgs,
            "id_a": id_a,
            "id_b": id_b,
            "default_pred": default_pred,
            "already_chained": await _in_same_chain(db, id_a, id_b),
            "ctx": ctx,
        },
    )


async def _apply_link(db, user, pred_id: str, succ_id: str, succession_date: str):
    """Validate and write the succession event.

    Returns ``(ok, body, fallback_key)`` — ``ok`` False means a reject with a
    warning body; hard errors (self-link, missing org) raise instead.
    """
    if pred_id == succ_id:
        raise HTTPException(status_code=400, detail="Cannot link an organization to itself")
    names = {}
    for oid in (pred_id, succ_id):
        name = await _org_display_name(db, oid)
        exists = await db.fetchval(
            "SELECT 1 FROM organizations WHERE id=$1 AND archived_at IS NULL", oid
        )
        if not exists:
            raise HTTPException(status_code=404)
        names[oid] = name or "(untitled)"

    date_parts: tuple[int | None, int | None, int | None] = (None, None, None)
    if succession_date.strip():
        try:
            d = datetime.date.fromisoformat(succession_date.strip())
            date_parts = (d.year, d.month, d.day)
        except ValueError:
            return False, "Succession date must be YYYY-MM-DD.", "invalid"

    # One chain, one membership: a pair already connected (either direction,
    # directly or transitively) gains no second edge — also blocks cycles.
    if await _in_same_chain(db, pred_id, succ_id):
        body = (
            f"<strong>{escape(names[pred_id])}</strong> and "
            f"<strong>{escape(names[succ_id])}</strong> are already in the "
            f"same succession chain."
        )
        return False, body, "exists"

    try:
        await db.execute(
            """INSERT INTO entity_events
                   (id, entity_type, entity_id, event_type_id,
                    event_year, event_month, event_day,
                    linked_entity_type, linked_entity_id)
               SELECT $1, 'organization', $2, t.id, $4, $5, $6, 'organization', $3
               FROM entity_event_types t WHERE t.slug = 'succeeded_by'""",
            generate_id(),
            pred_id,
            succ_id,
            *date_parts,
        )
    except asyncpg.UniqueViolationError:
        # uq_entity_events_succession_edge: a concurrent request won the race
        # between our chain check and this insert — same outcome as the check.
        body = (
            f"<strong>{escape(names[pred_id])}</strong> and "
            f"<strong>{escape(names[succ_id])}</strong> are already in the "
            f"same succession chain."
        )
        return False, body, "exists"
    await invalidate_dup_count_cache(db)
    logger.info(
        "org_succession_linked",
        extra={"predecessor_id": pred_id, "successor_id": succ_id, "by": user.email},
    )
    body = (
        f'<a href="/admin/orgs/{pred_id}/"><strong>{escape(names[pred_id])}</strong></a> '
        f"is now succeeded by "
        f'<a href="/admin/orgs/{succ_id}/"><strong>{escape(names[succ_id])}</strong></a>.'
    )
    if date_parts[0] is not None:
        body += f" Its lifespan now ends {escape(succession_date.strip())}."
    return True, body, "saved"


@router.post("/{pred_id}/link-successor/{succ_id}/")
async def link_successor(
    pred_id: str,
    succ_id: str,
    request: Request,
    succession_date: str = Form(""),
    user: AdminUser = Depends(get_admin_user),
    db=Depends(get_db),
):
    """Write a ``succeeded_by`` event on the predecessor, linked to the successor."""
    ok, body, fallback_key = await _apply_link(db, user, pred_id, succ_id, succession_date)
    if not is_htmx(request):
        return RedirectResponse(
            with_flash(f"/admin/orgs/{pred_id}/", fallback_key), status_code=303
        )
    if ok:
        headers = flash_trigger("success", body, extra={"refreshDupBadge": True})
    else:
        headers = flash_trigger("warning", body, extra={"refreshDupBadge": True})
    if request.headers.get("HX-Target") == "orgs-duplicates-region":
        pairs = await fetch_duplicate_pairs(db)
        return templates.TemplateResponse(
            request,
            "admin/orgs/_duplicates_region.html",
            {"user": user, "active_section": "orgs_duplicates", "pairs": pairs},
            headers=headers,
        )
    return HTMLResponse(content="", status_code=200, headers=headers)
