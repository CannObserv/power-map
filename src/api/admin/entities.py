"""Admin entities landing page."""

from fastapi import APIRouter, Depends, Request
from fastapi.templating import Jinja2Templates

from src.api.admin.deps import AdminUser, get_admin_user, get_db

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
