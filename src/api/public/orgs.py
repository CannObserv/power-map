"""Public API v1 — organization endpoints."""

from typing import Any

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response

from src.api.deps import get_db
from src.api.public.citations import to_citation_claims
from src.api.public.deps import AuthedKey, identifier_filter, require_api_key, require_scope
from src.api.public.etag import NOT_MODIFIED, conditional_response, make_etag
from src.api.public.events import (
    events_collection_validator,
    row_to_event,
)
from src.api.public.schemas import (
    CitationObservationResult,
    EntityEventsResponse,
    EventObservationResult,
    EventObservationsResponse,
    ObservationResponse,
    OrganizationObservationRequest,
    OrgDetail,
    OrgEventObservationsRequest,
    OrgSearchResponse,
)
from src.core.citations import write_citations
from src.core.observation import (
    Disposition,
    IdentifierConflict,
    ObservationRejected,
    apply_event_observations,
    lookup_org_parent_by_acronym,
    lookup_org_parent_by_name,
    resolve_entity,
    write_additional_identifiers,
    write_addresses,
    write_contact_methods,
    write_entity_events,
    write_links,
    write_names,
    write_org_acronyms,
    write_org_active,
    write_org_jurisdiction_affiliations,
    write_org_parent,
)

router = APIRouter(prefix="/orgs", tags=["public-api"])


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
    entity_id, entity_type, disposition, reason = await resolve_entity(
        db, request.identifier_type, request.identifier_value
    )

    if disposition is Disposition.REJECTED:
        return ObservationResponse(disposition="rejected", reason=reason)
    if entity_type != "organization":
        return ObservationResponse(
            disposition="rejected",
            reason=f"entity_type_mismatch: {entity_type!r}",
        )

    try:
        async with db.transaction():
            # Fail fast on an archived target before doing any write work (#240).
            if request.active is not None:
                await write_org_active(db, entity_id, request.active)
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
                # Authoritative (reparent) only when the org was id-addressed by
                # pm_org_id — the producer proves it means exactly this org
                # (#334). Natural / external-identifier matches stay write-if-null.
                await write_org_parent(
                    db,
                    entity_id,
                    parent_id,
                    source_key_id=auth.key_id,
                    authoritative=request.identifier_type == "pm_org_id",
                )

            await write_additional_identifiers(db, entity_id, request.additional_identifiers)
            event_results = await write_entity_events(
                db, entity_id, entity_type, auth.key_id, request.events
            )
            await write_org_jurisdiction_affiliations(
                db, entity_id, request.jurisdiction_affiliations
            )
            citation_results = await write_citations(
                db, entity_type, entity_id, auth.key_id, to_citation_claims(request.citations)
            )
    except ObservationRejected as exc:
        return ObservationResponse(disposition="rejected", reason=exc.detail)
    except IdentifierConflict as exc:
        return ObservationResponse(
            disposition="rejected",
            reason=f"identifier_conflict: {exc.identifier_type_slug!r}",
        )
    except (
        asyncpg.CheckViolationError,
        asyncpg.ForeignKeyViolationError,
        asyncpg.UniqueViolationError,
    ):
        return ObservationResponse(disposition="rejected", reason="db_constraint_violation")

    return ObservationResponse(
        disposition=disposition.value,
        entity_id=entity_id,
        entity_type=entity_type,
        events=[
            EventObservationResult(
                disposition=r.disposition.value, event_id=r.event_id, reason=r.reason
            )
            for r in event_results
        ]
        or None,
        citations=[
            CitationObservationResult(
                disposition=r.disposition.value, citation_id=r.citation_id, reason=r.reason
            )
            for r in citation_results
        ]
        or None,
    )


