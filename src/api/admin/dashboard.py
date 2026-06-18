"""Admin dashboard landing page."""

from fastapi import APIRouter, Depends, Request
from fastapi.templating import Jinja2Templates

from src.api.admin.deps import AdminUser, get_admin_user, get_db
from src.core.logging import get_logger

logger = get_logger(__name__)

templates = Jinja2Templates(directory="src/templates")
router = APIRouter()


@router.get("/")
async def dashboard(
    request: Request,
    user: AdminUser = Depends(get_admin_user),
    db=Depends(get_db),
):
    """Admin dashboard landing page."""
    counts = await db.fetchrow(
        """
        SELECT
            (SELECT COUNT(*) FROM people WHERE archived_at IS NULL)           AS people,
            (SELECT COUNT(*) FROM organizations WHERE archived_at IS NULL)     AS orgs,
            (SELECT COUNT(*) FROM roles WHERE archived_at IS NULL)             AS roles,
            (SELECT COUNT(*) FROM role_assignments WHERE archived_at IS NULL)  AS assignments,
            (SELECT COUNT(*) FROM import_batches)                              AS imports,
            (SELECT COUNT(*) FROM link_types WHERE NOT is_social)    AS general_link_types,
            (SELECT COUNT(*) FROM link_types WHERE is_social)        AS social_link_types,
            (SELECT COUNT(*) FROM entity_identifier_types)           AS identifier_types
        """
    )
    return templates.TemplateResponse(
        request,
        "admin/dashboard.html",
        {
            "user": user,
            "active_section": "dashboard",
            "counts": counts,
        },
    )
