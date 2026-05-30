"""Public API: link types lookup endpoint."""

from fastapi import APIRouter, Depends

from src.api.deps import get_db
from src.api.public.deps import require_api_key
from src.api.public.schemas import LinkType, LinkTypesResponse

router = APIRouter()


@router.get(
    "/link-types",
    response_model=LinkTypesResponse,
    operation_id="list_link_types",
)
async def list_link_types(
    _user_id: str = Depends(require_api_key),
    db=Depends(get_db),
) -> LinkTypesResponse:
    """Return all available link types."""
    rows = await db.fetch("SELECT id, slug, display_name, is_social FROM link_types ORDER BY slug")
    return LinkTypesResponse(data=[LinkType(**dict(r)) for r in rows])
