"""Public API v1 — organization endpoints."""

from typing import Any

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response

from src.api.deps import get_db
from src.api.public.deps import AuthedKey, identifier_filter, require_api_key, require_scope
from src.api.public.schemas import (
    EntityEventsResponse,
    ObservationResponse,
    OrganizationObservationRequest,
    OrgDetail,
    OrgSearchResponse,
    fmt_ts,
    make_etag,
)
from src.core.observation import (
    Disposition,
    IdentifierConflict,
    ObservationRejected,
    lookup_org_parent_by_acronym,
    lookup_org_parent_by_name,
    resolve_entity,
    write_additional_identifiers,
    write_addresses,
    write_contact_methods,
    write_links,
    write_names,
    write_org_acronyms,
    write_org_parent,
)

router = APIRouter(prefix="/orgs", tags=["public-api"])

_REJECTED_OBS = ObservationResponse(disposition="rejected", entity_id=None, entity_type=None)


@router.post(
    "/observations",
    response_model=ObservationResponse,
    operation_id="submitOrgObservation",
)
async def submit_org_observation(
    request: OrganizationObservationRequest,
    auth: AuthedKey = Depends(require_scope("observations:write")),
    db=Depends(get_db),
) -> ObservationResponse:
    """Submit an organization identity observation; attach to existing org or create a new one."""
    entity_id, entity_type, disposition = await resolve_entity(
        db, request.identifier_type, request.identifier_value
    )

    if disposition is Disposition.REJECTED:
        return _REJECTED_OBS
    if entity_type != "organization":
        return _REJECTED_OBS

    try:
        async with db.transaction():
            await write_names(db, entity_id, entity_type, auth.key_id, request.names)
            await write_links(db, entity_id, entity_type, request.links)
            await write_contact_methods(db, entity_id, entity_type, request.contact_methods)
            await write_addresses(db, entity_id, entity_type, request.addresses)
            await write_org_acronyms(db, entity_id, request.org_acronyms)

            parent_id: str | None = None
            if request.organization_parent_id:
                parent_id = request.organization_parent_id
            elif request.organization_parent_name:
                parent_id = await lookup_org_parent_by_name(db, request.organization_parent_name)
            elif request.organization_parent_acronym:
                parent_id = await lookup_org_parent_by_acronym(
                    db, request.organization_parent_acronym
                )

            if parent_id:
                await write_org_parent(db, entity_id, parent_id)

            await write_additional_identifiers(db, entity_id, request.additional_identifiers)
    except (
        ObservationRejected,
        IdentifierConflict,
        asyncpg.CheckViolationError,
        asyncpg.ForeignKeyViolationError,
        asyncpg.UniqueViolationError,
    ):
        return _REJECTED_OBS

    return ObservationResponse(
        disposition=disposition.value,
        entity_id=entity_id,
        entity_type=entity_type,
    )


def _org_row_to_dict(r: Any) -> dict[str, Any]:
    """Map an org row to the common base fields shared by search and detail."""
    acronym = r["acronym"]
    return {
        "id": r["id"],
        "name": r["name"],
        "acronym": acronym,
        # slug derived from canonical acronym (lower); null when no acronym exists
        "slug": acronym.lower() if acronym else None,
        "parent_id": r["parent_id"],
        "archived_at": r["archived_at"],  # datetime; serialized to Z-suffix by schema
    }


@router.get(
    "/search",
    response_model=OrgSearchResponse,
    operation_id="searchOrgs",
)
async def search_orgs(
    q: str = Query(default=""),
    limit: int = Query(default=10, ge=1),
    offset: int = Query(default=0, ge=0),
    include_archived: bool = Query(default=False),
    id_filter: tuple[str | None, str | None] = Depends(identifier_filter),
    _: str = Depends(require_api_key),
    db=Depends(get_db),
) -> Any:
    """Search organizations by name, acronym, or name variant.

    When identifier_type and identifier_value are both supplied they take precedence
    over q and return at most one result with has_more always false.
    """
    limit = min(limit, 50)
    id_type, id_value = id_filter

    if id_type is not None:
        rows = await db.fetch(
            """
            SELECT
                o.id,
                n.name,
                a.acronym,
                o.parent_id,
                o.archived_at
            FROM organizations o
            JOIN identifiers i ON i.entity_id = o.id
            JOIN entity_identifier_types t ON t.id = i.entity_identifier_type_id
            LEFT JOIN organization_names n ON n.organization_id = o.id AND n.is_canonical = TRUE
            LEFT JOIN organization_acronyms a ON a.organization_id = o.id AND a.is_canonical = TRUE
            WHERE t.slug = $1
              AND i.value = $2
              AND t.entity_type = 'organization'
              AND ($3 OR o.archived_at IS NULL)
            LIMIT 1
            """,
            id_type,
            id_value,
            include_archived,
        )
        return {
            "data": [_org_row_to_dict(r) for r in rows],
            "meta": {
                "limit": limit,
                "offset": offset,
                "count": len(rows),
                "has_more": False,
            },
        }

    if not q.strip():
        return {
            "data": [],
            "meta": {"limit": limit, "offset": offset, "count": 0, "has_more": False},
        }

    # Fetch limit+1 to determine has_more without a COUNT(*) query.
    rows = await db.fetch(
        """
        SELECT
            o.id,
            n.name,
            a.acronym,
            o.parent_id,
            o.archived_at
        FROM organizations o
        LEFT JOIN organization_names n ON n.organization_id = o.id AND n.is_canonical = TRUE
        LEFT JOIN organization_acronyms a ON a.organization_id = o.id AND a.is_canonical = TRUE
        WHERE ($4 OR o.archived_at IS NULL)
          AND (
              n.name ILIKE $1
              OR a.acronym ILIKE $1
              OR EXISTS (
                  SELECT 1 FROM organization_names v
                  WHERE v.organization_id = o.id AND v.name ILIKE $1
              )
          )
        ORDER BY
            CASE WHEN n.name ILIKE $1 THEN 0
                 WHEN a.acronym ILIKE $1 THEN 1
                 ELSE 2
            END,
            n.name NULLS LAST
        LIMIT $2 OFFSET $3
        """,
        f"%{q}%",
        limit + 1,
        offset,
        include_archived,
    )

    has_more = len(rows) > limit
    page = rows[:limit]

    return {
        "data": [_org_row_to_dict(r) for r in page],
        "meta": {
            "limit": limit,
            "offset": offset,
            "count": len(page),
            "has_more": has_more,
        },
    }


