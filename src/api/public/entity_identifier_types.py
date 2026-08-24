"""Public API: identifier types lookup endpoint (#459).

Exposes the ``entity_identifier_types`` catalog — the identity vocabulary a
producer addresses entities by. The last of the four observation vocabularies to
get a public catalog (``role_types``, ``link_types``, ``entity_event_types``
already had one), and the one that most needed it: an unknown ``identifier_type``
slug is rejected, but a *valid-but-wrong* one silently mints a duplicate entity,
and identity duplication propagates into every assignment, event and citation
hung off the fork.

Deliberately unfiltered. The catalog is tens of rows, its siblings take no
filters either, and an ``entity_type=`` param would have to be baked into the
ETag by hand — see ``EntityIdentifierTypesResponse``.
"""

from typing import Any

from fastapi import APIRouter, Depends, Request, Response

from src.api.deps import get_db
from src.api.public.deps import require_api_key
from src.api.public.etag import NOT_MODIFIED, catalog_validator, conditional_response
from src.api.public.schemas import EntityIdentifierType, EntityIdentifierTypesResponse

router = APIRouter()


@router.get(
    "/entity-identifier-types",
    response_model=EntityIdentifierTypesResponse,
    operation_id="list_entity_identifier_types",
    responses=NOT_MODIFIED,
)
async def list_entity_identifier_types(
    request: Request,
    response: Response,
    _user_id: str = Depends(require_api_key),
    db=Depends(get_db),
) -> Any:
    """Return all registered identifier types (the identity vocabulary)."""
    rows = await db.fetch(
        "SELECT id, slug, entity_type, display_name, full_name, is_internal"
        " FROM entity_identifier_types ORDER BY slug"
    )
    # Content-hash validator (#392): this catalog has no ``updated_at`` to
    # watermark, and a count + max(created_at) tag would be stable across an
    # in-place edit. Not hypothetical here — ``settings_identifier_types.py`` is
    # full admin CRUD and UPDATEs slug/display_name/is_internal in place, so a
    # 304ing consumer could hold a slug that no longer resolves. ``slug`` is
    # UNIQUE, so the ORDER BY is total and the hash stable.
    cached = conditional_response(request, response, catalog_validator(rows))
    if cached is not None:
        return cached
    return EntityIdentifierTypesResponse(data=[EntityIdentifierType(**dict(r)) for r in rows])
