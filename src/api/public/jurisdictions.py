"""Public API v1 — jurisdiction endpoints."""

from typing import Any, Literal

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response

from src.api.deps import get_db
from src.api.public.deps import AuthedKey, require_api_key, require_scope
from src.api.public.etag import (
    NOT_MODIFIED,
    catalog_validator,
    collection_etag,
    conditional_response,
    make_etag,
)
from src.api.public.schemas import (
    JurisdictionLineageResponse,
    JurisdictionListResponse,
    JurisdictionObservationRequest,
    JurisdictionRelationshipsResponse,
    JurisdictionResponse,
    ObservationResponse,
)
from src.core.jurisdictions import fetch_lineage
from src.core.observation import (
    Disposition,
    IdentifierConflict,
    ObservationRejected,
    resolve_entity,
    write_additional_identifiers,
    write_addresses,
    write_contact_methods,
    write_links,
)

router = APIRouter(prefix="/jurisdictions", tags=["public-api"])

_DETAIL_SQL = """
    SELECT
        j.id, j.slug, j.name,
        jt.id   AS type_id,
        jt.slug AS type_slug,
        jt.display_name AS type_display_name,
        j.valid_from, j.valid_until,
        j.recorded_at, j.superseded_at,
        j.created_at, j.updated_at, j.archived_at
    FROM jurisdictions j
    JOIN jurisdiction_types jt ON jt.id = j.type_id
"""


def _row_to_jur(r: Any) -> dict[str, Any]:
    return {
        "id": r["id"],
        "slug": r["slug"],
        "name": r["name"],
        "type": {
            "id": r["type_id"],
            "slug": r["type_slug"],
            "display_name": r["type_display_name"],
        },
        "valid_from": r["valid_from"],
        "valid_until": r["valid_until"],
        "recorded_at": r["recorded_at"],
        "superseded_at": r["superseded_at"],
        "created_at": r["created_at"],
        "updated_at": r["updated_at"],
        "archived_at": r["archived_at"],
    }


def _row_to_rel(r: Any) -> dict[str, Any]:
    return {
        "id": r["id"],
        "from_id": r["from_id"],
        "to_id": r["to_id"],
        "rel_type": {
            "id": r["rel_type_id"],
            "slug": r["rel_type_slug"],
            "display_name": r["rel_type_display_name"],
            "category": r["rel_type_category"],
            "is_symmetric": r["rel_type_is_symmetric"],
        },
        "valid_from": r["valid_from"],
        "valid_until": r["valid_until"],
        "recorded_at": r["recorded_at"],
        "superseded_at": r["superseded_at"],
        "created_at": r["created_at"],
        "updated_at": r["updated_at"],
    }


async def _fetch_identifiers(jurisdiction_id: str, db: Any) -> list[dict]:
    rows = await db.fetch(
        """
        SELECT i.id, i.entity_identifier_type_id AS type_id, t.slug AS type_slug, i.value
        FROM identifiers i
        JOIN entity_identifier_types t ON t.id = i.entity_identifier_type_id
        WHERE i.entity_id = $1 AND t.entity_type = 'jurisdiction' AND NOT t.is_internal
        ORDER BY t.slug, i.value
        """,
        jurisdiction_id,
    )
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# GET /jurisdictions/resolve  — must be declared before /{id}
# ---------------------------------------------------------------------------


@router.get(
    "/resolve",
    response_model=JurisdictionResponse,
    operation_id="resolveJurisdiction",
)
async def resolve_jurisdiction(
    slug: str | None = Query(default=None),
    scheme: str | None = Query(default=None),
    value: str | None = Query(default=None),
    _: str = Depends(require_api_key),
    db=Depends(get_db),
) -> Any:
    """Resolve a jurisdiction by slug or by external identifier (scheme + value).

    Exactly one lookup strategy must be supplied: either ``slug`` or both
    ``scheme`` and ``value``.
    """
    has_slug = slug is not None
    has_identifier = scheme is not None and value is not None
    has_partial = (scheme is None) != (value is None)

    if has_partial:
        raise HTTPException(status_code=422, detail="Provide both 'scheme' and 'value' together")
    if has_slug and has_identifier:
        raise HTTPException(status_code=422, detail="Provide 'slug' or 'scheme'+'value', not both")
    if not has_slug and not has_identifier:
        raise HTTPException(status_code=422, detail="Provide 'slug' or 'scheme'+'value'")

    if has_slug:
        row = await db.fetchrow(
            _DETAIL_SQL + "WHERE j.slug = $1",
            slug,
        )
    else:
        row = await db.fetchrow(
            _DETAIL_SQL
            + """
            JOIN identifiers i ON i.entity_id = j.id
            JOIN entity_identifier_types t ON t.id = i.entity_identifier_type_id
            WHERE t.slug = $1 AND i.value = $2 AND t.entity_type = 'jurisdiction'
            """,
            scheme,
            value,
        )

    if not row:
        raise HTTPException(status_code=404, detail="Jurisdiction not found")

    identifiers = await _fetch_identifiers(row["id"], db)
    return {**_row_to_jur(row), "identifiers": identifiers}