@router.post(
    "/{org_id}/events/observations",
    response_model=EventObservationsResponse,
    operation_id="submitOrgEventObservations",
)
async def submit_org_event_observations(
    org_id: str,
    request: OrgEventObservationsRequest,
    auth: AuthedKey = Depends(require_scope("observations:write")),
    db=Depends(get_db),
) -> EventObservationsResponse:
    """Observe lifecycle events on an org, **partial-success** (#321/#322).

    The event-native producer surface: each event lands independently under its
    own savepoint, so one rejected event (e.g. a ``succeeded_by`` whose successor
    isn't anchored yet → ``linked_entity_unresolved``) never rolls back its
    siblings. ``pm_event_id`` refines an event in place; absent it, a natural
    create with content dedup. ``op="retract"`` archives the ``pm_event_id``
    event — the only correction for a dateless linked event, so a re-link is
    create-new + retract-old in one batch (#322). Returns per-event dispositions
    + reason slugs.
    """
    exists = await db.fetchval("SELECT 1 FROM organizations WHERE id=$1", org_id)
    if not exists:
        raise HTTPException(status_code=404, detail="organization not found")

    async with db.transaction():
        results = await apply_event_observations(
            db, org_id, "organization", auth.key_id, request.events
        )
    return EventObservationsResponse(
        results=[
            EventObservationResult(
                disposition=r.disposition.value, event_id=r.event_id, reason=r.reason
            )
            for r in results
        ]
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
    jurisdiction: str | None = Query(default=None),
    id_filter: tuple[str | None, str | None] = Depends(identifier_filter),
    _: str = Depends(require_api_key),
    db=Depends(get_db),
) -> Any:
    """Search organizations by name, acronym, or name variant.

    Parameter precedence (first match wins, others ignored):
    1. identifier_type + identifier_value — returns at most one result, has_more=false.
    2. jurisdiction (slug or ULID) — filters to orgs with a governing affiliation; q further
       narrows by name when provided.
    3. q alone — substring search across names and acronyms.
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

    if jurisdiction is not None:
        return await _search_by_jurisdiction(
            db, jurisdiction, q.strip(), limit, offset, include_archived
        )

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
        FROM (SELECT pm_prefix_tsquery('pm_simple', $1) AS tsq) _q,
             organizations o
        LEFT JOIN organization_names n ON n.organization_id = o.id AND n.is_canonical = TRUE
        LEFT JOIN organization_acronyms a ON a.organization_id = o.id AND a.is_canonical = TRUE
        WHERE ($3 OR o.archived_at IS NULL)
          AND o.search_tsv @@ _q.tsq
        ORDER BY
            ts_rank(o.search_tsv, _q.tsq) DESC,
            n.name NULLS LAST,
            o.id  -- unique tiebreaker: stable offset pagination under rank/name ties (#297)
        LIMIT $2 OFFSET $4
        """,
        q.strip(),
        limit + 1,
        include_archived,
        offset,
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


