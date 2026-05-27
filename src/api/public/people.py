"""Public API v1 — people endpoints."""

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query

from src.api.deps import get_db
from src.api.public.deps import identifier_filter, require_api_key
from src.api.public.schemas import PersonDetail, PersonSearchResponse
from src.core.db import visible_names_filter

router = APIRouter(prefix="/people", tags=["public-api"])


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
        f"""
        SELECT
            p.id,
            v.display_name,
            p.archived_at
        FROM people p
        LEFT JOIN v_person_display_names v ON v.person_id = p.id
        WHERE ($4 OR p.archived_at IS NULL)
          AND (
              v.display_name ILIKE $1
              OR EXISTS (
                  SELECT 1 FROM person_names pn
                  WHERE pn.person_id = p.id
                    AND pn.name ILIKE $1
                    AND {visible_names_filter("pn")}
              )
          )
        ORDER BY
            CASE WHEN v.display_name ILIKE $1 THEN 0 ELSE 1 END,
            v.display_name NULLS LAST
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
)
async def get_person(
    person_id: str,
    _: str = Depends(require_api_key),
    db=Depends(get_db),
) -> Any:
    """Return full person record with public name variants and identifiers."""
    row = await db.fetchrow(
        """
        SELECT p.id, p.archived_at, v.display_name
        FROM people p
        LEFT JOIN v_person_display_names v ON v.person_id = p.id
        WHERE p.id = $1
        """,
        person_id,
    )
    if not row:
        raise HTTPException(status_code=404, detail="Person not found")

    names, identifiers = await _fetch_detail_arrays(person_id, db)

    return {
        "id": row["id"],
        "display_name": row["display_name"],
        "archived_at": row["archived_at"],
        "names": [dict(n) for n in names],
        "identifiers": [dict(i) for i in identifiers],
    }


async def _fetch_detail_arrays(person_id: str, db: Any) -> tuple[list[Any], list[Any]]:
    """Fetch public name variants and identifiers for a person."""
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
        WHERE i.entity_id = $1 AND t.entity_type = 'person'
        ORDER BY t.slug, i.value
        """,
        person_id,
    )
    return names, identifiers
