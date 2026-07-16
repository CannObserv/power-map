"""Admin views for the API request log (#260) — observability of public API traffic.

Read-only list + detail over ``api_request_log`` (populated by the public-API
capture middleware). The list defaults to the observations + changes route
groups and hides empty ``/changes`` polls; both are widenable via query params
so a filtered view is shareable. Detail pretty-prints the raw bodies and
resolves a result entity to its admin link.
"""

import json

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.templating import Jinja2Templates

from src.api.admin.deps import AdminUser, get_admin_user, get_db
from src.core.anomaly import HOURLY_REQUEST_THRESHOLD, key_activity

templates = Jinja2Templates(directory="src/templates")
router = APIRouter(prefix="/activity/requests", tags=["admin-activity-requests"])

PAGE_SIZE = 50
_WINDOWS = {"24h": 24, "7d": 168}

# entity_type -> admin detail URL prefix + existence table, for link resolution.
# jurisdiction has no admin screen, so it is intentionally absent (renders a plain id).
_ENTITY_LINK = {
    "person": ("/admin/people/", "people"),
    "organization": ("/admin/orgs/", "organizations"),
    "role": ("/admin/roles/", "roles"),
    "role_assignment": ("/admin/role-assignments/", "role_assignments"),
}

_STATS_SQL = """
SELECT
    COUNT(*) AS total,
    COUNT(*) FILTER (WHERE route_group='observations' AND disposition='new') AS obs_new,
    COUNT(*) FILTER (WHERE route_group='observations' AND disposition='auto-attached')
        AS obs_attached,
    COUNT(*) FILTER (WHERE route_group='observations' AND disposition='rejected')
        AS obs_rejected,
    COUNT(*) FILTER (WHERE route_group='changes') AS changes_polls,
    COALESCE(SUM(item_count) FILTER (WHERE route_group='changes'), 0) AS changes_rows,
    COUNT(*) FILTER (WHERE status_code >= 400) AS errors,
    COUNT(DISTINCT api_key_id) AS active_keys
FROM api_request_log
WHERE occurred_at >= NOW() - make_interval(hours => $1::int)
"""


def _build_filters(group, key, status, disposition, show_empty, q):
    """Return (where_sql, args) for the list query. Columns are aliased ``r``."""
    conds: list[str] = []
    args: list = []
    if group is None:
        conds.append("r.route_group IN ('observations','changes')")
    elif group != "all":
        args.append(group)
        conds.append(f"r.route_group = ${len(args)}")
    if key:
        args.append(key)
        conds.append(f"r.api_key_id = ${len(args)}")
    if status in ("2xx", "3xx", "4xx", "5xx"):
        low = int(status[0]) * 100
        args.append(low)
        conds.append(f"r.status_code >= ${len(args)}")
        args.append(low + 100)
        conds.append(f"r.status_code < ${len(args)}")
    if disposition:
        args.append(disposition)
        conds.append(f"r.disposition = ${len(args)}")
    if not show_empty:
        conds.append("r.is_empty = FALSE")
    if q:
        args.append(f"%{q}%")
        conds.append(f"(r.path ILIKE ${len(args)} OR r.reason ILIKE ${len(args)})")
    where = ("WHERE " + " AND ".join(conds)) if conds else ""
    return where, args


