"""Shared query helpers for the jurisdictions admin module.

Extracted so the list route (and any future list-flow reuse) shares one source
of truth for the jurisdictions list query — the same rationale behind
``orgs_queries.py`` / ``people_queries.py`` / ``roles_queries.py``.

Jurisdictions have no ``search_tsv`` column, so search is ILIKE over name/slug
(matching the existing typeahead). The status axis partitions cleanly on
(archived_at, superseded_at): a superseded row keeps ``archived_at IS NULL``
(supersession is not soft-delete), so ``active`` excludes both archived and
superseded, ``superseded`` is the still-live-but-superseded lens, and
``archived`` is the terminal soft-deleted set.
"""

from src.api.admin.deps import escape_like
from src.api.admin.list_status import count_with_hidden_matches
from src.api.admin.pagination import pagination_context

# Status → SQL predicate, in dropdown order; ``all`` (no predicate) is a
# first-class validated status (#306).
STATUS_PREDICATES: dict[str, str] = {
    "active": "j.archived_at IS NULL AND j.superseded_at IS NULL",
    "superseded": "j.archived_at IS NULL AND j.superseded_at IS NOT NULL",
    "archived": "j.archived_at IS NOT NULL",
}
VALID_STATUSES: set[str] = set(STATUS_PREDICATES) | {"all"}


async def query_jurisdictions_rows(
    db, *, q: str, status: str, type_slug: str | None, page: int, page_size: int
) -> tuple[list, int, dict, list[dict]]:
    """Run the jurisdictions list query under the given filter state.

    Returns ``(rows, count, pctx, hidden_matches)`` where ``pctx`` is the
    ``pagination_context()`` dict (with ``page`` clamped to the valid range)
    and ``hidden_matches`` lists ``{"status", "count"}`` for search matches
    the current status filter excludes (#306) — the counts respect the
    ``type_slug`` filter. Empty when there is no search text or
    ``status == "all"``. ``q`` is matched case-insensitively against name and
    slug; an unknown ``status`` falls back to ``active`` — never to no-filter.
    """
    if status not in VALID_STATUSES:
        status = "active"
    search_conditions: list[str] = []
    params: list = []
    if q:
        params.append(f"%{escape_like(q)}%")
        search_conditions.append(
            f"(j.name ILIKE ${len(params)} ESCAPE '\\' OR j.slug ILIKE ${len(params)} ESCAPE '\\')"
        )
    if type_slug:
        params.append(type_slug)
        search_conditions.append(f"jt.slug = ${len(params)}")
    conditions = ([STATUS_PREDICATES[status]] if status != "all" else []) + search_conditions
    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""

    if q:
        count, hidden_matches = await count_with_hidden_matches(
            db,
            from_clause="jurisdictions j JOIN jurisdiction_types jt ON jt.id = j.type_id",
            search_conditions=search_conditions,
            params=params,
            predicates=STATUS_PREDICATES,
            status=status,
        )
    else:
        hidden_matches = []
        count = await db.fetchval(
            f"""SELECT count(j.id)
                FROM jurisdictions j
                JOIN jurisdiction_types jt ON jt.id = j.type_id
                {where}""",
            *params,
        )

    pctx = pagination_context(page, count, page_size)
    offset = (pctx["page"] - 1) * page_size
    list_params = params + [page_size, offset]
    rows = await db.fetch(
        f"""SELECT j.id, j.slug, j.name, j.valid_from, j.valid_until,
                   j.superseded_at, j.archived_at, j.created_at,
                   jt.slug AS type_slug, jt.display_name AS type_display_name
            FROM jurisdictions j
            JOIN jurisdiction_types jt ON jt.id = j.type_id
            {where}
            ORDER BY j.name, j.id
            LIMIT ${len(list_params) - 1} OFFSET ${len(list_params)}""",
        *list_params,
    )
    return rows, count, pctx, hidden_matches
