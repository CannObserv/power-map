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
    counts = await db.fetchrow("SELECT COUNT(*) AS imports FROM import_batches")
    return templates.TemplateResponse(
        request,
        "admin/activity/index.html",
        {
            "user": user,
            "active_section": "activity",
            "counts": counts,
        },
    )
