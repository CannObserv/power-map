"""Admin jurisdiction views — list/browse plus the role-picker typeahead.

Phase 1 (#275) adds the read-only browse surface (list + detail). The ``/search/``
typeahead (#264) that feeds the role-type form's jurisdiction picker remains.
"""

from fastapi import APIRouter, Depends, Query, Request
from fastapi.templating import Jinja2Templates

from src.api.admin.deps import AdminUser, escape_like, get_admin_user, get_db, is_htmx
from src.api.admin.jurisdictions_queries import VALID_STATUSES, query_jurisdictions_rows
from src.api.admin.pagination import PAGE_SIZE_DEFAULT, PAGE_SIZE_MAX, PAGE_SIZE_MIN

templates = Jinja2Templates(directory="src/templates")
router = APIRouter(prefix="/jurisdictions", tags=["admin-jurisdictions"])


@router.get("/")
async def jurisdictions_list(
    request: Request,
    q: str = "",
    status: str = "active",
    type_: str = Query("", alias="type"),
    page: int = Query(1, ge=1),
    page_size: int = Query(PAGE_SIZE_DEFAULT, ge=PAGE_SIZE_MIN, le=PAGE_SIZE_MAX),
    user: AdminUser = Depends(get_admin_user),
    db=Depends(get_db),
):
    """List jurisdictions with search, type filter, and status filter."""
    if status not in VALID_STATUSES:
        status = "active"
    type_slug = type_ or None
    rows, count, pctx = await query_jurisdictions_rows(
        db, q=q, status=status, type_slug=type_slug, page=page, page_size=page_size
    )
    types = await db.fetch(
        "SELECT slug, display_name FROM jurisdiction_types ORDER BY display_name"
    )
    ctx = {
        "user": user,
        "active_section": "jurisdictions",
        "jurisdictions": rows,
        "types": types,
        "q": q,
        "status": status,
        "type_slug": type_slug or "",
        "page_size": page_size,
        "total": count,
        "flash_msg": None,
        **pctx,
    }
    template = (
        "admin/jurisdictions/_region.html" if is_htmx(request) else "admin/jurisdictions/list.html"
    )
    return templates.TemplateResponse(request, template, ctx)


@router.get("/search/")
async def jurisdictions_search(
    request: Request,
    q: str = "",
    user: AdminUser = Depends(get_admin_user),
    db=Depends(get_db),
):
    """Typeahead search — returns an HTML fragment of matching jurisdictions."""
    results = []
    if q.strip():
        results = await db.fetch(
            """SELECT id, name, slug
               FROM jurisdictions
               WHERE archived_at IS NULL
                 AND (name ILIKE $1 ESCAPE '\\' OR slug ILIKE $1 ESCAPE '\\')
               ORDER BY name
               LIMIT 20""",
            f"%{escape_like(q.strip())}%",
        )
    return templates.TemplateResponse(
        request,
        "admin/jurisdictions/partials/_search_results.html",
        {"results": results},
    )
