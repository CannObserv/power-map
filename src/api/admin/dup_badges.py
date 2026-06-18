"""Async dup-count badge partials for HTMX hx-trigger="load" slots."""

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.templating import Jinja2Templates

from src.api.admin.deps import get_admin_user
from src.api.admin.org_dups import get_org_dup_count
from src.api.admin.people_dups import get_person_dup_count

router = APIRouter(prefix="/_dup-badge", tags=["admin-dup-badges"])
templates = Jinja2Templates(directory="src/templates")

_VALID_VARIANTS = {"card", "banner"}


def _render_badge(request: Request, entity_type: str, variant: str, count: int):
    """Validate and render the requested badge partial.

    Raises 400 for non-HTMX or unknown variant; returns TemplateResponse on success.
    """
    if not request.headers.get("HX-Request"):
        raise HTTPException(status_code=400, detail="HTMX request required")
    if variant not in _VALID_VARIANTS:
        raise HTTPException(status_code=400, detail="Unknown variant")
    return templates.TemplateResponse(
        request,
        f"admin/partials/_dup_badge_{variant}.html",
        {
            "count": count,
            "entity_type": entity_type,
            "duplicates_url": f"/admin/{entity_type}/duplicates/",
        },
    )


@router.get("/people/")
async def dup_badge_people(
    variant: str,
    request: Request,
    _user=Depends(get_admin_user),
    count: int = Depends(get_person_dup_count),
):
    """Return the people dup badge partial for async HTMX load.

    Requires HX-Request header; returns 400 for direct (non-HTMX) requests.
    variant: 'card' | 'banner'
    """
    return _render_badge(request, "people", variant, count)


@router.get("/orgs/")
async def dup_badge_orgs(
    variant: str,
    request: Request,
    _user=Depends(get_admin_user),
    count: int = Depends(get_org_dup_count),
):
    """Return the orgs dup badge partial for async HTMX load.

    Requires HX-Request header; returns 400 for direct (non-HTMX) requests.
    variant: 'card' | 'banner'
    """
    return _render_badge(request, "orgs", variant, count)
