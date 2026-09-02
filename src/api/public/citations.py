"""Public citation provenance API (#319).

One generic router keyed on a canonical ``{entity_type}`` path segment (one of
:data:`src.core.citations.CITABLE_ENTITY_TYPES`) rather than a per-entity endpoint
in every router — citations are uniform across all seven citable types.

- ``POST /api/v1/citations/{entity_type}/{entity_id}/observations`` — the
  citation-native producer surface, **partial-success** (per-claim savepoint +
  disposition + reason slug), ``citations:write`` scope.
- ``GET /api/v1/citations/{entity_type}/{entity_id}`` — paginated read,
  ``citations:read`` scope.
"""

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response

from src.api.deps import get_db
from src.api.public.deps import AuthedKey, require_scope, stamped_transaction
from src.api.public.etag import NOT_MODIFIED, collection_etag, conditional_response
from src.api.public.schemas import (
    CitationListResponse,
    CitationObservationResult,
    CitationObservationsRequest,
    CitationObservationsResponse,
)
from src.core.citations import (
    CITABLE_ENTITY_TYPES,
    CitationClaim,
    apply_citation_observations,
)

router = APIRouter(prefix="/citations", tags=["public-api"])


def to_citation_claims(items) -> list[CitationClaim]:
    """Map API CitationObservationItem models → core CitationClaim dataclasses.

    Shared by the native endpoint and the embedded transport (citations[] on an
    org/person observation payload).
    """
    return [
        CitationClaim(
            field_name=c.field_name,
            url=c.url,
            title=c.title,
            excerpt=c.excerpt,
            accessed_at=c.accessed_at,
            op=c.op,
            pm_citation_id=c.pm_citation_id,
        )
        for c in items
    ]


def _validate_entity_type(entity_type: str) -> None:
    if entity_type not in CITABLE_ENTITY_TYPES:
        raise HTTPException(
            status_code=422,
            detail=f"entity_type must be one of {sorted(CITABLE_ENTITY_TYPES)}",
        )


@router.post(
    "/{entity_type}/{entity_id}/observations",
    response_model=CitationObservationsResponse,
    operation_id="submitCitationObservations",
)
async def submit_citation_observations(
    entity_type: str,
    entity_id: str,
    request: CitationObservationsRequest,
    auth: AuthedKey = Depends(require_scope("citations:write")),
    db=Depends(get_db),
) -> CitationObservationsResponse:
    """Observe/retract source citations on an entity, **partial-success** (#319).

    Each claim lands independently under its own savepoint: one rejection (e.g. a
    typo'd ``field_name`` → ``citable_field_unknown``, or a not-yet-anchored target
    → ``entity_unresolved``) never rolls back its siblings. ``pm_citation_id``
    addresses an existing citation for id-scoped refine/retract; absent → a
    natural-key observe (identity = entity/field/url; refine-or-create).
    """
    _validate_entity_type(entity_type)
    claims = to_citation_claims(request.citations)
    async with stamped_transaction(db, auth.key_id):
        results = await apply_citation_observations(db, entity_type, entity_id, auth.key_id, claims)
    return CitationObservationsResponse(
        results=[
            CitationObservationResult(
                disposition=r.disposition.value,
                citation_id=r.citation_id,
                reason=r.reason,
                attached_archived=r.attached_archived or None,
            )
            for r in results
        ]
    )


_VERSION_SQL = """
    SELECT count(*) AS n, max(updated_at) AS last
    FROM citations
    WHERE entity_type = $1 AND entity_id = $2
      AND ($3::boolean OR archived_at IS NULL)
      AND ($4::text IS NULL OR field_name IS NOT DISTINCT FROM $4)
"""


@router.get(
    "/{entity_type}/{entity_id}",
    response_model=CitationListResponse,
    operation_id="listCitations",
    responses=NOT_MODIFIED,
)
async def list_citations(
    entity_type: str,
    entity_id: str,
    request: Request,
    response: Response,
    field_name: str | None = Query(default=None),
    include_archived: bool = Query(default=False),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    _: str = Depends(require_scope("citations:read")),
    db=Depends(get_db),
) -> Any:
    """Return citations for an entity, newest first.

    Optional ``field_name`` narrows to a single field's citations (omit for all,
    including whole-entity citations). ``include_archived=true`` includes retracted
    rows. Stable offset pagination — ``ORDER BY created_at DESC, id DESC`` ends on
    a unique column (#297).

    Conditional GET (#392): watermark validator over the *same* filter the body
    uses — a retract archives rather than deletes, so only a count taken over
    the active-only set moves when the default view loses a row.
    """
    _validate_entity_type(entity_type)

    version = await db.fetchrow(_VERSION_SQL, entity_type, entity_id, include_archived, field_name)
    etag = collection_etag(
        f"{entity_type}-{entity_id}-citations",
        version["n"],
        version["last"],
        field_name,
        include_archived,
        limit,
        offset,
    )
    cached = conditional_response(request, response, etag, version["last"])
    if cached is not None:
        return cached

    rows = await db.fetch(
        """
        SELECT id, entity_type, entity_id, field_name, url, title, excerpt,
               accessed_at, archived_at, created_at, updated_at
        FROM citations
        WHERE entity_type = $1 AND entity_id = $2
          AND ($3::boolean OR archived_at IS NULL)
          AND ($4::text IS NULL OR field_name IS NOT DISTINCT FROM $4)
        ORDER BY created_at DESC, id DESC
        LIMIT $5 OFFSET $6
        """,
        entity_type,
        entity_id,
        include_archived,
        field_name,
        limit + 1,
        offset,
    )

    has_more = len(rows) > limit
    page = rows[:limit]
    return {
        "data": [dict(r) for r in page],
        "meta": {
            "limit": limit,
            "offset": offset,
            "count": len(page),
            "has_more": has_more,
        },
    }
