"""Public API: role types lookup endpoint (#268).

Exposes the ``role_types`` classifier catalog so producers of structured roles
can discover the match-key vocabulary (``slug``) instead of hardcoding it from a
design doc. Mirrors the link-types lookup endpoint.
"""

from fastapi import APIRouter, Depends

from src.api.deps import get_db
from src.api.public.deps import require_api_key
from src.api.public.schemas import RoleType, RoleTypesResponse

router = APIRouter()


@router.get(
    "/role-types",
    response_model=RoleTypesResponse,
    operation_id="list_role_types",
)
async def list_role_types(
    _user_id: str = Depends(require_api_key),
    db=Depends(get_db),
) -> RoleTypesResponse:
    """Return all role types (the structural-match vocabulary)."""
    rows = await db.fetch(
        "SELECT id, slug, display_name, expects_jurisdiction FROM role_types ORDER BY slug"
    )
    return RoleTypesResponse(data=[RoleType(**dict(r)) for r in rows])
