"""Public API v1 — people endpoints."""

from typing import Any

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response

from src.api.deps import get_db
from src.api.public.citations import to_citation_claims
from src.api.public.deps import (
    AuthedKey,
    identifier_filter,
    require_api_key,
    require_scope,
    stamped_transaction,
)
from src.api.public.etag import NOT_MODIFIED, conditional_response, make_etag
from src.api.public.events import (
    events_collection_validator,
    row_to_event,
)
from src.api.public.schemas import (
    CitationObservationResult,
    EntityEventsResponse,
    EventObservationResult,
    ObservationResponse,
    PeopleObservationRequest,
    PersonDetail,
    PersonSearchResponse,
)
from src.core.citations import write_citations
from src.core.db import visible_names_filter
from src.core.embedding_registry import EmbeddingRegistry
from src.core.observation import (
    Disposition,
    IdentifierConflict,
    ObservationRejected,
    resolve_entity,
    write_additional_identifiers,
    write_addresses,
    write_contact_methods,
    write_entity_events,
    write_links,
    write_names,
    write_pronouns,
    write_role_assignments,
)

router = APIRouter(prefix="/people", tags=["public-api"])


def _get_registry(request: Request) -> EmbeddingRegistry:
    """Return the startup-loaded embedding model registry from app state."""
    return request.app.state.embedding_registry


@router.post(
    "/observations",
    response_model=ObservationResponse,
    operation_id="submitPeopleObservation",
)
async def submit_people_observation(
    request: PeopleObservationRequest,
    auth: AuthedKey = Depends(require_scope("observations:write")),
    db=Depends(get_db),
) -> ObservationResponse:
    """Submit a person identity observation; attach to existing person or create a new one."""
    try:
        # Resolution + all writes share one transaction so any rejection rolls the
        # whole observation back — nothing half-written (#456 CR2, mirroring the
        # assignments route). resolve_entity *creates* on an unseen identifier, so
        # running it outside meant a payload rejection left a bare, nameless Person
        # committed while the response withheld entity_id: unreachable by the caller
        # and indistinguishable from a real record. Both guards raise rather than
        # return, so the rollback covers the entity_type mismatch too — posting an
        # org identifier here used to mint an Organization and then say "rejected".
        async with stamped_transaction(db, auth.key_id):
            entity_id, entity_type, disposition, reason = await resolve_entity(
                db, request.identifier_type, request.identifier_value
            )
            if disposition is Disposition.REJECTED:
                raise ObservationRejected(reason or "rejected")
            if entity_type != "person":
                raise ObservationRejected(f"entity_type_mismatch: {entity_type!r}")

            await write_names(db, entity_id, entity_type, auth.key_id, request.names)
            await write_links(db, entity_id, entity_type, request.links)
            await write_contact_methods(db, entity_id, entity_type, request.contact_methods)
            await write_addresses(db, entity_id, entity_type, request.addresses)
            await write_role_assignments(db, entity_id, auth.key_id, request.role_assignments)
            if request.personal_pronouns:
                await write_pronouns(db, entity_id, request.personal_pronouns)
            await write_additional_identifiers(db, entity_id, request.additional_identifiers)
            event_results = await write_entity_events(
                db, entity_id, entity_type, auth.key_id, request.events
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
                disposition=r.disposition.value,
                event_id=r.event_id,
                reason=r.reason,
                attached_archived=r.attached_archived or None,
            )
            for r in event_results
        ]
        or None,
        citations=[
            CitationObservationResult(
                disposition=r.disposition.value,
                citation_id=r.citation_id,
                reason=r.reason,
                attached_archived=r.attached_archived or None,
            )
            for r in citation_results
        ]
        or None,
    )


