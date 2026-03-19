"""Admin router — mounts all entity sub-routers."""

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates

from src.api.admin import imports as imports_module
from src.api.admin import lookups as lookups_module
from src.api.admin import orgs as orgs_module
from src.api.admin import people as people_module
from src.api.admin import role_assignments as role_assignments_module
from src.api.admin import roles as roles_module
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


admin_router.include_router(imports_module.router)
admin_router.include_router(lookups_module.router)
admin_router.include_router(orgs_module.router)
admin_router.include_router(people_module.router)
admin_router.include_router(roles_module.router)
admin_router.include_router(role_assignments_module.router)
