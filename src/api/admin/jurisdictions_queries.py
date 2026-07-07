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
from src.api.admin.pagination import pagination_context

VALID_STATUSES: set[str] = {"active", "archived", "superseded"}


async def query_jurisdictions_rows(
    db, *, q: str, status: str, type_slug: str | None, page: int, page_size: int
) -> tuple[list, int, dict]:
    """Run the jurisdictions list query under the given filter state.

    Returns ``(rows, count, pctx)`` where ``pctx`` is the ``pagination_context()``
    dict (with ``page`` clamped to the valid range). ``q`` is matched
    case-insensitively against name and slug; ``type_slug`` filters by
    jurisdiction type; ``status`` is one of ``VALID_STATUSES``.
    """
    conditions: list[str] = []
    params: list = []
    if status == "active":
        conditions.append("j.archived_at IS NULL AND j.superseded_at IS NULL")
    elif status == "superseded":
        conditions.append("j.archived_at IS NULL AND j.superseded_at IS NOT NULL")
    elif status == "archived":
        conditions.append("j.archived_at IS NOT NULL")
    if q:
        params.append(f"%{escape_like(q)}%")
        conditions.append(
            f"(j.name ILIKE ${len(params)} ESCAPE '\\' OR j.slug ILIKE ${len(params)} ESCAPE '\\')"
        )
    if type_slug:
        params.append(type_slug)
        conditions.append(f"jt.slug = ${len(params)}")
    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""

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
    return rows, count, pctx