@router.get(
    "/search",
    response_model=PersonSearchResponse,
    operation_id="searchPeople",
)
async def search_people(
    q: str = Query(default=""),
    limit: int = Query(default=10, ge=1),
    offset: int = Query(default=0, ge=0),
    include_archived: bool = Query(default=False),
    id_filter: tuple[str | None, str | None] = Depends(identifier_filter),
    _: str = Depends(require_api_key),
    db=Depends(get_db),
) -> Any:
    """Search people by display name or public name variant.

    When identifier_type and identifier_value are both supplied they take precedence
    over q and return at most one result with has_more always false.
    """
    limit = min(limit, 50)
    id_type, id_value = id_filter

    if id_type is not None:
        rows = await db.fetch(
            """
            SELECT p.id, v.display_name, p.archived_at
            FROM people p
            JOIN identifiers i ON i.entity_id = p.id
            JOIN entity_identifier_types t ON t.id = i.entity_identifier_type_id
            LEFT JOIN v_person_display_names v ON v.person_id = p.id
            WHERE t.slug = $1
              AND i.value = $2
              AND t.entity_type = 'person'
              AND ($3 OR p.archived_at IS NULL)
            LIMIT 1
            """,
            id_type,
            id_value,
            include_archived,
        )
        return {
            "data": [
                {"id": r["id"], "display_name": r["display_name"], "archived_at": r["archived_at"]}
                for r in rows
            ],
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

    rows = await db.fetch(
        """
        SELECT
            p.id,
            v.display_name,
            p.archived_at
        FROM (SELECT pm_prefix_tsquery('pm_unaccent_simple', $1) AS tsq) _q,
             people p
        LEFT JOIN v_person_display_names v ON v.person_id = p.id
        WHERE ($3 OR p.archived_at IS NULL)
          AND p.search_tsv @@ _q.tsq
        ORDER BY
            ts_rank(p.search_tsv, _q.tsq) DESC,
            v.display_name NULLS LAST,
            p.id  -- unique tiebreaker: stable offset pagination under rank/name ties (#297)
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
        "data": [
            {"id": r["id"], "display_name": r["display_name"], "archived_at": r["archived_at"]}
            for r in page
        ],
        "meta": {
            "limit": limit,
            "offset": offset,
            "count": len(page),
            "has_more": has_more,
        },
    }


@router.get(
    "/{person_id}",
    response_model=PersonDetail,
    operation_id="getPerson",
    responses=NOT_MODIFIED,
)
async def get_person(
    person_id: str,
    request: Request,
    response: Response,
    _: str = Depends(require_api_key),
    db=Depends(get_db),
    registry: EmbeddingRegistry = Depends(_get_registry),
) -> Any:
    """Return full person record with public name variants and identifiers."""
    row = await db.fetchrow(
        """
        SELECT p.id, p.archived_at, p.created_at, p.updated_at, v.display_name
        FROM people p
        LEFT JOIN v_person_display_names v ON v.person_id = p.id
        WHERE p.id = $1
        """,
        person_id,
    )
    if not row:
        raise HTTPException(status_code=404, detail="Person not found")

    etag = make_etag(row["id"], row["updated_at"])
    cached = conditional_response(request, response, etag, row["updated_at"])
    if cached is not None:
        return cached

    names, identifiers, voice_count = await _fetch_detail_arrays(person_id, db, registry)

    return {
        "id": row["id"],
        "display_name": row["display_name"],
        "archived_at": row["archived_at"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "names": [dict(n) for n in names],
        "identifiers": [dict(i) for i in identifiers],
        "voice_embeddings_count": voice_count,
    }


@router.get(
    "/{person_id}/events",
    response_model=EntityEventsResponse,
    operation_id="listPersonEvents",
    responses=NOT_MODIFIED,
)
async def list_person_events(
    person_id: str,
    request: Request,
    response: Response,
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    _: str = Depends(require_api_key),
    db=Depends(get_db),
) -> Any:
    """Return public, active lifecycle events for a person."""
    exists = await db.fetchval("SELECT 1 FROM people WHERE id = $1", person_id)
    if not exists:
        raise HTTPException(status_code=404, detail="Person not found")

    etag, last = await events_collection_validator(db, person_id, "person", limit, offset)
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
          AND ee.entity_type = 'person'
          AND ee.visibility = 'public'
          AND ee.archived_at IS NULL
        -- ee.id: unique tiebreaker for stable offset pagination under date/created_at ties (#297)
        ORDER BY ee.event_year DESC NULLS LAST, ee.event_month DESC NULLS LAST,
                 ee.event_day DESC NULLS LAST, ee.created_at DESC, ee.id DESC
        LIMIT $2 OFFSET $3
        """,
        person_id,
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


async def _fetch_detail_arrays(
    person_id: str, db: Any, registry: EmbeddingRegistry
) -> tuple[list[Any], list[Any], int]:
    """Fetch public name variants, identifiers, and total active embedding count for a person."""
    names = await db.fetch(
        f"""
        SELECT id, name, name_type, locale, is_canonical
        FROM person_names
        WHERE person_id = $1
          AND {visible_names_filter()}
        ORDER BY is_canonical DESC, name_type, name
        """,
        person_id,
    )
    identifiers = await db.fetch(
        """
        SELECT i.id, i.entity_identifier_type_id AS type_id, t.slug AS type_slug, i.value
        FROM identifiers i
        JOIN entity_identifier_types t ON t.id = i.entity_identifier_type_id
        WHERE i.entity_id = $1 AND t.entity_type = 'person' AND NOT t.is_internal
        ORDER BY t.slug, i.value
        """,
        person_id,
    )
    queryable = [m for m in registry.all() if m.is_queryable]
    if not queryable:
        voice_count = 0
    else:
        # table_name values are registry-controlled — not user input
        union_sql = " UNION ALL ".join(
            f"SELECT COUNT(*) AS n FROM {m.table_name} WHERE person_id = $1 AND archived_at IS NULL"
            for m in queryable
        )
        rows = await db.fetch(union_sql, person_id)
        voice_count = sum(r["n"] for r in rows)
    return names, identifiers, voice_count
