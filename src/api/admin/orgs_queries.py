"""Shared query helpers for the organizations admin module.

Extracted so both `orgs.py` (the list route) and `orgs_merge.py` (the
list-flow merge response branch) can share one source of truth for the
orgs list query — preventing the query from drifting between the two
sites, the same failure mode `people_queries.py` was created to prevent.
"""

from src.api.admin.pagination import pagination_context


async def query_orgs_rows(
    db, *, q: str, status: str, page: int, page_size: int
) -> tuple[list, int, dict]:
    """Run the orgs list query under the given filter state.

    Returns ``(rows, count, pctx)`` where ``pctx`` is the
    `pagination_context()` dict (with ``page`` clamped to the valid range).

    Mirrors the inline query that used to live in ``orgs_list`` — callers
    build their own template context from the returned tuple. Note the
    three-value status axis (``active`` / ``inactive`` / ``archived``):
    ``inactive`` is org-only (``organizations.active = FALSE``) and has no
    People equivalent.
    """
    conditions: list[str] = []
    params: list = []
    if status == "active":
        conditions.append("o.archived_at IS NULL AND o.active = TRUE")
    elif status == "inactive":
        conditions.append("o.archived_at IS NULL AND o.active = FALSE")
    elif status == "archived":
        conditions.append("o.archived_at IS NOT NULL")
    if q:
        params.append(q)
        conditions.append(f"o.search_tsv @@ plainto_tsquery('pm_simple', ${len(params)})")
    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""

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
    return rows, count, pctx
