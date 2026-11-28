"""Admin › Kind review: the org-kind review queue (#877 phase 3b, design D5).

Bulk accept (``scripts/accept_classification_suggestions.py``) takes the
answers an operator trusts by rule; this page is where the rest are decided.
It lists ``classification.review_queue``: every active org with no kind and no
deciding ``manual`` row whose answers bulk accept would not take, plus the
cleared orgs, disputes first. The reason is ``bulk_outcome``'s, so this page
and the script can't disagree. Untouched orgs the next bulk run would accept
aren't listed; the dashboard counts them.

Each row's Accept rules, Accept AI, Set and Dismiss post one
``POST /admin/classification/review/{org_id}/`` through
``record_decision(undecided_only=True)``, the only writer of
``organizations.org_kind_id``: an org decided elsewhere since the page loaded
is refused, never overwritten. HTMX gets the current page's region back (the
row gone, the count and pages right) with a flash; other clients a 303 to the
queue with ``saved`` / ``invalid``.

The whole queue is computed per request and paged in Python. It is bounded by
the orgs with no kind, about 520 after the first bulk run.
"""

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request
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
    resolve_query_flash,
    with_flash,
)
from src.api.admin.pagination import (
    PAGE_SIZE_DEFAULT,
    PAGE_SIZE_MAX,
    PAGE_SIZE_MIN,
    pagination_context,
)
from src.core import classification
from src.core import jev_client as jc
from src.core.logging import get_logger

logger = get_logger(__name__)

templates = Jinja2Templates(directory="src/templates")
router = APIRouter(prefix="/classification/review", tags=["admin-classification-review"])

URL = "/admin/classification/review/"
_PAGE = "admin/classification/review/list.html"
_REGION = "admin/classification/review/_region.html"

#: Why an org is in the queue, as the page says it.
REASON_LABELS: dict[classification.ReviewReason, str] = {
    "disagree": "Rules and AI disagree",
    "conflict": "Conflicting identifiers",
    "low": "Low AI confidence",
    "untrusted": "AI kind not trusted yet",
    "none": "AI found no kind",
    "unanswered": "No AI answer yet",
    "cleared": "Cleared",
}

_FLASH_MESSAGES: dict[str, tuple[str, str]] = {}


async def _region_context(db, *, page: int, page_size: int) -> dict:
    """One page of the queue, with the kinds for the Set select and the labels."""
    queue = await classification.review_queue(db, threshold=jc.confidence_threshold())
    pctx = pagination_context(page, len(queue.items), page_size)
    start = (pctx["page"] - 1) * page_size
    kinds = await db.fetch("SELECT slug, display_name FROM org_kinds ORDER BY display_name")
    return {
        "items": queue.items[start : start + page_size],
        "total": len(queue.items),
        "ready": queue.ready,
        "kinds": kinds,
        "kind_names": {k["slug"]: k["display_name"] for k in kinds},
        "reason_labels": REASON_LABELS,
        "page_size": page_size,
        **pctx,
    }


@router.get("/")
async def review_queue(
    request: Request,
    page: int = Query(1, ge=1),
    page_size: int = Query(PAGE_SIZE_DEFAULT, ge=PAGE_SIZE_MIN, le=PAGE_SIZE_MAX),
    flash: str | None = Query(None),
    user: AdminUser = Depends(get_admin_user),
    db=Depends(get_db),
):
    """List the org-kind review queue, disputes first."""
    ctx = await _region_context(db, page=page, page_size=page_size)
    if is_htmx(request):
        return templates.TemplateResponse(request, _REGION, ctx)
    flash_msg, headers = resolve_query_flash(request, _FLASH_MESSAGES, flash)
    ctx |= {"user": user, "active_section": "classification_review", "flash_msg": flash_msg}
    return templates.TemplateResponse(request, _PAGE, ctx, headers=headers)


@router.post("/{org_id}/")
async def review_decide(
    org_id: str,
    request: Request,
    action: str = Form(...),
    kind: str = Form(""),
    source: str = Form(""),
    page: int = Form(1),
    page_size: int = Form(PAGE_SIZE_DEFAULT),
    user: AdminUser = Depends(provision_app_user),
    db=Depends(get_db),
):
    """Decide one queued org's kind; return the refreshed page of the queue."""
    name = await db.fetchval(
        "SELECT COALESCE(dn.display_name, '(unnamed)') FROM organizations o"
        " LEFT JOIN v_org_display_names dn ON dn.organization_id = o.id WHERE o.id = $1",
        org_id,
    )
    if name is None:
        raise HTTPException(status_code=404)
    page_size = min(max(page_size, PAGE_SIZE_MIN), PAGE_SIZE_MAX)
    try:
        if action not in classification.DECIDING_ACTIONS:
            raise classification.DecisionError(f"not a review action: {action!r}")
        await classification.record_decision(
            db,
            org_id=org_id,
            action=action,
            kind=kind or None,
            source=source or None,
            user_id=user.id,
            undecided_only=True,
        )
    except classification.DecisionError as e:
        if not is_htmx(request):
            return RedirectResponse(with_flash(URL, "invalid"), 303)
        # Re-rendered, not refused in place: a row decided elsewhere goes.
        ctx = await _region_context(db, page=max(page, 1), page_size=page_size)
        body = f"{escape(name)}: kind not changed: {escape(str(e))}"
        return templates.TemplateResponse(
            request, _REGION, ctx, headers=flash_trigger("warning", body)
        )
    if not is_htmx(request):
        return RedirectResponse(with_flash(URL, "saved"), 303)
    if action == "dismiss":
        body = f"Dismissed: {escape(name)} has no kind."
    else:
        kind_name = await db.fetchval("SELECT display_name FROM org_kinds WHERE slug=$1", kind)
        body = f"Kind set: <strong>{escape(kind_name)}</strong> for {escape(name)}."
    ctx = await _region_context(db, page=max(page, 1), page_size=page_size)
    return templates.TemplateResponse(request, _REGION, ctx, headers=flash_trigger("success", body))