async def _search_by_jurisdiction(
    db: Any,
    jurisdiction: str,
    q: str,
    limit: int,
    offset: int,
    include_archived: bool,
) -> dict[str, Any]:
    """Return orgs with a governing affiliation for the given jurisdiction slug or ULID.

    When q is non-empty, further narrows by substring match on names and acronyms.
    """
    # $5 is nullable (q omitted → NULL); a lateral subquery would need an extra guard,
    # so pm_prefix_tsquery is called twice instead. It is STRICT, so both calls return
    # NULL when $5 is NULL, which is safe: the WHERE short-circuits via IS NULL and
    # ORDER BY ranks as NULL LAST.
    rows = await db.fetch(
        """
        SELECT
            o.id,
            n.name,
            a.acronym,
            o.parent_id,
            o.archived_at
        FROM organizations o
        JOIN organization_jurisdiction_affiliations aff ON aff.organization_id = o.id
        JOIN organization_jurisdiction_affiliation_types at_ ON at_.id = aff.affiliation_type_id
        JOIN jurisdictions j ON j.id = aff.jurisdiction_id
        LEFT JOIN organization_names n ON n.organization_id = o.id AND n.is_canonical = TRUE
        LEFT JOIN organization_acronyms a ON a.organization_id = o.id AND a.is_canonical = TRUE
        WHERE at_.slug = 'governing'
          AND (j.id = $1 OR j.slug = $1)
          AND ($2 OR o.archived_at IS NULL)
          AND ($5::text IS NULL OR o.search_tsv @@ pm_prefix_tsquery('pm_simple', $5))
        ORDER BY
            ts_rank(o.search_tsv, pm_prefix_tsquery('pm_simple', $5)) DESC NULLS LAST,
            n.name NULLS LAST,
            o.id  -- unique tiebreaker: stable offset pagination under rank/name ties (#297)
        LIMIT $3 OFFSET $4
        """,
        jurisdiction,
        include_archived,
        limit + 1,
        offset,
        q.strip() if q else None,
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
    responses=NOT_MODIFIED,
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
            o.active,
            o.created_at,
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
    cached = conditional_response(request, response, etag, row["updated_at"])
    if cached is not None:
        return cached

    names, acronyms, identifiers, affiliations = await _fetch_detail_arrays(org_id, db)

    return {
        **_org_row_to_dict(row),
        # active is detail-only (#240) — added here, not in _org_row_to_dict
        # (shared with search, whose queries do not select o.active).
        "active": row["active"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "names": [dict(n) for n in names],
        "acronyms": [dict(a) for a in acronyms],
        "identifiers": [dict(i) for i in identifiers],
        "jurisdiction_affiliations": [
            {
                "jurisdiction_id": r["jurisdiction_id"],
                "affiliation_type": {
                    "id": r["affiliation_type_id"],
                    "slug": r["affiliation_type_slug"],
                    "display_name": r["affiliation_type_display_name"],
                },
            }
            for r in affiliations
        ],
    }


@router.get(
    "/{org_id}/events",
    response_model=EntityEventsResponse,
    operation_id="listOrgEvents",
    responses=NOT_MODIFIED,
)
async def list_org_events(
    org_id: str,
    request: Request,
    response: Response,
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    _: str = Depends(require_api_key),
    db=Depends(get_db),
) -> Any:
    """Return public, active lifecycle events for an organization."""
    exists = await db.fetchval("SELECT 1 FROM organizations WHERE id = $1", org_id)
    if not exists:
        raise HTTPException(status_code=404, detail="Organization not found")

    etag, last = await events_collection_validator(db, org_id, "organization", limit, offset)
    cached = conditional_response(request, response, etag, last)
    if cached is not None:
        return cached

    rows = await db.fetch(
        """
        SELECT
            ee.id,
            ee.event_year, ee.event_month, ee.event_day,
            ee.event_hour, ee.event_minute, ee.event_second,
            ee.event_at,
            ee.event_place_text, ee.event_place_address_id,
            ee.linked_entity_type, ee.linked_entity_id,
            ee.notes, ee.visibility, ee.verified_at, ee.created_at,
            eet.id AS event_type_id,
            eet.slug AS event_type_slug,
            eet.display_name AS event_type_display_name,
            pa.city AS place_city, pa.region AS place_region,
            pa.standardized AS place_standardized, pa.precision AS place_precision
        FROM entity_events ee
        JOIN entity_event_types eet ON eet.id = ee.event_type_id
        LEFT JOIN addresses pa ON pa.id = ee.event_place_address_id
        WHERE ee.entity_id = $1
          AND ee.entity_type = 'organization'
          AND ee.visibility = 'public'
          AND ee.archived_at IS NULL
        -- ee.id: unique tiebreaker for stable offset pagination under date/created_at ties (#297)
        ORDER BY ee.event_year DESC NULLS LAST, ee.event_month DESC NULLS LAST,
                 ee.event_day DESC NULLS LAST, ee.created_at DESC, ee.id DESC
        LIMIT $2 OFFSET $3
        """,
        org_id,
        limit + 1,
        offset,
    )

    has_more = len(rows) > limit
    page = rows[:limit]

    return {
        "data": [row_to_event(r) for r in page],
        "meta": {
            "limit": limit,
            "offset": offset,
            "count": len(page),
            "has_more": has_more,
        },
    }


async def _fetch_detail_arrays(org_id: str, db: Any) -> tuple:
    """Fetch names, acronyms, identifiers, and jurisdiction affiliations for an org."""
    names = await db.fetch(
        """
        SELECT id, name, name_type, is_canonical, effective_start, effective_end
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
        WHERE i.entity_id = $1 AND t.entity_type = 'organization' AND NOT t.is_internal
        ORDER BY t.slug, i.value
        """,
        org_id,
    )
    affiliations = await db.fetch(
        """
        SELECT
            aff.jurisdiction_id,
            at_.id  AS affiliation_type_id,
            at_.slug AS affiliation_type_slug,
            at_.display_name AS affiliation_type_display_name
        FROM organization_jurisdiction_affiliations aff
        JOIN organization_jurisdiction_affiliation_types at_ ON at_.id = aff.affiliation_type_id
        WHERE aff.organization_id = $1
        ORDER BY at_.slug, aff.jurisdiction_id
        """,
        org_id,
    )
    return names, acronyms, identifiers, affiliations
