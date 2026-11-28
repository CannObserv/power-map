"""Public API: the organization-kind catalog (#877 phase 3c, design D9).

The vocabulary of ``org_kind_slug`` on org reads: each kind's slug, display
name and description. The description is the meaning the classifiers are asked
to apply, so a consumer reads the same definition PM decided against.
"""

from typing import Any

from fastapi import APIRouter, Depends, Request, Response

from src.api.deps import get_db
from src.api.public.deps import require_api_key
from src.api.public.etag import NOT_MODIFIED, catalog_validator, conditional_response
from src.api.public.schemas import OrgKind, OrgKindsResponse

router = APIRouter()


@router.get(
    "/org-kinds",
    response_model=OrgKindsResponse,
    operation_id="list_org_kinds",
    responses=NOT_MODIFIED,
)
async def list_org_kinds(
    request: Request,
    response: Response,
    _user_id: str = Depends(require_api_key),
    db=Depends(get_db),
) -> Any:
    """Return every organization kind, by slug."""
    rows = await db.fetch("SELECT slug, display_name, description FROM org_kinds ORDER BY slug")
    # Content-hash validator (#392), as role-types: a seeded catalog with no
    # updated_at, reconciled in place, so only hashing the rows is exact.
    cached = conditional_response(request, response, catalog_validator(rows))
    if cached is not None:
        return cached
    return OrgKindsResponse(data=[OrgKind(**dict(r)) for r in rows])