@router.get(
    "/{org_id}",
    response_model=OrgDetail,
    operation_id="getOrg",
)
async def get_org(
    org_id: str,
    request: Request,
    response: Response,
    _: str = Depends(require_api_key),
    db=Depends(get_db),
) -> Any:
    """Return full org record with names, acronyms, and identifiers."""
    row = await db.fetchrow(
        """
        SELECT
            o.id,
            o.parent_id,
            o.archived_at,
            o.updated_at,
            n.name,
            a.acronym
        FROM organizations o
        LEFT JOIN organization_names n ON n.organization_id = o.id AND n.is_canonical = TRUE
        LEFT JOIN organization_acronyms a ON a.organization_id = o.id AND a.is_canonical = TRUE
        WHERE o.id = $1
        """,
        org_id,
    )
    if not row:
        raise HTTPException(status_code=404, detail="Organization not found")

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

    names, acronyms, identifiers = await _fetch_detail_arrays(org_id, db)

    return {
        **_org_row_to_dict(row),
        "names": [dict(n) for n in names],
        "acronyms": [dict(a) for a in acronyms],
        "identifiers": [dict(i) for i in identifiers],
    }


def _row_to_event(r: Any) -> dict:
    """Map an entity_events row (with joined event_type fields) to a response dict."""
    return {
        "id": r["id"],
        "event_type": {
            "id": r["event_type_id"],
            "slug": r["event_type_slug"],
            "display_name": r["event_type_display_name"],
        },
        "date": {
            "year": r["event_year"],
            "month": r["event_month"],
            "day": r["event_day"],
            "hour": r["event_hour"],
            "minute": r["event_minute"],
            "second": r["event_second"],
            "at": fmt_ts(r["event_at"]) if r["event_at"] else None,
        },
        "event_place_text": r["event_place_text"],
        "linked_entity_type": r["linked_entity_type"],
        "linked_entity_id": r["linked_entity_id"],
        "notes": r["notes"],
        "visibility": r["visibility"],
        "verified_at": r["verified_at"],
        "created_at": r["created_at"],
    }


@router.get(
    "/{org_id}/events",
    response_model=EntityEventsResponse,
    operation_id="listOrgEvents",
)
async def list_org_events(
    org_id: str,
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    _: str = Depends(require_api_key),
    db=Depends(get_db),
) -> Any:
    """Return public, active lifecycle events for an organization."""
    exists = await db.fetchval("SELECT 1 FROM organizations WHERE id = $1", org_id)
    if not exists:
        raise HTTPException(status_code=404, detail="Organization not found")

    rows = await db.fetch(
        """
        SELECT
            ee.id,
            ee.event_year, ee.event_month, ee.event_day,
            ee.event_hour, ee.event_minute, ee.event_second,
            ee.event_at,
            ee.event_place_text,
            ee.linked_entity_type, ee.linked_entity_id,
            ee.notes, ee.visibility, ee.verified_at, ee.created_at,
            eet.id AS event_type_id,
            eet.slug AS event_type_slug,
            eet.display_name AS event_type_display_name
        FROM entity_events ee
        JOIN entity_event_types eet ON eet.id = ee.event_type_id
        WHERE ee.entity_id = $1
          AND ee.entity_type = 'organization'
          AND ee.visibility = 'public'
          AND ee.archived_at IS NULL
        ORDER BY ee.event_year DESC NULLS LAST, ee.event_month DESC NULLS LAST,
                 ee.event_day DESC NULLS LAST, ee.created_at DESC
        LIMIT $2 OFFSET $3
        """,
        org_id,
        limit + 1,
        offset,
    )

    has_more = len(rows) > limit
    page = rows[:limit]

    return {
        "data": [_row_to_event(r) for r in page],
        "meta": {
            "limit": limit,
            "offset": offset,
            "count": len(page),
            "has_more": has_more,
        },
    }


async def _fetch_detail_arrays(org_id: str, db: Any) -> tuple:
    """Fetch names, acronyms, and identifiers for an org."""
    names = await db.fetch(
        """
        SELECT id, name, name_type, is_canonical
        FROM organization_names
        WHERE organization_id = $1
        ORDER BY is_canonical DESC, name_type, name
        """,
        org_id,
    )
    acronyms = await db.fetch(
        """
        SELECT id, acronym, is_canonical
        FROM organization_acronyms
        WHERE organization_id = $1
        ORDER BY is_canonical DESC, acronym
        """,
        org_id,
    )
    identifiers = await db.fetch(
        """
        SELECT i.id, i.entity_identifier_type_id AS type_id, t.slug AS type_slug, i.value
        FROM identifiers i
        JOIN entity_identifier_types t ON t.id = i.entity_identifier_type_id
        WHERE i.entity_id = $1 AND t.entity_type = 'organization'
        ORDER BY t.slug, i.value
        """,
        org_id,
    )
    return names, acronyms, identifiers
