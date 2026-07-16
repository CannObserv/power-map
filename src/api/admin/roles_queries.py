"""Shared query helpers for the roles admin module.

Extracted so both `roles.py` (the list route) and `orgs_roles.py` (the
list-flow merge response branch, #251) share one source of truth for the
roles list query — preventing the query from drifting between the two sites,
the same failure mode `orgs_queries.py` / `people_queries.py` address.
"""

from src.api.admin.pagination import pagination_context


async def query_roles_rows(
    db, *, q: str, org_q: str, status: str, page: int, page_size: int
) -> tuple[list, int, dict]:
    """Run the roles list query under the given filter state.

    Returns ``(rows, count, pctx)`` where ``pctx`` is the
    `pagination_context()` dict (with ``page`` clamped to the valid range).

    Mirrors the inline query that used to live in ``roles_list``. Unlike Orgs /
    People, the roles list is cross-org with a two-value status axis
    (``active`` / ``archived``) plus a second free-text filter ``org_q`` matched
    against the joined organization's ``search_tsv``.
    """
    conditions: list[str] = []
    params: list = []
    if status == "active":
        conditions.append("r.archived_at IS NULL")
    elif status == "archived":
        conditions.append("r.archived_at IS NOT NULL")
    if q:
        params.append(q)
        conditions.append(f"r.search_tsv @@ plainto_tsquery('pm_simple', ${len(params)})")
    if org_q:
        params.append(org_q)
        conditions.append(f"o.search_tsv @@ plainto_tsquery('pm_simple', ${len(params)})")
    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""

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
    return rows, count, pctx
