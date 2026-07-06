"""Admin entities landing page and unified entity-search typeahead."""

from fastapi import APIRouter, Depends, Request
from fastapi.templating import Jinja2Templates

from src.api.admin.deps import AdminUser, get_admin_user, get_db
from src.api.admin.entity_lookup import search_entities

templates = Jinja2Templates(directory="src/templates")
router = APIRouter(prefix="/entities", tags=["admin-entities"])


@router.get("/")
async def entities_index(
    request: Request,
    user: AdminUser = Depends(get_admin_user),
    db=Depends(get_db),
):
    """Entities landing page — overview cards for all entity types."""
    counts = await db.fetchrow(
        """
        SELECT
            (SELECT COUNT(*) FROM people           WHERE archived_at IS NULL) AS people,
            (SELECT COUNT(*) FROM organizations    WHERE archived_at IS NULL) AS orgs,
            (SELECT COUNT(*) FROM jurisdictions    WHERE archived_at IS NULL) AS jurisdictions,
            (SELECT COUNT(*) FROM roles            WHERE archived_at IS NULL) AS roles,
            (SELECT COUNT(*) FROM role_assignments WHERE archived_at IS NULL) AS assignments
        """
    )
    return templates.TemplateResponse(
        request,
        "admin/entities/index.html",
        {
            "user": user,
            "active_section": "entities",
            "counts": counts,
        },
    )


@router.get("/search/")
async def entities_search(
    request: Request,
    q: str = "",
    linked_entity_type: str = "",
    user: AdminUser = Depends(get_admin_user),
    db=Depends(get_db),
):
    """Typeahead search scoped by entity type — returns an HTML option fragment.

    Backs the linked-entity field on the admin event form: ``linked_entity_type``
    selects which table (people or organizations) is searched. Unsupported types
    and blank queries yield an empty fragment.
    """
    results = await search_entities(db, linked_entity_type, q)
    return templates.TemplateResponse(
        request,
        "admin/shared/_entity_search_results.html",
        {"results": results},
    )
