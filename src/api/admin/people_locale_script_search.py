"""Admin typeahead search for BCP 47 locales + ISO 15924 scripts.

Phase 2b (#123): backs the locale + script combobox inputs in the
person-name edit form. Both endpoints filter the relevant lookup table
by substring on the code or human-readable column, sort by code ASC,
and cap at `limit`. Empty `q` returns []; the UI shows a placeholder.

The pg_trgm GIN indexes added in Phase 2-prep make these queries fast
once the lookup tables grow large; at current row counts the planner
may pick a Seq Scan, which is correct.
"""

from fastapi import APIRouter, Depends, Query

from src.api.admin.deps import AdminUser, get_admin_user, get_db

router = APIRouter(prefix="/people", tags=["admin-people-typeahead"])


@router.get("/_locale_search")
async def locale_search(
    q: str = Query("", max_length=100),
    limit: int = Query(20, ge=1, le=100),
    user: AdminUser = Depends(get_admin_user),
    db=Depends(get_db),
) -> list[dict]:
    """Return up to `limit` locales whose code or display_name contains `q`.

    Empty/whitespace `q` returns no rows so the typeahead shows its
    placeholder until the user starts typing.
    """
    needle = q.strip()
    if not needle:
        return []
    pattern = f"%{needle}%"
    rows = await db.fetch(
        "SELECT code, display_name FROM bcp47_locales"
        " WHERE code ILIKE $1 OR display_name ILIKE $1"
        " ORDER BY code ASC"
        " LIMIT $2",
        pattern, limit,
    )
    return [{"code": r["code"], "display_name": r["display_name"]} for r in rows]


@router.get("/_script_search")
async def script_search(
    q: str = Query("", max_length=100),
    limit: int = Query(20, ge=1, le=100),
    user: AdminUser = Depends(get_admin_user),
    db=Depends(get_db),
) -> list[dict]:
    """Return up to `limit` scripts whose code or name contains `q`."""
    needle = q.strip()
    if not needle:
        return []
    pattern = f"%{needle}%"
    rows = await db.fetch(
        "SELECT code, name FROM iso15924_scripts"
        " WHERE code ILIKE $1 OR name ILIKE $1"
        " ORDER BY code ASC"
        " LIMIT $2",
        pattern, limit,
    )
    return [{"code": r["code"], "name": r["name"]} for r in rows]
