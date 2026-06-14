"""Shared query helpers for the people admin module.

Extracted so both `people.py` (the list route) and `people_merge.py` (the
list-flow merge response branch) can share one source of truth for the
people list query — preventing the query from drifting between the two
sites the way it nearly did when #137 first shipped.
"""

from src.api.admin.pagination import pagination_context


async def query_people_rows(
    db, *, q: str, status: str, page: int, page_size: int
) -> tuple[list, int, dict]:
    """Run the people list query under the given filter state.

    Returns ``(rows, count, pctx)`` where ``pctx`` is the
    `pagination_context()` dict (with ``page`` clamped to the valid range).

    Mirrors the inline query that used to live in ``people_list`` —
    callers build their own template context from the returned tuple.
    """
    conditions: list[str] = []
    params: list = []
    if status == "active":
        conditions.append("p.archived_at IS NULL")
    elif status == "archived":
        conditions.append("p.archived_at IS NOT NULL")
    if q:
        params.append(q)
        conditions.append(f"p.search_tsv @@ plainto_tsquery('pm_unaccent_simple', ${len(params)})")
    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""

    count = await db.fetchval(
        f"""SELECT count(p.id)
            FROM people p
            {where}""",
        *params,
    )

    pctx = pagination_context(page, count, page_size)
    offset = (pctx["page"] - 1) * page_size
    list_params = params + [page_size, offset]
    rows = await db.fetch(
        f"""SELECT p.id, p.archived_at, p.created_at,
                   n.display_name AS canonical_name
            FROM people p
            LEFT JOIN v_person_display_names n ON n.person_id = p.id
            {where}
            ORDER BY n.sort_key COLLATE "und-x-icu" NULLS LAST
            LIMIT ${len(list_params) - 1} OFFSET ${len(list_params)}""",
        *list_params,
    )
    return rows, count, pctx
