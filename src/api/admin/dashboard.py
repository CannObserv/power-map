"""Admin dashboard landing page."""

from fastapi import APIRouter, Depends, Request
from fastapi.templating import Jinja2Templates

import src.core.db as db_module
from src.api.admin.deps import AdminUser, get_admin_user
from src.api.admin.org_dups import count_org_duplicates
from src.api.admin.people_dups import count_person_duplicates
from src.core.logging import get_logger

logger = get_logger(__name__)

templates = Jinja2Templates(directory="src/templates")
router = APIRouter()


@router.get("/")
async def dashboard(
    request: Request,
    user: AdminUser = Depends(get_admin_user),
):
    """Admin dashboard landing page."""
    # Acquire directly rather than via Depends(get_db): the dashboard calls
    # count_org_duplicates / count_person_duplicates directly (not through their
    # FastAPI deps) and needs to wrap each in its own try/except.  Using
    # db_module.acquire() keeps a single connection for the whole handler while
    # allowing the dup-count calls to be isolated from one another.
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
