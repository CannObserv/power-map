"""Admin router — mounts all entity sub-routers."""

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates

from src.api.admin.deps import AdminUser, get_admin_user

templates = Jinja2Templates(directory="src/templates")
admin_router = APIRouter(prefix="/admin")


@admin_router.get("/")
async def dashboard(
    request: Request,
    user: AdminUser | RedirectResponse = Depends(get_admin_user),
):
    """Admin dashboard landing page."""
    if isinstance(user, RedirectResponse):
        return user
    return templates.TemplateResponse(
        request,
        "admin/dashboard.html",
        {
            "user": user,
            "active_section": "dashboard",
            "nav_items": [
                {"label": "People", "url": "/admin/people/", "count": "—"},
                {"label": "Organizations", "url": "/admin/orgs/", "count": "—"},
                {"label": "Roles", "url": "/admin/roles/", "count": "—"},
                {"label": "Assignments", "url": "/admin/role-assignments/", "count": "—"},
                {"label": "Import History", "url": "/admin/imports/", "count": "—"},
            ],
        },
    )
