"""Public API v1 — assignment endpoints."""

from typing import Any

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response

from src.api.deps import get_db
from src.api.public.deps import require_api_key, require_scope
from src.api.public.schemas import (
    AssignmentDetail,
    AssignmentListResponse,
    AssignmentObservationRequest,
    ObservationResponse,
    make_etag,
)
from src.core.observation import (
    Disposition,
    ObservationRejected,
    resolve_assignment,
    resolve_entity,
    write_addresses,
    write_contact_methods,
    write_links,
)

router = APIRouter(prefix="/assignments", tags=["public-api"])

_REJECTED_OBS = ObservationResponse(disposition="rejected", entity_id=None, entity_type=None)


def _row_to_dict(r: Any) -> dict[str, Any]:
    return {
        "id": r["id"],
        "person_id": r["person_id"],
        "role_id": r["role_id"],
        "is_current": r["is_current"],
        "start_date": r["start_date"],
        "end_date": r["end_date"],
        "notes": r["notes"],
        "archived_at": r["archived_at"],
        "created_at": r["created_at"],
        "updated_at": r["updated_at"],
    }


@router.get(
    "",
    response_model=AssignmentListResponse,
    operation_id="listAssignments",
)
async def list_assignments(
    person_id: str | None = Query(default=None),
    role_id: str | None = Query(default=None),
    include_archived: bool = Query(default=False),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    _: str = Depends(require_api_key),
    db=Depends(get_db),
) -> Any:
    """Return a paginated list of role assignments, optionally filtered."""
    rows = await db.fetch(
        """
        SELECT id, person_id, role_id, is_current, start_date, end_date,
               notes, archived_at, created_at, updated_at
        FROM role_assignments
        WHERE ($1::TEXT IS NULL OR person_id = $1)
          AND ($2::TEXT IS NULL OR role_id = $2)
          AND ($3 OR archived_at IS NULL)
        ORDER BY person_id, role_id, start_date
        LIMIT $4 OFFSET $5
        """,
        person_id,
        role_id,
        include_archived,
        limit + 1,
        offset,
    )

    has_more = len(rows) > limit
    page = rows[:limit]

    return {
        "data": [_row_to_dict(r) for r in page],
        "meta": {
            "limit": limit,
            "offset": offset,
            "count": len(page),
            "has_more": has_more,
        },
    }


async def _fetch_arrays(assignment_id: str, db: Any) -> tuple:
    """Fetch links, contact_methods, and addresses for an assignment."""
    links = await db.fetch(
        """
        SELECT l.id, l.url, l.link_type_id, l.is_active,
               lt.slug AS link_type_slug, lt.display_name AS link_type_name
        FROM links l
        JOIN link_types lt ON lt.id = l.link_type_id
        WHERE l.entity_type = 'role_assignment' AND l.entity_id = $1
        ORDER BY lt.slug, l.url
        """,
        assignment_id,
    )
    contact_methods = await db.fetch(
        """
        SELECT id, contact_type, value, display_label
        FROM contact_methods
        WHERE entity_type = 'role_assignment' AND entity_id = $1
        ORDER BY contact_type, value
        """,
        assignment_id,
    )
    addresses = await db.fetch(
        """
        SELECT ea.id, ea.address_id, ea.address_type, a.raw_input, a.standardized
        FROM entity_addresses ea
        JOIN addresses a ON a.id = ea.address_id
        WHERE ea.entity_type = 'role_assignment' AND ea.entity_id = $1
        ORDER BY ea.address_type
        """,
        assignment_id,
    )
    return links, contact_methods, addresses


@router.get(
    "/{assignment_id}",
    response_model=AssignmentDetail,
    operation_id="getAssignment",
)
async def get_assignment(
    assignment_id: str,
    request: Request,
    response: Response,
    _: str = Depends(require_api_key),
    db=Depends(get_db),
) -> Any:
    """Return a full assignment record with links, contact methods, and addresses."""
    row = await db.fetchrow(
        """
        SELECT id, person_id, role_id, is_current, start_date, end_date,
               notes, archived_at, created_at, updated_at
        FROM role_assignments
        WHERE id = $1
        """,
        assignment_id,
    )
    if not row:
        raise HTTPException(status_code=404, detail="Assignment not found")

    etag = make_etag(row["id"], row["updated_at"])
    cache_headers = {
        "ETag": etag,
        "Last-Modified": row["updated_at"].strftime("%a, %d %b %Y %H:%M:%S GMT"),
        "Cache-Control": "no-cache",
        "Vary": "X-API-Key",
    }
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers=cache_headers)

    for k, v in cache_headers.items():
        response.headers[k] = v

    links, contact_methods, addresses = await _fetch_arrays(assignment_id, db)
    return {
        **_row_to_dict(row),
        "links": [dict(r) for r in links],
        "contact_methods": [dict(r) for r in contact_methods],
        "addresses": [dict(r) for r in addresses],
    }


@router.post(
    "/observations",
    response_model=ObservationResponse,
    operation_id="submitAssignmentObservation",
)
async def submit_assignment_observation(
    req: AssignmentObservationRequest,
    _=Depends(require_scope("observations:write")),
    db=Depends(get_db),
) -> ObservationResponse:
    """Submit an assignment observation.

    Resolves by (person_id, role_id, start_date) or by pm_assignment_id.
    """
    if req.identifier_type == "pm_assignment_id":
        assignment_id, _, disposition = await resolve_entity(
            db, "pm_assignment_id", req.identifier_value
        )
        if disposition is Disposition.REJECTED:
            return _REJECTED_OBS
    else:
        assignment_id, disposition = await resolve_assignment(
            db,
            req.person_id,
            req.role_id,
            req.start_date,
            end_date=req.end_date,
            is_current=req.is_current,
            notes=req.notes,
        )
        if disposition is Disposition.REJECTED:
            return _REJECTED_OBS

    try:
        async with db.transaction():
            await write_links(db, assignment_id, "role_assignment", req.links)
            await write_contact_methods(db, assignment_id, "role_assignment", req.contact_methods)
            await write_addresses(db, assignment_id, "role_assignment", req.addresses)
    except (
        ObservationRejected,
        asyncpg.CheckViolationError,
        asyncpg.ForeignKeyViolationError,
        asyncpg.UniqueViolationError,
    ):
        return _REJECTED_OBS

    return ObservationResponse(
        disposition=disposition.value,
        entity_id=assignment_id,
        entity_type="role_assignment",
    )
