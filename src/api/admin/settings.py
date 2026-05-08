"""Admin settings landing page."""

from fastapi import APIRouter, Depends, Request
from fastapi.templating import Jinja2Templates

from src.api.admin.deps import (
    AdminUser,
    get_db,
    provision_app_user,
)
from src.api.admin.org_dups import get_org_dup_count
from src.api.admin.people_dups import get_person_dup_count
from src.core.types import ORG_NAME_TYPES, PERSON_NAME_TYPES

templates = Jinja2Templates(directory="src/templates")
router = APIRouter(prefix="/settings", tags=["admin-settings"])


def _base_ctx(user, org_dup_count, person_dup_count, active_section: str = "settings"):
    return {
        "user": user,
        "active_section": active_section,
        "org_dup_count": org_dup_count,
        "person_dup_count": person_dup_count,
    }


@router.get("/")
async def settings_index(
    request: Request,
    user: AdminUser = Depends(provision_app_user),
    db=Depends(get_db),
    org_dup_count: int = Depends(get_org_dup_count),
    person_dup_count: int = Depends(get_person_dup_count),
):
    counts = await db.fetchrow(
        """
        SELECT
            (SELECT COUNT(*) FROM link_types WHERE is_social = FALSE) AS general_link_types,
            (SELECT COUNT(*) FROM link_types WHERE is_social = TRUE)  AS social_link_types,
            (SELECT COUNT(*) FROM entity_identifier_types)            AS identifier_types,
            (SELECT COUNT(*) FROM api_keys WHERE user_id = $1)        AS api_keys
        """,
        user.id,
    )
    return templates.TemplateResponse(
        request,
        "admin/settings/index.html",
        {
            **_base_ctx(user, org_dup_count, person_dup_count),
            "counts": counts,
            "person_name_types": PERSON_NAME_TYPES,
            "org_name_types": ORG_NAME_TYPES,
        },
    )
