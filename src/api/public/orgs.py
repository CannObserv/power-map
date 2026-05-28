"""Public API v1 — organization endpoints."""

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response

from src.api.deps import get_db
from src.api.public.deps import identifier_filter, require_api_key
from src.api.public.schemas import OrgDetail, OrgSearchResponse, make_etag

router = APIRouter(prefix="/orgs", tags=["public-api"])


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
