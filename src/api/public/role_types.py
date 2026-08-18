"""Public API: role types lookup endpoint (#268).

Exposes the ``role_types`` classifier catalog so producers of structured roles
can discover the match-key vocabulary (``slug``) instead of hardcoding it from a
design doc. Mirrors the link-types lookup endpoint.
"""

from typing import Any

from fastapi import APIRouter, Depends, Request, Response

from src.api.deps import get_db
from src.api.public.deps import require_api_key
from src.api.public.etag import NOT_MODIFIED, catalog_validator, conditional_response
from src.api.public.schemas import RoleType, RoleTypesResponse

router = APIRouter()


@router.get(
    "/role-types",
    response_model=RoleTypesResponse,
    operation_id="list_role_types",
    responses=NOT_MODIFIED,
)
async def list_role_types(
    request: Request,
    response: Response,
    _user_id: str = Depends(require_api_key),
    db=Depends(get_db),
) -> Any:
    """Return all role types (the structural-match vocabulary)."""
    rows = await db.fetch(
        "SELECT id, slug, display_name, expects_jurisdiction, requires_qualifier,"
        " forbids_qualifier FROM role_types ORDER BY slug"
    )
    # Content-hash validator (#392): this catalog has no ``updated_at`` to
    # watermark, and it is edited in place — a count + max(created_at) tag
    # would be stable across a rename. Hashing the rows is exact; the rows
    # are already fetched, so the win is serialization + transfer.
    cached = conditional_response(request, response, catalog_validator(rows))
    if cached is not None:
        return cached
    return RoleTypesResponse(data=[RoleType(**dict(r)) for r in rows])