# ---------------------------------------------------------------------------
# GET /jurisdictions
# ---------------------------------------------------------------------------


@router.get(
    "",
    response_model=JurisdictionListResponse,
    operation_id="listJurisdictions",
)
async def list_jurisdictions(
    type: str | None = Query(default=None, description="Filter by type slug"),
    include_archived: bool = Query(default=False),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    _: str = Depends(require_api_key),
    db=Depends(get_db),
) -> Any:
    """Return a paginated list of jurisdictions."""
    rows = await db.fetch(
        _DETAIL_SQL
        + """
        WHERE ($1 OR j.archived_at IS NULL)
          AND ($2::text IS NULL OR jt.slug = $2)
        ORDER BY j.name, j.id
        LIMIT $3 OFFSET $4
        """,
        include_archived,
        type,
        limit + 1,
        offset,
    )
    has_more = len(rows) > limit
    page = rows[:limit]
    return {
        "data": [_row_to_jur(r) for r in page],
        "meta": {"limit": limit, "offset": offset, "count": len(page), "has_more": has_more},
    }


# ---------------------------------------------------------------------------
# GET /jurisdictions/{id}
# ---------------------------------------------------------------------------


@router.get(
    "/{jurisdiction_id}",
    response_model=JurisdictionResponse,
    operation_id="getJurisdiction",
    responses=NOT_MODIFIED,
)
async def get_jurisdiction(
    jurisdiction_id: str,
    request: Request,
    response: Response,
    _: str = Depends(require_api_key),
    db=Depends(get_db),
) -> Any:
    """Return a single jurisdiction by ULID or slug."""
    row = await db.fetchrow(
        _DETAIL_SQL + "WHERE j.id = $1 OR j.slug = $1",
        jurisdiction_id,
    )
    if not row:
        raise HTTPException(status_code=404, detail="Jurisdiction not found")

    etag = make_etag(row["id"], row["updated_at"])
    cached = conditional_response(request, response, etag, row["updated_at"])
    if cached is not None:
        return cached

    identifiers = await _fetch_identifiers(row["id"], db)
    return {**_row_to_jur(row), "identifiers": identifiers}


# ---------------------------------------------------------------------------
# GET /jurisdictions/{id}/relationships
# ---------------------------------------------------------------------------


_REL_VERSION_SQL = """
    SELECT count(*) AS n, max(jr.updated_at) AS last
    FROM jurisdiction_relationships jr
    JOIN jurisdiction_relationship_types jrt ON jrt.id = jr.rel_type_id
    WHERE (
        ($2 = 'from' AND jr.from_id = $1) OR
        ($2 = 'to'   AND jr.to_id   = $1) OR
        ($2 = 'both' AND (jr.from_id = $1 OR jr.to_id = $1))
    )
      AND ($3::text IS NULL OR jrt.category = $3)
      AND ($4::text IS NULL OR jrt.slug = $4)
"""


@router.get(
    "/{jurisdiction_id}/relationships",
    response_model=JurisdictionRelationshipsResponse,
    operation_id="listJurisdictionRelationships",
    responses=NOT_MODIFIED,
)
async def list_jurisdiction_relationships(
    jurisdiction_id: str,
    request: Request,
    response: Response,
    direction: Literal["from", "to", "both"] = Query(default="both"),
    category: str | None = Query(default=None, description="Filter by relationship category"),
    rel_type: str | None = Query(default=None, description="Filter by relationship type slug"),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    _: str = Depends(require_api_key),
    db=Depends(get_db),
) -> Any:
    """Return relationships (edges) involving the given jurisdiction.

    Lookup accepts ULID or slug for ``jurisdiction_id``.

    Conditional GET (#392): watermark over the same filtered set the body uses.
    Keyed on the resolved id, so the ULID and slug spellings of one jurisdiction
    share a tag rather than splitting the cache.
    """
    jur = await db.fetchrow(
        "SELECT id FROM jurisdictions WHERE id = $1 OR slug = $1", jurisdiction_id
    )
    if not jur:
        raise HTTPException(status_code=404, detail="Jurisdiction not found")
    jid = jur["id"]

    version = await db.fetchrow(_REL_VERSION_SQL, jid, direction, category, rel_type)
    etag = collection_etag(
        f"{jid}-jur-relationships",
        version["n"],
        version["last"],
        direction,
        category,
        rel_type,
        limit,
        offset,
    )
    cached = conditional_response(request, response, etag, version["last"])
    if cached is not None:
        return cached

    rows = await db.fetch(
        """
        SELECT
            jr.id, jr.from_id, jr.to_id,
            jrt.id           AS rel_type_id,
            jrt.slug         AS rel_type_slug,
            jrt.display_name AS rel_type_display_name,
            jrt.category     AS rel_type_category,
            jrt.is_symmetric AS rel_type_is_symmetric,
            jr.valid_from, jr.valid_until,
            jr.recorded_at, jr.superseded_at, jr.created_at, jr.updated_at
        FROM jurisdiction_relationships jr
        JOIN jurisdiction_relationship_types jrt ON jrt.id = jr.rel_type_id
        WHERE (
            ($2 = 'from' AND jr.from_id = $1) OR
            ($2 = 'to'   AND jr.to_id   = $1) OR
            ($2 = 'both' AND (jr.from_id = $1 OR jr.to_id = $1))
        )
          AND ($3::text IS NULL OR jrt.category = $3)
          AND ($4::text IS NULL OR jrt.slug = $4)
        ORDER BY jr.created_at, jr.id
        LIMIT $5 OFFSET $6
        """,
        jid,
        direction,
        category,
        rel_type,
        limit + 1,
        offset,
    )
    has_more = len(rows) > limit
    page = rows[:limit]
    return {
        "data": [_row_to_rel(r) for r in page],
        "meta": {"limit": limit, "offset": offset, "count": len(page), "has_more": has_more},
    }


