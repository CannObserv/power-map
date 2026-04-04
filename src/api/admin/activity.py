"""Admin activity landing page."""

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates

from src.api.admin.deps import AdminUser, check_auth, get_admin_user, get_db
from src.api.admin.org_dups import get_org_dup_count
from src.api.admin.people_dups import get_person_dup_count

templates = Jinja2Templates(directory="src/templates")
router = APIRouter(prefix="/activity", tags=["admin-activity"])


@router.get("/")
async def activity_index(
    request: Request,
    user: AdminUser | RedirectResponse = Depends(get_admin_user),
    db=Depends(get_db),
    org_dup_count: int = Depends(get_org_dup_count),
    person_dup_count: int = Depends(get_person_dup_count),
):
    """Activity landing page — overview cards for all activity sections."""
    redirect, user = check_auth(user)
    if redirect:
        return redirect
    counts = await db.fetchrow(
        "SELECT COUNT(*) AS imports FROM import_batches"
    )
    return templates.TemplateResponse(
        request,
        "admin/activity/index.html",
        {
            "user": user,
            "active_section": "activity",
            "org_dup_count": org_dup_count,
            "person_dup_count": person_dup_count,
            "counts": counts,
        },
    )
