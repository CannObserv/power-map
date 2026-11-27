"""Admin Kind panel: an org's kind, its suggestions, and the operator's decision (#877).

Sources (identifier rules and TypeSafe Jev) only suggest. The panel shows the
resolved kind, where it came from, and each source's latest answer; the
operator accepts one, sets another, dismisses (decided: no kind) or clears
(back to undecided). Every decision goes through
``classification.record_decision``, the only writer of
``organizations.org_kind_id``. **Re-ask AI** asks Jev once more (one attempt,
``REASK_TIMEOUT``) and stores a second opinion; it never changes the kind, and
is disabled while ``JEV_SUGGESTIONS_ENABLED`` is off. Design:
``docs/plans/2026-11-24-org-kind-suggestions-design.md`` D5.
"""

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from markupsafe import escape

from src.api.admin.deps import (
    AdminUser,
    flash_trigger,
    get_admin_user,
    get_db,
    is_htmx,
    provision_app_user,
    with_flash,
)
from src.core import classification
from src.core import jev_client as jc
from src.core.logging import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/orgs", tags=["admin-orgs-kind"])
templates = Jinja2Templates(directory="src/templates")

#: How each source is labelled on the panel. Vendor-neutral, as the tracker's badge.
SOURCE_LABELS = {"rules": "Rules", "jev": "AI", "manual": "Reviewed"}
#: One Re-ask attempt's limit: an operator is waiting on the request.
REASK_TIMEOUT = 20.0


async def _reask_ask(org, crit, **kw) -> jc.JevAnswer:
    """``jc.ask_kind`` bounded for a request handler: one attempt, ``REASK_TIMEOUT``."""
    return await jc.ask_kind(org, crit, timeout=REASK_TIMEOUT, max_attempts=1, **kw)


async def _panel_context(db, org_id: str) -> dict:
    """Everything the panel renders; 404 for an unknown org."""
    org = await db.fetchrow(
        "SELECT o.id, o.archived_at, k.slug AS kind_slug, k.display_name AS kind_name"
        " FROM organizations o LEFT JOIN org_kinds k ON k.id = o.org_kind_id"
        " WHERE o.id = $1",
        org_id,
    )
    if org is None:
        raise HTTPException(status_code=404)
    kinds = await db.fetch("SELECT slug, display_name FROM org_kinds ORDER BY display_name")
    names = {k["slug"]: k["display_name"] for k in kinds}
    answers = await classification.latest_answers(db, org_id)
    manual = answers.get("manual")
    suggestions = [
        {
            "source": source,
            "label": SOURCE_LABELS[source],
            "value": a["value"],
            "name": names.get(a["value"]),
            "confidence": a["confidence"],
            "note": a["note"],
            # Only a real kind that isn't already the resolved one can be accepted.
            "acceptable": a["value"] in names and a["value"] != org["kind_slug"],
        }
        for source, a in answers.items()
        if source != "manual"
    ]
    # Provenance of an accepted kind: the manual row's accepted_source.
    accepted_from = None
    if manual is not None and manual["manual_action"] == "accept":
        source = manual["accepted_source"]
        accepted_from = {
            "label": SOURCE_LABELS.get(source, source),
            "confidence": answers[source]["confidence"]
            if source in answers and answers[source]["value"] == org["kind_slug"]
            else None,
        }
    return {
        "org": org,
        "kinds": kinds,
        "suggestions": suggestions,
        "accepted_from": accepted_from,
        "jev_enabled": jc.is_enabled(),
        "decision": manual["manual_action"] if manual else None,
        "decided": manual is not None
        and manual["manual_action"] in classification.DECIDING_ACTIONS,
        "editable": org["archived_at"] is None,
    }


@router.get("/{org_id}/kind/")
async def org_kind_panel(
    org_id: str,
    request: Request,
    user: AdminUser = Depends(get_admin_user),
    db=Depends(get_db),
):
    """Return the Kind panel partial."""
    ctx = await _panel_context(db, org_id)
    return templates.TemplateResponse(request, "admin/orgs/partials/_kind_panel.html", ctx)


@router.post("/{org_id}/kind/")
async def org_kind_decide(
    org_id: str,
    request: Request,
    action: str = Form(...),
    kind: str = Form(""),
    source: str = Form(""),
    user: AdminUser = Depends(provision_app_user),
    db=Depends(get_db),
):
    """Record an operator decision on the org's kind; return the refreshed panel."""
    if not await db.fetchval("SELECT 1 FROM organizations WHERE id=$1", org_id):
        raise HTTPException(status_code=404)
    try:
        await classification.record_decision(
            db,
            org_id=org_id,
            action=action,  # validated by record_decision
            kind=kind or None,
            source=source or None,
            user_id=user.id,
        )
    except classification.DecisionError as e:
        if not is_htmx(request):
            return RedirectResponse(with_flash(f"/admin/orgs/{org_id}/", "invalid"), 303)
        ctx = await _panel_context(db, org_id)
        return templates.TemplateResponse(
            request,
            "admin/orgs/partials/_kind_panel.html",
            ctx,
            headers=flash_trigger("warning", f"Kind not changed: {escape(str(e))}"),
        )
    if not is_htmx(request):
        return RedirectResponse(with_flash(f"/admin/orgs/{org_id}/", "saved"), 303)
    ctx = await _panel_context(db, org_id)
    if action in ("accept", "set"):
        body = f"Kind set: <strong>{escape(ctx['org']['kind_name'])}</strong>."
    elif action == "dismiss":
        body = "Dismissed: this organization has no kind."
    else:
        body = "Kind cleared: back to undecided."
    return templates.TemplateResponse(
        request,
        "admin/orgs/partials/_kind_panel.html",
        ctx,
        headers=flash_trigger("success", body),
    )


@router.post("/{org_id}/kind/reask/")
async def org_kind_reask(
    org_id: str,
    request: Request,
    user: AdminUser = Depends(get_admin_user),
    db=Depends(get_db),
):
    """Ask Jev again about this org; return the refreshed panel. Never sets the kind."""
    if not await db.fetchval("SELECT 1 FROM organizations WHERE id=$1", org_id):
        raise HTTPException(status_code=404)
    result = await classification.reask(db, [org_id], origin="reask", ask=_reask_ask)
    if not is_htmx(request):
        key = "saved" if result.answered else "invalid"
        return RedirectResponse(with_flash(f"/admin/orgs/{org_id}/", key), 303)
    ctx = await _panel_context(db, org_id)
    if result.answered:
        ai = next(s for s in ctx["suggestions"] if s["source"] == "jev")
        name = escape(ai["name"]) if ai["name"] else "no kind"
        body = f"AI suggests <strong>{name}</strong> ({ai['confidence'] * 100:.0f}%)."
        headers = flash_trigger("success", body)
    else:
        if result.status == "disabled":
            body = "AI suggestions are off (JEV_SUGGESTIONS_ENABLED); nothing asked."
        elif result.status == "idle":
            body = "Archived organizations aren't asked."
        else:
            body = f"AI not asked again: {escape(result.error or 'no answer')}"
        headers = flash_trigger("warning", body)
    return templates.TemplateResponse(
        request, "admin/orgs/partials/_kind_panel.html", ctx, headers=headers
    )
