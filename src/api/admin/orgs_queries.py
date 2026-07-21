"""Shared query helpers for the organizations admin module.

Extracted so both `orgs.py` (the list route) and `orgs_merge.py` (the
list-flow merge response branch) can share one source of truth for the
orgs list query — preventing the query from drifting between the two
sites, the same failure mode `people_queries.py` was created to prevent.
"""

from src.api.admin.list_status import count_with_hidden_matches
from src.api.admin.pagination import pagination_context

# Status → SQL predicate, in dropdown order. ``all`` (no predicate) is a
# first-class validated status (#306) so one search can span every status.
# ``inactive`` is org-only (``organizations.active = FALSE``) — no People
# equivalent.
STATUS_PREDICATES: dict[str, str] = {
    "active": "o.archived_at IS NULL AND o.active = TRUE",
    "inactive": "o.archived_at IS NULL AND o.active = FALSE",
    "archived": "o.archived_at IS NOT NULL",
}
VALID_STATUSES: set[str] = set(STATUS_PREDICATES) | {"all"}


async def query_orgs_rows(
    db, *, q: str, status: str, page: int, page_size: int
) -> tuple[list, int, dict, list[dict]]:
    """Run the orgs list query under the given filter state.

    Returns ``(rows, count, pctx, hidden_matches)`` where ``pctx`` is the
    `pagination_context()` dict (with ``page`` clamped to the valid range)
    and ``hidden_matches`` lists ``{"status", "count"}`` for search matches
    the current status filter excludes (#306 — a search must never silently
    hide same-named rows under another status). Empty when there is no
    search text or ``status == "all"``.

    An unknown ``status`` falls back to ``active`` — never to no-filter.
    """
    if status not in VALID_STATUSES:
        status = "active"
    search_conditions: list[str] = []
    params: list = []
    if q:
        params.append(q)
        search_conditions.append(f"o.search_tsv @@ pm_prefix_tsquery('pm_simple', ${len(params)})")
    conditions = ([STATUS_PREDICATES[status]] if status != "all" else []) + search_conditions
    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""

    if q:
        count, hidden_matches = await count_with_hidden_matches(
            db,
            from_clause="organizations o",
            search_conditions=search_conditions,
            params=params,
            predicates=STATUS_PREDICATES,
            status=status,
        )
    else:
        hidden_matches = []
        count = await db.fetchval(
            f"""SELECT count(o.id)
                FROM organizations o
                {where}""",
            *params,
        )

    pctx = pagination_context(page, count, page_size)
    offset = (pctx["page"] - 1) * page_size
    list_params = params + [page_size, offset]
    rows = await db.fetch(
        f"""SELECT o.id, o.active, o.archived_at, o.created_at,
                   dn.display_name AS canonical_name
            FROM organizations o
            LEFT JOIN v_org_display_names dn ON dn.organization_id = o.id
            {where}
            ORDER BY dn.display_name NULLS LAST, o.id
            LIMIT ${len(list_params) - 1} OFFSET ${len(list_params)}""",
        *list_params,
    )
    return rows, count, pctx, hidden_matches
