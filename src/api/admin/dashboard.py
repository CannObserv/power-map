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
    api_stats = await db.fetchrow(
        """
        SELECT
            COUNT(*) AS total,
            COUNT(*) FILTER (WHERE route_group='observations' AND disposition='new') AS obs_new,
            COUNT(*) FILTER (WHERE route_group='observations' AND disposition='auto-attached')
                AS obs_attached,
            COUNT(*) FILTER (WHERE route_group='observations' AND disposition='rejected')
                AS obs_rejected,
            COUNT(*) FILTER (WHERE route_group='changes') AS changes_polls,
            COALESCE(SUM(item_count) FILTER (WHERE route_group='changes'), 0) AS changes_rows,
            COUNT(*) FILTER (WHERE status_code >= 400) AS errors,
            MAX(occurred_at) AS last_request
        FROM api_request_log
        WHERE occurred_at >= NOW() - INTERVAL '24 hours'
        """
    )
    return templates.TemplateResponse(
        request,
        "admin/dashboard.html",
        {
            "user": user,
            "active_section": "dashboard",
            "counts": counts,
            "api_stats": api_stats,
        },
    )
