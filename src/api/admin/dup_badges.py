"""Async dup-count badge partials for HTMX hx-trigger="load" slots."""

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.templating import Jinja2Templates

from src.api.admin.deps import get_admin_user, get_db
from src.api.admin.org_dups import get_org_dup_count
from src.api.admin.people_dups import get_person_dup_count

router = APIRouter(prefix="/_dup-badge", tags=["admin-dup-badges"])
templates = Jinja2Templates(directory="src/templates")

_VALID_TYPES = {"people", "orgs"}
_VALID_VARIANTS = {"card", "banner"}


@router.get("/{entity_type}/")
async def dup_badge(
    entity_type: str,
    variant: str,
    request: Request,
    _user=Depends(get_admin_user),
    _db=Depends(get_db),
    org_dup_count: int = Depends(get_org_dup_count),
    person_dup_count: int = Depends(get_person_dup_count),
):
    """Return the appropriate dup badge partial for async HTMX load.

    Requires HX-Request header; returns 400 for direct (non-HTMX) requests.
    entity_type: 'people' | 'orgs'
    variant: 'card' | 'banner'
    """
    if not request.headers.get("HX-Request"):
        raise HTTPException(status_code=400, detail="HTMX request required")
    if entity_type not in _VALID_TYPES:
        raise HTTPException(status_code=404, detail="Unknown entity type")
    if variant not in _VALID_VARIANTS:
        raise HTTPException(status_code=400, detail="Unknown variant")

    count = person_dup_count if entity_type == "people" else org_dup_count
    template_name = f"admin/partials/_dup_badge_{variant}.html"
    ctx = {
        "count": count,
        "entity_type": entity_type,
        "duplicates_url": f"/admin/{entity_type}/duplicates/",
    }
    return templates.TemplateResponse(request, template_name, ctx)
