"""Admin activity landing page."""

from fastapi import APIRouter, Depends, Request
from fastapi.templating import Jinja2Templates

from src.api.admin.deps import AdminUser, get_admin_user, get_db

templates = Jinja2Templates(directory="src/templates")
router = APIRouter(prefix="/activity", tags=["admin-activity"])


@router.get("/")
async def activity_index(
    request: Request,
    user: AdminUser = Depends(get_admin_user),
    db=Depends(get_db),
):
    """Activity landing page — overview cards for all activity sections."""
    counts = await db.fetchrow(
        """
        SELECT
            (SELECT COUNT(*) FROM import_batches) AS imports,
            (SELECT COUNT(*) FROM api_request_log
             WHERE occurred_at >= NOW() - INTERVAL '24 hours') AS req_24h,
            (SELECT COUNT(*) FROM api_request_log
             WHERE occurred_at >= NOW() - INTERVAL '24 hours'
               AND disposition = 'rejected') AS req_rejected_24h
        """
    )
    # Busiest key of the last 24h (#294) — a runaway client is visible from the
    # landing page, not only after drilling into the request log.
    busiest = await db.fetchrow(
        """
        SELECT k.label AS key_label, COUNT(*) AS request_count
        FROM api_request_log r
        LEFT JOIN api_keys k ON k.id = r.api_key_id
        WHERE r.occurred_at >= NOW() - INTERVAL '24 hours'
        GROUP BY r.api_key_id, k.label
        ORDER BY request_count DESC, r.api_key_id
        LIMIT 1
        """
    )
    return templates.TemplateResponse(
        request,
        "admin/activity/index.html",
        {
            "user": user,
            "active_section": "activity",
            "counts": counts,
            "busiest": busiest,
        },
    )