@router.get("/")
async def request_list(
    request: Request,
    group: str | None = Query(default=None),
    key: str | None = Query(default=None),
    status: str | None = Query(default=None),
    disposition: str | None = Query(default=None),
    show_empty: bool = Query(default=False),
    q: str | None = Query(default=None),
    window: str = Query(default="24h"),
    page: int = Query(default=1, ge=1),
    user: AdminUser = Depends(get_admin_user),
    db=Depends(get_db),
):
    """Filterable, paginated list of captured API requests."""
    where, args = _build_filters(group, key, status, disposition, show_empty, q)
    total = await db.fetchval(f"SELECT COUNT(*) FROM api_request_log r {where}", *args)
    offset = (page - 1) * PAGE_SIZE
    rows = await db.fetch(
        f"SELECT r.*, k.label AS key_label "
        f"FROM api_request_log r LEFT JOIN api_keys k ON k.id = r.api_key_id "
        f"{where} ORDER BY r.id DESC LIMIT ${len(args) + 1} OFFSET ${len(args) + 2}",
        *args,
        PAGE_SIZE,
        offset,
    )
    window_hours = _WINDOWS.get(window, 24)
    stats = await db.fetchrow(_STATS_SQL, window_hours)
    # Per-key breakdown (#294) — hot keys visible at a glance; a per-hour rate
    # at/above the anomaly threshold highlights the row.
    per_key = [
        {
            "api_key_id": a.api_key_id,
            "key_label": a.key_label,
            "request_count": a.request_count,
            "throttled_count": a.throttled_count,
            "last_seen": a.last_seen,
            "per_hour": round(a.request_count / window_hours, 1),
            "hot": a.request_count / window_hours >= HOURLY_REQUEST_THRESHOLD,
        }
        for a in await key_activity(db, window_hours=window_hours)
    ]
    keys = await db.fetch(
        "SELECT DISTINCT k.id, k.label FROM api_keys k "
        "JOIN api_request_log r ON r.api_key_id = k.id ORDER BY k.label"
    )
    return templates.TemplateResponse(
        request,
        "admin/activity/requests/list.html",
        {
            "user": user,
            "active_section": "activity_requests",
            "rows": rows,
            "total": total,
            "page": page,
            "page_size": PAGE_SIZE,
            "stats": stats,
            "per_key": per_key,
            "keys": keys,
            "filters": {
                "group": group or "",
                "key": key or "",
                "status": status or "",
                "disposition": disposition or "",
                "show_empty": show_empty,
                "q": q or "",
                "window": window if window in _WINDOWS else "24h",
            },
        },
    )


@router.get("/{log_id}/")
async def request_detail(
    log_id: int,
    request: Request,
    user: AdminUser = Depends(get_admin_user),
    db=Depends(get_db),
):
    """Detail for one captured request — metadata, bodies, resolved entity link."""
    row = await db.fetchrow(
        "SELECT r.*, k.label AS key_label "
        "FROM api_request_log r LEFT JOIN api_keys k ON k.id = r.api_key_id "
        "WHERE r.id = $1",
        log_id,
    )
    if not row:
        raise HTTPException(status_code=404, detail="Request log entry not found")

    scopes = []
    if row["api_key_id"]:
        scope_rows = await db.fetch(
            "SELECT scope_id FROM api_key_scopes WHERE api_key_id = $1 ORDER BY scope_id",
            row["api_key_id"],
        )
        scopes = [s["scope_id"] for s in scope_rows]

    entity_link = None
    entity_removed = False
    et, eid = row["entity_type"], row["result_entity_id"]
    if eid and et in _ENTITY_LINK:
        prefix, table = _ENTITY_LINK[et]
        exists = await db.fetchval(f"SELECT 1 FROM {table} WHERE id = $1", eid)
        if exists:
            entity_link = f"{prefix}{eid}/"
        else:
            entity_removed = True

    return templates.TemplateResponse(
        request,
        "admin/activity/requests/detail.html",
        {
            "user": user,
            "active_section": "activity_requests",
            "row": row,
            "scopes": scopes,
            "entity_link": entity_link,
            "entity_removed": entity_removed,
            "request_body_pretty": _pretty(row["request_body"]),
            "response_body_pretty": _pretty(row["response_body"]),
        },
    )


def _pretty(raw: str | None) -> str | None:
    """Pretty-print a stored JSONB body (asyncpg returns jsonb as str)."""
    if raw is None:
        return None
    try:
        return json.dumps(json.loads(raw), indent=2, ensure_ascii=False)
    except (ValueError, TypeError):
        return raw
