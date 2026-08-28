"""Public role-assignment relationship API (#301).

A directional temporal edge between two role_assignments (staffer -> principal).

- ``POST /api/v1/assignment-relationships/observations`` — the producer surface,
  **partial-success** (per-claim savepoint + disposition + reason slug),
  ``assignment_relationships:write`` scope. pm-native: each claim references its
  endpoints by ``pm_assignment_id`` (identity = from + to + rel_type).
- ``GET /api/v1/assignments/{pm_assignment_id}/relationships`` — paginated read of
  every edge touching the assignment (either direction), ``assignment_relationships:read``.
"""

from typing import Any

from fastapi import APIRouter, Depends, Query, Request, Response

from src.api.deps import get_db
from src.api.public.deps import AuthedKey, require_scope
from src.api.public.etag import NOT_MODIFIED, collection_etag, conditional_response
from src.api.public.schemas import (
    RelationshipListResponse,
    RelationshipObservationResult,
    RelationshipObservationsRequest,
    RelationshipObservationsResponse,
)
from src.core.assignment_relationships import (
    RelationshipClaim,
    apply_relationship_observations,
)

router = APIRouter(tags=["public-api"])


def to_relationship_claims(items) -> list[RelationshipClaim]:
    """Map API RelationshipObservationItem models → core RelationshipClaim dataclasses."""
    return [
        RelationshipClaim(
            from_pm_assignment_id=r.from_pm_assignment_id,
            to_pm_assignment_id=r.to_pm_assignment_id,
            rel_type=r.rel_type,
            valid_from=r.valid_from,
            valid_until=r.valid_until,
            notes=r.notes,
            op=r.op,
            pm_relationship_id=r.pm_relationship_id,
        )
        for r in items
    ]


@router.post(
    "/assignment-relationships/observations",
    response_model=RelationshipObservationsResponse,
    operation_id="submitRelationshipObservations",
)
async def submit_relationship_observations(
    request: RelationshipObservationsRequest,
    auth: AuthedKey = Depends(require_scope("assignment_relationships:write")),
    db=Depends(get_db),
) -> RelationshipObservationsResponse:
    """Observe/retract role-assignment relationships, **partial-success** (#301).

    Each claim lands independently under its own savepoint: one rejection (e.g. a
    not-yet-anchored endpoint → ``assignment_unresolved``, or a foreign owner →
    ``provenance_conflict``) never rolls back its siblings. ``pm_relationship_id``
    addresses an existing edge for id-scoped refine/retract; absent → a natural-key
    observe (identity = from + to + rel_type; refine-or-create). Temporal windows
    are recorded freely here — the daily audit reconciles against endpoint windows.
    """
    claims = to_relationship_claims(request.relationships)
    async with db.transaction():
        results = await apply_relationship_observations(db, auth.key_id, claims)
    return RelationshipObservationsResponse(
        results=[
            RelationshipObservationResult(
                disposition=r.disposition.value,
                relationship_id=r.relationship_id,
                reason=r.reason,
                attached_archived=r.attached_archived or None,
            )
            for r in results
        ]
    )


_VERSION_SQL = """
    SELECT count(*) AS n, max(updated_at) AS last
    FROM role_assignment_relationships
    WHERE (from_assignment_id = $1 OR to_assignment_id = $1)
      AND ($2::boolean OR archived_at IS NULL)
"""


@router.get(
    "/assignments/{pm_assignment_id}/relationships",
    response_model=RelationshipListResponse,
    operation_id="listAssignmentRelationships",
    responses=NOT_MODIFIED,
)
async def list_assignment_relationships(
    pm_assignment_id: str,
    request: Request,
    response: Response,
    include_archived: bool = Query(default=False),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    _: AuthedKey = Depends(require_scope("assignment_relationships:read")),
    db=Depends(get_db),
) -> Any:
    """Return every relationship touching the assignment (either direction), newest first.

    ``include_archived=true`` includes retracted / cascade-archived edges. Stable
    offset pagination — ``ORDER BY created_at DESC, id DESC`` ends on a unique
    column (#297).

    Conditional GET (#392): watermark validator spanning *both* directions, so an
    inbound edge moves the tag too; the cascade (#301) archives edges when an
    endpoint shrinks, which the active-only count catches.
    """
    version = await db.fetchrow(_VERSION_SQL, pm_assignment_id, include_archived)
    etag = collection_etag(
        f"{pm_assignment_id}-relationships",
        version["n"],
        version["last"],
        include_archived,
        limit,
        offset,
    )
    cached = conditional_response(request, response, etag, version["last"])
    if cached is not None:
        return cached

    rows = await db.fetch(
        """
        SELECT r.id, r.from_assignment_id, r.to_assignment_id, t.slug AS rel_type,
               r.valid_from, r.valid_until, r.notes, r.archived_at,
               r.created_at, r.updated_at
        FROM role_assignment_relationships r
        JOIN role_assignment_relationship_types t ON t.id = r.rel_type_id
        WHERE (r.from_assignment_id = $1 OR r.to_assignment_id = $1)
          AND ($2::boolean OR r.archived_at IS NULL)
        ORDER BY r.created_at DESC, r.id DESC
        LIMIT $3 OFFSET $4
        """,
        pm_assignment_id,
        include_archived,
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
