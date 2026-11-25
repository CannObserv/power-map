"""Public API v1 — role endpoints."""

from typing import Any

import asyncpg
from fastapi import APIRouter, Depends, Query, Request, Response

from src.api.deps import get_db
from src.api.public.deps import (
    AuthedKey,
    identifier_filter,
    require_api_key,
    require_scope,
)
from src.api.public.etag import conditional_response, make_etag
from src.api.public.gone import DETAIL_RESPONSES, not_found_or_gone
from src.api.public.identifier_reads import fetch_identifiers, identifier_match
from src.api.public.schemas import (
    ObservationResponse,
    RoleDetail,
    RoleListResponse,
    RoleObservationRequest,
)
from src.core.identifier_taken import unique_violation_reason
from src.core.observation import (
    Disposition,
    IdentifierConflict,
    ObservationRejected,
    log_replaced_identifiers,
    resolve_role,
    write_additional_identifiers,
    write_addresses,
    write_contact_methods,
    write_links,
)
from src.core.refusals import ObservationReasonCode

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
    id_filter: tuple[str | None, str | None] = Depends(identifier_filter),
    _: str = Depends(require_api_key),
    db=Depends(get_db),
) -> Any:
    """Return a paginated list of roles, optionally filtered by organization.

    ``identifier_type`` + ``identifier_value`` (both or neither) narrow the list
    to roles holding that identifier (#808) — every holder, paginated as usual.
    """
    id_type, id_value = id_filter
    rows = await db.fetch(
        f"""
        SELECT r.id, r.organization_id, r.title, r.notes,
               r.established_on, r.abolished_on,
               r.role_type_id, rt.slug AS role_type_slug,
               r.jurisdiction_id, r.qualifier,
               r.archived_at, r.created_at, r.updated_at
        FROM roles r
        LEFT JOIN role_types rt ON rt.id = r.role_type_id
        WHERE ($1::TEXT IS NULL OR r.organization_id = $1)
          AND ($2 OR r.archived_at IS NULL)
          AND {identifier_match("role", "r.id", 5, 6)}
        -- r.id: unique tiebreaker for stable offset pagination under (org, title) ties (#297)
        ORDER BY r.organization_id, r.title, r.id
        LIMIT $3 OFFSET $4
        """,  # noqa: S608 — identifier_match interpolates fixed strings only
        organization_id,
        include_archived,
        limit + 1,
        offset,
        id_type,
        id_value,
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
    responses=DETAIL_RESPONSES,
)
async def get_role(
    role_id: str,
    request: Request,
    response: Response,
    _: str = Depends(require_api_key),
    db=Depends(get_db),
) -> Any:
    """Return a full role record with links, contact methods, addresses, and identifiers."""
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
        return await not_found_or_gone(db, "role", role_id, "Role not found")

    etag = make_etag(row["id"], row["updated_at"])
    cached = conditional_response(request, response, etag, row["updated_at"])
    if cached is not None:
        return cached

    links, contact_methods, addresses = await _fetch_role_arrays(role_id, db)
    return {
        **_role_row_to_dict(row),
        "links": [dict(r) for r in links],
        "contact_methods": [dict(r) for r in contact_methods],
        "addresses": [dict(r) for r in addresses],
        "identifiers": await fetch_identifiers(db, "role", role_id),
    }


async def _resolve_role(db: Any, req: RoleObservationRequest) -> tuple:
    """Match or create the role by its natural key, retrying once on a create race.

    The first attempt runs in a savepoint: a concurrent create of the same role
    loses on the unique index, which would otherwise abort the enclosing
    observation transaction. The retry then attaches to the winner's row. A second,
    persistent conflict propagates (the route answers ``db_constraint_violation``).
    """
    resolve_kwargs = dict(
        notes=req.notes,
        established_on=req.established_on,
        abolished_on=req.abolished_on,
        role_type=req.role_type,
        jurisdiction_id=req.jurisdiction_id,
        qualifier=req.qualifier,
    )
    try:
        async with db.transaction():
            return await resolve_role(db, req.organization_id, req.title, **resolve_kwargs)
    except asyncpg.UniqueViolationError:
        return await resolve_role(db, req.organization_id, req.title, **resolve_kwargs)


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
    """Submit a role observation; match by (organization_id, title) or by pm_role_id.

    Resolution and every payload write share one transaction (#456, roles since
    #808), so ``rejected`` means nothing was written — a role the natural key
    would have created included.
    """
    try:
        async with db.transaction():
            if req.identifier_type == "pm_role_id":
                role_id = await db.fetchval(
                    "SELECT id FROM roles WHERE id=$1 AND archived_at IS NULL",
                    req.identifier_value,
                )
                if role_id is None:
                    raise ObservationRejected(
                        ObservationReasonCode.PM_ID_NOT_FOUND,
                        f"pm_id_not_found: {req.identifier_value!r}",
                    )
                disposition = Disposition.AUTO_ATTACHED
            else:
                role_id, disposition, reason = await _resolve_role(db, req)
                if disposition is Disposition.REJECTED:
                    raise ObservationRejected.of(reason)

            await write_links(db, role_id, "role", req.links)
            await write_contact_methods(db, role_id, "role", req.contact_methods)
            await write_addresses(db, role_id, "role", req.addresses)
            replaced = await write_additional_identifiers(
                db, role_id, "role", req.additional_identifiers
            )
    except ObservationRejected as exc:
        return ObservationResponse.rejected(exc.code, exc.detail)
    except IdentifierConflict as exc:
        return ObservationResponse.rejected(
            ObservationReasonCode.IDENTIFIER_CONFLICT,
            f"identifier_conflict: {exc.identifier_type_slug!r}",
        )
    except asyncpg.UniqueViolationError as exc:
        # #812: a lost race on uq_identifiers_cannabis_observer is identifier_taken.
        refusal = await unique_violation_reason(db, exc)
        return ObservationResponse.rejected(refusal.code, refusal)
    except (asyncpg.CheckViolationError, asyncpg.ForeignKeyViolationError):
        return ObservationResponse.rejected(ObservationReasonCode.DB_CONSTRAINT_VIOLATION)

    log_replaced_identifiers("role", role_id, replaced)
    return ObservationResponse(
        disposition=disposition.value,
        entity_id=role_id,
        entity_type="role",
    )
