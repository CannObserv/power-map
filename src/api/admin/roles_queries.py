"""Shared query helpers for the roles admin module.

Extracted so both `roles.py` (the list route) and `orgs_roles.py` (the
list-flow merge response branch, #251) share one source of truth for the
roles list query — preventing the query from drifting between the two sites,
the same failure mode `orgs_queries.py` / `people_queries.py` address.
"""

from src.api.admin.list_status import count_with_hidden_matches
from src.api.admin.pagination import pagination_context

# Status → SQL predicate, in dropdown order; ``all`` (no predicate) is a
# first-class validated status (#306).
STATUS_PREDICATES: dict[str, str] = {
    "active": "r.archived_at IS NULL",
    "archived": "r.archived_at IS NOT NULL",
}
VALID_STATUSES: set[str] = set(STATUS_PREDICATES) | {"all"}


async def query_roles_rows(
    db, *, q: str, org_q: str, status: str, page: int, page_size: int
) -> tuple[list, int, dict, list[dict]]:
    """Run the roles list query under the given filter state.

    Returns ``(rows, count, pctx, hidden_matches)`` where ``pctx`` is the
    `pagination_context()` dict (with ``page`` clamped to the valid range)
    and ``hidden_matches`` lists ``{"status", "count"}`` for search matches
    the current status filter excludes (#306). Empty unless a search is
    active — either free-text filter (``q`` on the role, ``org_q`` on the
    joined organization) counts as one — and ``status != "all"``.

    An unknown ``status`` falls back to ``active`` — never to no-filter.
    Unlike Orgs / People, the roles list is cross-org with the second
    ``org_q`` filter matched against the joined organization's ``search_tsv``.
    """
    if status not in VALID_STATUSES:
        status = "active"
    search_conditions: list[str] = []
    params: list = []
    if q:
        params.append(q)
        search_conditions.append(f"r.search_tsv @@ plainto_tsquery('pm_simple', ${len(params)})")
    if org_q:
        params.append(org_q)
        search_conditions.append(f"o.search_tsv @@ plainto_tsquery('pm_simple', ${len(params)})")
    conditions = ([STATUS_PREDICATES[status]] if status != "all" else []) + search_conditions
    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""

    if q or org_q:
        count, hidden_matches = await count_with_hidden_matches(
            db,
            from_clause="roles r JOIN organizations o ON o.id = r.organization_id",
            search_conditions=search_conditions,
            params=params,
            predicates=STATUS_PREDICATES,
            status=status,
        )
    else:
        hidden_matches = []
        count = await db.fetchval(
            f"""SELECT count(r.id)
                FROM roles r
                JOIN organizations o ON o.id = r.organization_id
                {where}""",
            *params,
        )

    pctx = pagination_context(page, count, page_size)
    offset = (pctx["page"] - 1) * page_size
    list_params = params + [page_size, offset]
    rows = await db.fetch(
        f"""SELECT r.id, r.title, r.notes, r.archived_at, r.created_at,
                   o.id AS org_id,
                   dn.display_name AS org_name,
                   r.role_type_id,
                   rt.display_name AS role_type_name
            FROM roles r
            JOIN organizations o ON o.id = r.organization_id
            LEFT JOIN v_org_display_names dn ON dn.organization_id = o.id
            LEFT JOIN role_types rt ON rt.id = r.role_type_id
            {where}
            ORDER BY dn.display_name NULLS LAST, r.title, r.id
            LIMIT ${len(list_params) - 1} OFFSET ${len(list_params)}""",
        *list_params,
    )
    return rows, count, pctx, hidden_matches
