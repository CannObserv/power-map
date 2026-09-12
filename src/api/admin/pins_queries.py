"""Query helpers for the curation overlay's pins page (#498).

Every pin in one place, so no override is invisible. The status axis follows
the admin list convention (#306): ``active`` (a pin in force) first and the
default, ``archived`` (unpinned or displaced), and the first-class ``all``.
There is no search box, so no status can hide a match. A value is shown as a
curator reads it — the slot's ``display_sql`` turns a parent's id into its name.
"""

from src.api.admin.overlay_slots import SLOTS
from src.api.admin.pagination import pagination_context

__all__ = ["ENTITY_TYPES", "STATUS_PREDICATES", "VALID_STATUSES", "pin_row", "query_pins"]

# Status → SQL predicate, in dropdown order; ``all`` has no predicate.
STATUS_PREDICATES: dict[str, str] = {
    "active": "o.archived_at IS NULL",
    "archived": "o.archived_at IS NOT NULL",
}
VALID_STATUSES: set[str] = set(STATUS_PREDICATES) | {"all"}
# The entity types a slot exists for — the type filter's options.
ENTITY_TYPES: tuple[str, ...] = tuple(dict.fromkeys(t for t, _ in SLOTS))

_LIST_SQL = (
    "SELECT o.id, o.entity_type, o.entity_id, o.field, o.value, o.note,"
    "       o.created_at, o.archived_at, u.email AS pinned_by,"
    "       CASE o.entity_type WHEN 'person' THEN pdn.display_name"
    "                          ELSE odn.display_name END AS entity_name"
    "  FROM curation_overlay o"
    "  LEFT JOIN app_users u ON u.id = o.created_by"
    "  LEFT JOIN v_person_display_names pdn"
    "         ON o.entity_type = 'person' AND pdn.person_id = o.entity_id"
    "  LEFT JOIN v_org_display_names odn"
    "         ON o.entity_type = 'organization' AND odn.organization_id = o.entity_id"
)


async def _as_read(db, row) -> dict:
    """A pin row with its slot label and its value as a curator reads it."""
    out = dict(row)
    slot = SLOTS.get((out["entity_type"], out["field"]))
    out["label"] = slot.label if slot else out["field"]
    out["display"] = out["value"]
    if slot and slot.display_sql and out["value"] is not None:
        out["display"] = await db.fetchval(slot.display_sql, out["value"]) or out["value"]
    return out


async def query_pins(
    db, *, status: str, entity_type: str | None, page: int, page_size: int
) -> tuple[list[dict], int, dict]:
    """One page of pins under the filter state; return ``(rows, count, pctx)``.

    Live ones first, newest first, id last so the order — and so each page — is
    stable. ``pctx`` is ``pagination_context()``, its ``page`` clamped to range.
    """
    conditions: list[str] = [STATUS_PREDICATES[status]] if status != "all" else []
    params: list = []
    if entity_type:
        params.append(entity_type)
        conditions.append(f"o.entity_type = ${len(params)}")
    where = f" WHERE {' AND '.join(conditions)}" if conditions else ""
    count = await db.fetchval(f"SELECT count(*) FROM curation_overlay o{where}", *params)
    pctx = pagination_context(page, count, page_size)
    params += [page_size, (pctx["page"] - 1) * page_size]
    order = " ORDER BY o.archived_at IS NOT NULL, o.created_at DESC, o.id"
    limit = f" LIMIT ${len(params) - 1} OFFSET ${len(params)}"
    rows = await db.fetch(_LIST_SQL + where + order + limit, *params)
    return [await _as_read(db, r) for r in rows], count, pctx


async def pin_row(db, pin_id: str) -> dict | None:
    """One pin as the list shows it, or None."""
    row = await db.fetchrow(_LIST_SQL + " WHERE o.id = $1", pin_id)
    return await _as_read(db, row) if row is not None else None
