"""Shared query helpers for the role-assignments admin module.

Extracted from the inline query in ``role_assignments.py`` (#306 CR round 1)
so the assignments list follows the same declarative status-axis shape as
``orgs_queries.py`` / ``people_queries.py`` / ``roles_queries.py`` /
``jurisdictions_queries.py`` — the inline build is how this surface was
missed by the original #306 parity pass.
"""

from src.api.admin._citations_shared import citation_count_lateral
from src.api.admin.list_status import count_with_hidden_matches
from src.api.admin.pagination import pagination_context

# Status → SQL predicate, in dropdown order; ``all`` (no predicate) is a
# first-class validated status (#306).
STATUS_PREDICATES: dict[str, str] = {
    "active": "ra.archived_at IS NULL",
    "archived": "ra.archived_at IS NOT NULL",
}
VALID_STATUSES: set[str] = set(STATUS_PREDICATES) | {"all"}

_LIST_SELECT = f"""
    SELECT ra.id, ra.is_current, ra.start_date, ra.end_date, ra.archived_at, ra.created_at,
           cc_j.citation_count,
           p.id AS person_id,
           pn.display_name AS person_name,
           r.id AS role_id, r.title AS role_title,
           o.id AS org_id,
           dn.display_name AS org_name
    FROM role_assignments ra
    JOIN people p ON p.id = ra.person_id
    LEFT JOIN v_person_display_names pn ON pn.person_id = p.id
    JOIN roles r ON r.id = ra.role_id
    JOIN organizations o ON o.id = r.organization_id
    LEFT JOIN v_org_display_names dn ON dn.organization_id = o.id
    {citation_count_lateral("role_assignment", "ra.id")}
"""

# ra.id: unique tiebreaker for stable offset pagination under the non-unique sort keys (#297)
_LIST_ORDER = (
    "ORDER BY ra.is_current DESC, person_name NULLS LAST, ra.start_date DESC NULLS LAST, ra.id"
)

# Count queries need the searchable joins (person / role / org tsv) but not
# the display-name views.
_COUNT_FROM = """role_assignments ra
            JOIN people p ON p.id = ra.person_id
            JOIN roles r ON r.id = ra.role_id
            JOIN organizations o ON o.id = r.organization_id"""


async def query_role_assignments_rows(
    db, *, q: str, status: str, page: int, page_size: int
) -> tuple[list, int, dict, list[dict]]:
    """Run the role-assignments list query under the given filter state.

    Returns ``(rows, count, pctx, hidden_matches)`` where ``pctx`` is the
    `pagination_context()` dict (with ``page`` clamped to the valid range)
    and ``hidden_matches`` lists ``{"status", "count"}`` for search matches
    the current status filter excludes (#306). Empty when there is no search
    text or ``status == "all"``. ``q`` matches the assignment's person, role,
    or organization ``search_tsv``.

    An unknown ``status`` falls back to ``active`` — never to no-filter.
    """
    if status not in VALID_STATUSES:
        status = "active"
    search_conditions: list[str] = []
    params: list = []
    if q:
        params.append(q)
        idx = len(params)
        search_conditions.append(
            f"(p.search_tsv @@ pm_prefix_tsquery('pm_unaccent_simple', ${idx})"
            f" OR r.search_tsv @@ pm_prefix_tsquery('pm_simple', ${idx})"
            f" OR o.search_tsv @@ pm_prefix_tsquery('pm_simple', ${idx}))"
        )
    conditions = ([STATUS_PREDICATES[status]] if status != "all" else []) + search_conditions
    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""

    if q:
        count, hidden_matches = await count_with_hidden_matches(
            db,
            from_clause=_COUNT_FROM,
            search_conditions=search_conditions,
            params=params,
            predicates=STATUS_PREDICATES,
            status=status,
        )
    else:
        hidden_matches = []
        count = await db.fetchval(
            f"""SELECT count(ra.id)
                FROM {_COUNT_FROM}
                {where}""",
            *params,
        )

    pctx = pagination_context(page, count, page_size)
    offset = (pctx["page"] - 1) * page_size
    list_params = params + [page_size, offset]
    rows = await db.fetch(
        f"""{_LIST_SELECT}
            {where}
            {_LIST_ORDER}
            LIMIT ${len(list_params) - 1} OFFSET ${len(list_params)}""",
        *list_params,
    )
    return rows, count, pctx, hidden_matches
