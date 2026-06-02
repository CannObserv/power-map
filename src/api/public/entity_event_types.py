"""Public API route for entity event types."""

from fastapi import APIRouter, Depends

from src.api.deps import get_db
from src.api.public.deps import require_api_key
from src.api.public.schemas import EntityEventType, EntityEventTypesResponse

router = APIRouter()


@router.get(
    "/entity-event-types",
    response_model=EntityEventTypesResponse,
    operation_id="list_entity_event_types",
)
async def list_entity_event_types(
    _user_id: str = Depends(require_api_key),
    db=Depends(get_db),
) -> EntityEventTypesResponse:
    """Return all available entity event types."""
    rows = await db.fetch(
        "SELECT id, slug, display_name, applies_to, requires_year, requires_linked_entity"
        " FROM entity_event_types ORDER BY slug"
    )
    return EntityEventTypesResponse(data=[EntityEventType(**dict(r)) for r in rows])
