"""Shared query helpers for the people admin module.

Extracted so both `people.py` (the list route) and `people_merge.py` (the
list-flow merge response branch) can share one source of truth for the
people list query — preventing the query from drifting between the two
sites the way it nearly did when #137 first shipped.
"""

from src.api.admin.list_status import count_with_hidden_matches
from src.api.admin.pagination import pagination_context

# Status → SQL predicate, in dropdown order; ``all`` (no predicate) is a
# first-class validated status (#306). Two-valued axis — People has no
# ``inactive`` equivalent of the org flag.
STATUS_PREDICATES: dict[str, str] = {
    "active": "p.archived_at IS NULL",
    "archived": "p.archived_at IS NOT NULL",
}
VALID_STATUSES: set[str] = set(STATUS_PREDICATES) | {"all"}


async def query_people_rows(
    db, *, q: str, status: str, page: int, page_size: int
) -> tuple[list, int, dict, list[dict]]:
    """Run the people list query under the given filter state.

    Returns ``(rows, count, pctx, hidden_matches)`` where ``pctx`` is the
    `pagination_context()` dict (with ``page`` clamped to the valid range)
    and ``hidden_matches`` lists ``{"status", "count"}`` for search matches
    the current status filter excludes (#306). Empty when there is no search
    text or ``status == "all"``.

    An unknown ``status`` falls back to ``active`` — never to no-filter.
    """
    if status not in VALID_STATUSES:
        status = "active"
    search_conditions: list[str] = []
    params: list = []
    if q:
        params.append(q)
        search_conditions.append(
            f"p.search_tsv @@ plainto_tsquery('pm_unaccent_simple', ${len(params)})"
        )
    conditions = ([STATUS_PREDICATES[status]] if status != "all" else []) + search_conditions
    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""

    if q:
        count, hidden_matches = await count_with_hidden_matches(
            db,
            from_clause="people p",
            search_conditions=search_conditions,
            params=params,
            predicates=STATUS_PREDICATES,
            status=status,
        )
    else:
        hidden_matches = []
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
            ORDER BY n.sort_key COLLATE "und-x-icu" NULLS LAST, p.id
            LIMIT ${len(list_params) - 1} OFFSET ${len(list_params)}""",
        *list_params,
    )
    return rows, count, pctx, hidden_matches
