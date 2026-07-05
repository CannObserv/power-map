"""Admin jurisdiction typeahead — supports the seat picker on roles (#264).

Jurisdictions have no full admin CRUD surface (they arrive via observations);
this module exposes only the read-only typeahead the role seat form needs.
"""

from fastapi import APIRouter, Depends, Request
from fastapi.templating import Jinja2Templates

from src.api.admin.deps import AdminUser, escape_like, get_admin_user, get_db

templates = Jinja2Templates(directory="src/templates")
router = APIRouter(prefix="/jurisdictions", tags=["admin-jurisdictions"])


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
