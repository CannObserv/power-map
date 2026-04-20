"""Public API v1 — organization endpoints."""

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query

from src.api.admin.deps import get_db
from src.api.public.deps import require_api_key

router = APIRouter(prefix="/orgs", tags=["public-api"])


def _org_row_to_dict(r: Any) -> dict[str, Any]:
    """Map a search-result row to the search response shape."""
    acronym = r["acronym"]
    return {
        "id": r["id"],
        "name": r["name"],
        "acronym": acronym,
        # slug derived from canonical acronym (lower); null when no acronym exists
        "slug": acronym.lower() if acronym else None,
        "parent_id": r["parent_id"],
        "archived_at": r["archived_at"].isoformat() if r["archived_at"] else None,
    }


@router.get("/search")
async def search_orgs(
    q: str = Query(default=""),
    limit: int = Query(default=10, ge=1),
    offset: int = Query(default=0, ge=0),
    include_archived: bool = Query(default=False),
    _: str = Depends(require_api_key),
    db=Depends(get_db),
) -> list[dict[str, Any]]:
    """Search organizations by name, acronym, or name variant."""
    if not q.strip():
        return []

    limit = min(limit, 50)
    archived_filter = "" if include_archived else "AND o.archived_at IS NULL"

    rows = await db.fetch(
        f"""
        SELECT
            o.id,
            n.name,
            a.acronym,
            o.parent_id,
            o.archived_at
        FROM organizations o
        LEFT JOIN organization_names n ON n.organization_id = o.id AND n.is_canonical = TRUE
        LEFT JOIN organization_acronyms a ON a.organization_id = o.id AND a.is_canonical = TRUE
        WHERE 1=1
          {archived_filter}
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
        limit,
        offset,
    )

    return [_org_row_to_dict(r) for r in rows]


@router.get("/{org_id}")
async def get_org(
    org_id: str,
    _: str = Depends(require_api_key),
    db=Depends(get_db),
) -> dict[str, Any]:
    """Return full org record with names, acronyms, and identifiers."""
    row = await db.fetchrow(
        """
        SELECT
            o.id,
            o.parent_id,
            o.archived_at,
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
        WHERE i.entity_id = $1
        ORDER BY t.slug, i.value
        """,
        org_id,
    )

    acronym = row["acronym"]
    return {
        "id": row["id"],
        "name": row["name"],
        "acronym": acronym,
        "slug": acronym.lower() if acronym else None,
        "parent_id": row["parent_id"],
        "archived_at": row["archived_at"].isoformat() if row["archived_at"] else None,
        "names": [
            {
                "id": n["id"],
                "name": n["name"],
                "name_type": n["name_type"],
                "is_canonical": n["is_canonical"],
            }
            for n in names
        ],
        "acronyms": [
            {
                "id": a["id"],
                "acronym": a["acronym"],
                "is_canonical": a["is_canonical"],
            }
            for a in acronyms
        ],
        "identifiers": [
            {
                "id": i["id"],
                "type_id": i["type_id"],
                "type_slug": i["type_slug"],
                "value": i["value"],
            }
            for i in identifiers
        ],
    }
