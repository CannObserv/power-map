"""Public API v1 — role endpoints."""

from typing import Any

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response

from src.api.deps import get_db
from src.api.public.deps import AuthedKey, require_api_key, require_scope
from src.api.public.schemas import (
    NOT_MODIFIED,
    ObservationResponse,
    RoleDetail,
    RoleListResponse,
    RoleObservationRequest,
    make_etag,
)
from src.core.observation import (
    Disposition,
    resolve_role,
    write_addresses,
    write_contact_methods,
    write_links,
)

router = APIRouter(prefix="/roles", tags=["public-api"])


def _role_row_to_dict(r: Any) -> dict[str, Any]:
    return {
        "id": r["id"],
        "organization_id": r["organization_id"],
        "title": r["title"],
        "notes": r["notes"],
        "established_on": r["established_on"],
        "abolished_on": r["abolished_on"],
        "role_type_id": r["role_type_id"],
        "role_type_slug": r["role_type_slug"],
        "jurisdiction_id": r["jurisdiction_id"],
        "qualifier": r["qualifier"],
        "archived_at": r["archived_at"],
        "created_at": r["created_at"],
        "updated_at": r["updated_at"],
    }


@router.get(
    "",
    response_model=RoleListResponse,
    operation_id="listRoles",
)
async def list_roles(
    organization_id: str | None = Query(default=None),
    include_archived: bool = Query(default=False),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    _: str = Depends(require_api_key),
    db=Depends(get_db),
) -> Any:
    """Return a paginated list of roles, optionally filtered by organization."""
    rows = await db.fetch(
        """
        SELECT r.id, r.organization_id, r.title, r.notes,
               r.established_on, r.abolished_on,
               r.role_type_id, rt.slug AS role_type_slug,
               r.jurisdiction_id, r.qualifier,
               r.archived_at, r.created_at, r.updated_at
        FROM roles r
        LEFT JOIN role_types rt ON rt.id = r.role_type_id
        WHERE ($1::TEXT IS NULL OR r.organization_id = $1)
          AND ($2 OR r.archived_at IS NULL)
        -- r.id: unique tiebreaker for stable offset pagination under (org, title) ties (#297)
        ORDER BY r.organization_id, r.title, r.id
        LIMIT $3 OFFSET $4
        """,
        organization_id,
        include_archived,
        limit + 1,
        offset,
    )

    has_more = len(rows) > limit
    page = rows[:limit]

    return {
        "data": [_role_row_to_dict(r) for r in page],
        "meta": {
            "limit": limit,
            "offset": offset,
            "count": len(page),
            "has_more": has_more,
        },
    }


async def _fetch_role_arrays(role_id: str, db: Any) -> tuple:
    """Fetch links, contact_methods, and addresses for a role."""
    links = await db.fetch(
        """
        SELECT l.id, l.url, l.link_type_id, l.is_active,
               lt.slug AS link_type_slug, lt.display_name AS link_type_name
        FROM links l
        JOIN link_types lt ON lt.id = l.link_type_id
        WHERE l.entity_type = 'role' AND l.entity_id = $1
        ORDER BY lt.slug, l.url
        """,
        role_id,
    )
    contact_methods = await db.fetch(
        """
        SELECT id, contact_type, value, display_label
        FROM contact_methods
        WHERE entity_type = 'role' AND entity_id = $1
        ORDER BY contact_type, value
        """,
        role_id,
    )
    addresses = await db.fetch(
        """
        SELECT ea.id, ea.address_id, ea.address_type, ea.valid_from, ea.valid_until,
               a.raw_input, a.standardized
        FROM entity_addresses ea
        JOIN addresses a ON a.id = ea.address_id
        WHERE ea.entity_type = 'role' AND ea.entity_id = $1
        ORDER BY ea.address_type, ea.valid_from DESC NULLS LAST
        """,
        role_id,
    )
    return links, contact_methods, addresses


@router.get(
    "/{role_id}",
    response_model=RoleDetail,
    operation_id="getRole",
    responses=NOT_MODIFIED,
)
async def get_role(
    role_id: str,
    request: Request,
    response: Response,
    _: str = Depends(require_api_key),
    db=Depends(get_db),
) -> Any:
    """Return a full role record with links, contact methods, and addresses."""
    row = await db.fetchrow(
        """
        SELECT r.id, r.organization_id, r.title, r.notes,
               r.established_on, r.abolished_on,
               r.role_type_id, rt.slug AS role_type_slug,
               r.jurisdiction_id, r.qualifier,
               r.archived_at, r.created_at, r.updated_at
        FROM roles r
        LEFT JOIN role_types rt ON rt.id = r.role_type_id
        WHERE r.id = $1
        """,
        role_id,
    )
    if not row:
        raise HTTPException(status_code=404, detail="Role not found")

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

    links, contact_methods, addresses = await _fetch_role_arrays(role_id, db)
    return {
        **_role_row_to_dict(row),
        "links": [dict(r) for r in links],
        "contact_methods": [dict(r) for r in contact_methods],
        "addresses": [dict(r) for r in addresses],
    }


@router.post(
    "/observations",
    response_model=ObservationResponse,
    operation_id="submitRoleObservation",
)
async def submit_role_observation(
    req: RoleObservationRequest,
    auth: AuthedKey = Depends(require_scope("observations:write")),
    db=Depends(get_db),
) -> ObservationResponse:
    """Submit a role observation; match by (organization_id, title) or by pm_role_id."""
    if req.identifier_type == "pm_role_id":
        row = await db.fetchrow(
            "SELECT id FROM roles WHERE id=$1 AND archived_at IS NULL",
            req.identifier_value,
        )
        if row is None:
            return ObservationResponse(
                disposition="rejected", reason=f"pm_id_not_found: {req.identifier_value!r}"
            )
        role_id = row["id"]
        disposition = Disposition.AUTO_ATTACHED
    else:
        resolve_kwargs = dict(
            notes=req.notes,
            established_on=req.established_on,
            abolished_on=req.abolished_on,
            role_type=req.role_type,
            jurisdiction_id=req.jurisdiction_id,
            qualifier=req.qualifier,
        )
        try:
            role_id, disposition, reason = await resolve_role(
                db, req.organization_id, req.title, **resolve_kwargs
            )
        except asyncpg.UniqueViolationError:
            # Concurrent create of the same role: re-resolve so the loser
            # of the race attaches to the winner's row instead of 500-ing. One
            # retry only — a second, persistent conflict is treated as a genuine
            # error and propagates (500).
            role_id, disposition, reason = await resolve_role(
                db, req.organization_id, req.title, **resolve_kwargs
            )
        if disposition is Disposition.REJECTED:
            return ObservationResponse(disposition="rejected", reason=reason)

    try:
        async with db.transaction():
            await write_links(db, role_id, "role", req.links)
            await write_contact_methods(db, role_id, "role", req.contact_methods)
            await write_addresses(db, role_id, "role", req.addresses)
    except (
        asyncpg.CheckViolationError,
        asyncpg.ForeignKeyViolationError,
        asyncpg.UniqueViolationError,
    ):
        return ObservationResponse(disposition="rejected", reason="db_constraint_violation")

    return ObservationResponse(
        disposition=disposition.value,
        entity_id=role_id,
        entity_type="role",
    )
