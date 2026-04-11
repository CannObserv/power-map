"""Admin router — mounts all entity sub-routers."""

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates

import src.core.db as db_module
from src.api.admin import activity as activity_module
from src.api.admin import entities as entities_module
from src.api.admin import imports as imports_module
from src.api.admin import orgs as orgs_module
from src.api.admin import orgs_acronyms as orgs_acronyms_module
from src.api.admin import orgs_addresses as orgs_addresses_module
from src.api.admin import orgs_contacts as orgs_contacts_module
from src.api.admin import orgs_identifiers as orgs_identifiers_module
from src.api.admin import orgs_links as orgs_links_module
from src.api.admin import orgs_merge as orgs_merge_module
from src.api.admin import orgs_names as orgs_names_module
from src.api.admin import orgs_roles as orgs_roles_module
from src.api.admin import people as people_module
from src.api.admin import people_addresses as people_addresses_module
from src.api.admin import people_assignments as people_assignments_module
from src.api.admin import people_contacts as people_contacts_module
from src.api.admin import people_identifiers as people_identifiers_module
from src.api.admin import people_links as people_links_module
from src.api.admin import people_merge as people_merge_module
from src.api.admin import people_names as people_names_module
from src.api.admin import role_assignments as role_assignments_module
from src.api.admin import roles as roles_module
from src.api.admin import roles_detail as roles_detail_module
from src.api.admin import settings as settings_module
from src.api.admin.deps import AdminUser, get_admin_user
from src.api.admin.org_dups import count_org_duplicates
from src.api.admin.people_dups import count_person_duplicates
from src.core.logging import get_logger

logger = get_logger(__name__)

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
    async with db_module.acquire() as db:
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
        try:
            org_dup_count = await count_org_duplicates(db)
        except Exception:
            org_dup_count = 0
        try:
            person_dup_count = await count_person_duplicates(db)
        except Exception:
            person_dup_count = 0
    return templates.TemplateResponse(
        request,
        "admin/dashboard.html",
        {
            "user": user,
            "active_section": "dashboard",
            "org_dup_count": org_dup_count,
            "person_dup_count": person_dup_count,
            "counts": counts,
        },
    )


admin_router.include_router(entities_module.router)
admin_router.include_router(imports_module.router)
admin_router.include_router(settings_module.router)
admin_router.include_router(activity_module.router)
admin_router.include_router(orgs_module.router)
admin_router.include_router(orgs_merge_module.router)
admin_router.include_router(orgs_names_module.router)
admin_router.include_router(orgs_acronyms_module.router)
admin_router.include_router(orgs_addresses_module.router)
admin_router.include_router(orgs_contacts_module.router)
admin_router.include_router(orgs_links_module.router)
admin_router.include_router(orgs_identifiers_module.router)
admin_router.include_router(orgs_roles_module.router)
admin_router.include_router(people_module.router)
admin_router.include_router(people_merge_module.router)
admin_router.include_router(people_names_module.router)
admin_router.include_router(people_contacts_module.router)
admin_router.include_router(people_addresses_module.router)
admin_router.include_router(people_links_module.router)
admin_router.include_router(people_identifiers_module.router)
admin_router.include_router(people_assignments_module.router)
admin_router.include_router(roles_module.router)
admin_router.include_router(roles_detail_module.router)
admin_router.include_router(role_assignments_module.router)
