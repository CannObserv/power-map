"""Public API: link types lookup endpoint."""

from typing import Any

from fastapi import APIRouter, Depends, Request, Response

from src.api.deps import get_db
from src.api.public.deps import require_api_key
from src.api.public.etag import NOT_MODIFIED, catalog_validator, conditional_response
from src.api.public.schemas import LinkType, LinkTypesResponse

router = APIRouter()


@router.get(
    "/link-types",
    response_model=LinkTypesResponse,
    operation_id="list_link_types",
    responses=NOT_MODIFIED,
)
async def list_link_types(
    request: Request,
    response: Response,
    _user_id: str = Depends(require_api_key),
    db=Depends(get_db),
) -> Any:
    """Return all available link types."""
    rows = await db.fetch("SELECT id, slug, display_name, is_social FROM link_types ORDER BY slug")
    # Content-hash validator (#392): this catalog has no ``updated_at`` to
    # watermark, and it is edited in place — a count + max(created_at) tag
    # would be stable across a rename. Hashing the rows is exact; the rows
    # are already fetched, so the win is serialization + transfer.
    cached = conditional_response(request, response, catalog_validator(rows))
    if cached is not None:
        return cached
    return LinkTypesResponse(data=[LinkType(**dict(r)) for r in rows])