# ---------------------------------------------------------------------------
# GET /jurisdictions/{id}/lineage
# ---------------------------------------------------------------------------


@router.get(
    "/{jurisdiction_id}/lineage",
    response_model=JurisdictionLineageResponse,
    operation_id="getJurisdictionLineage",
    responses=NOT_MODIFIED,
)
async def get_jurisdiction_lineage(
    jurisdiction_id: str,
    request: Request,
    response: Response,
    depth: int = Query(default=10, ge=1, le=50),
    _: str = Depends(require_api_key),
    db=Depends(get_db),
) -> Any:
    """Return the lineage chain for a jurisdiction.

    Traverses edges with ``category = 'lineage'`` (supersedes, evolved_from,
    merged_into) in both directions up to ``depth`` hops. Cycle-safe via a
    visited array; depth capped at 50.

    Lookup accepts ULID or slug for ``jurisdiction_id``.
    """
    # Resolve slug → id first so the recursive CTE can use a stable id anchor.
    jur = await db.fetchrow(
        "SELECT id FROM jurisdictions WHERE id = $1 OR slug = $1", jurisdiction_id
    )
    if not jur:
        raise HTTPException(status_code=404, detail="Jurisdiction not found")
    jid = jur["id"]

    rows = await fetch_lineage(db, jid, depth)
    # Content hash, not a watermark (#392): the result is a recursive traversal
    # over jurisdictions *and* lineage edges, so no single table's max(updated_at)
    # covers it — and any validator that did would still have to run the
    # traversal. Hashing what the traversal returned is exact and covers a rename
    # of any member reached along the chain. Saves serialization + transfer only.
    # Hashes the raw CTE rows, which is deliberately *broader* than the serialized
    # body (traversal bookkeeping columns included): it can only over-invalidate,
    # never miss a change.
    cached = conditional_response(request, response, catalog_validator(rows))
    if cached is not None:
        return cached
    return {"data": [_row_to_jur(r) for r in rows]}


# ---------------------------------------------------------------------------
# POST /jurisdictions/observations
# ---------------------------------------------------------------------------


@router.post(
    "/observations",
    response_model=ObservationResponse,
    operation_id="submitJurisdictionObservation",
)
async def submit_jurisdiction_observation(
    request: JurisdictionObservationRequest,
    auth: AuthedKey = Depends(require_scope("observations:write")),
    db=Depends(get_db),
) -> ObservationResponse:
    """Submit a jurisdiction identity observation; attach to existing or create new."""
    create_data: dict | None = None
    if (
        request.jurisdiction_slug is not None
        and request.jurisdiction_name is not None
        and request.jurisdiction_type_slug is not None
    ):
        create_data = {
            "slug": request.jurisdiction_slug,
            "name": request.jurisdiction_name,
            "type_slug": request.jurisdiction_type_slug,
            "valid_from": request.jurisdiction_valid_from,
            "valid_until": request.jurisdiction_valid_until,
            "notes": request.jurisdiction_notes,
        }

    entity_id, entity_type, disposition, reason = await resolve_entity(
        db,
        request.identifier_type,
        request.identifier_value,
        create_data=create_data,
    )

    if disposition is Disposition.REJECTED:
        return ObservationResponse(disposition="rejected", reason=reason)
    if entity_type != "jurisdiction":
        return ObservationResponse(
            disposition="rejected",
            reason=f"entity_type_mismatch: {entity_type!r}",
        )

    try:
        async with db.transaction():
            await write_links(db, entity_id, entity_type, request.links)
            await write_contact_methods(db, entity_id, entity_type, request.contact_methods)
            await write_addresses(db, entity_id, entity_type, request.addresses)
            await write_additional_identifiers(db, entity_id, request.additional_identifiers)
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
    )
