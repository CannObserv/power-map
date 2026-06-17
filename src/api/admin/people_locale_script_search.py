"""Admin typeahead search for BCP 47 locales + ISO 15924 scripts.

Phase 2b (#123): backs the locale + script combobox inputs in the
person-name edit form. Both endpoints filter the relevant lookup table
by substring on the code or human-readable column, sort by code ASC,
and cap at `limit`. Empty `q` returns no rows; the UI shows a placeholder.

Returns HTML option-list partials shaped for the existing
`typeahead-combobox.js` listbox (`<li role="option" data-id data-label>`).

The pg_trgm GIN indexes added in Phase 2-prep make these queries fast
once the lookup tables grow large; at current row counts the planner
may pick a Seq Scan, which is correct.
"""

from fastapi import APIRouter, Depends, Query, Request
from fastapi.templating import Jinja2Templates

from src.api.admin.deps import AdminUser, escape_like, get_admin_user, get_db

templates = Jinja2Templates(directory="src/templates")
router = APIRouter(prefix="/people", tags=["admin-people-typeahead"])


@router.get("/_locale_search")
async def locale_search(
    request: Request,
    q: str = Query("", max_length=100),
    limit: int = Query(20, ge=1, le=100),
    user: AdminUser = Depends(get_admin_user),
    db=Depends(get_db),
):
    """Render <li> option rows for locales matching `q`.

    Empty/whitespace `q` renders zero rows so the typeahead listbox
    stays empty until the user starts typing.
    """
    needle = q.strip()
    results: list[dict] = []
    if needle:
        pattern = f"%{escape_like(needle)}%"
        rows = await db.fetch(
            "SELECT code, display_name FROM bcp47_locales"
            " WHERE code ILIKE $1 ESCAPE '\\' OR display_name ILIKE $1 ESCAPE '\\'"
            ' ORDER BY code COLLATE "C" ASC'
            " LIMIT $2",
            pattern,
            limit,
        )
        results = [{"code": r["code"], "display_name": r["display_name"]} for r in rows]
    return templates.TemplateResponse(
        request,
        "admin/people/partials/_locale_search_results.html",
        {"results": results},
    )


@router.get("/_script_search")
async def script_search(
    request: Request,
    q: str = Query("", max_length=100),
    limit: int = Query(20, ge=1, le=100),
    user: AdminUser = Depends(get_admin_user),
    db=Depends(get_db),
):
    """Render <li> option rows for scripts matching `q`."""
    needle = q.strip()
    results: list[dict] = []
    if needle:
        pattern = f"%{escape_like(needle)}%"
        rows = await db.fetch(
            "SELECT code, name FROM iso15924_scripts"
            " WHERE code ILIKE $1 ESCAPE '\\' OR name ILIKE $1 ESCAPE '\\'"
            ' ORDER BY code COLLATE "C" ASC'
            " LIMIT $2",
            pattern,
            limit,
        )
        results = [{"code": r["code"], "name": r["name"]} for r in rows]
    return templates.TemplateResponse(
        request,
        "admin/people/partials/_script_search_results.html",
        {"results": results},
    )
