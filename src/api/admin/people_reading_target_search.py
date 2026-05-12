"""Admin typeahead — same-person visual rows that can be `reading_of` parents.

Phase 2c (#123): backs the conditional `reading_of_id` typeahead in the
person-name edit form. Returns rows from the SAME person whose
`name_type` is NOT in {'reading','romanization','mrz'} — only "visual"
rows are valid parents for a reading/romanization/mrz row.

Empty `q` returns no rows; substring filter on `name` with escape_like
+ ESCAPE '\\'; ordered canonical-first then by name; capped at limit.
404s when the person doesn't exist (matches other person-scoped
endpoints).
"""

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.templating import Jinja2Templates

from src.api.admin.deps import AdminUser, escape_like, get_admin_user, get_db

templates = Jinja2Templates(directory="src/templates")
router = APIRouter(prefix="/people", tags=["admin-people-typeahead"])

# Mirror person_names.name_type CHECK; rows in this set are READINGS (children),
# never valid parents.
_READING_TYPES = ("reading", "romanization", "mrz")


@router.get("/{person_id}/_reading_target_search")
async def reading_target_search(
    person_id: str,
    request: Request,
    q: str = Query("", max_length=100),
    limit: int = Query(20, ge=1, le=100),
    user: AdminUser = Depends(get_admin_user),
    db=Depends(get_db),
):
    """Render <li> option rows for visual names of `person_id` matching `q`."""
    person = await db.fetchrow("SELECT id FROM people WHERE id=$1", person_id)
    if not person:
        raise HTTPException(status_code=404, detail="Person not found")

    needle = q.strip()
    results: list[dict] = []
    if needle:
        pattern = f"%{escape_like(needle)}%"
        # Only public rows — keeps the typeahead consistent with the
        # default detail view, which hides legal_only/hidden behind the
        # `?show_historical=1` toggle (visibility-allowlist via the
        # `visibility = 'public'` predicate, satisfying the lint rule).
        # name_type filter uses the `_READING_TYPES` tuple so the SQL
        # tracks `_validate_reading_of_target`'s rejection set.
        rows = await db.fetch(
            "SELECT id, name, name_type, is_canonical FROM person_names"
            " WHERE person_id = $1"
            "   AND visibility = 'public'"
            "   AND name_type <> ALL($2::text[])"
            "   AND name ILIKE $3 ESCAPE '\\'"
            " ORDER BY is_canonical DESC, name_type, name"
            " LIMIT $4",
            person_id,
            list(_READING_TYPES),
            pattern,
            limit,
        )
        results = [
            {
                "id": r["id"],
                "name": r["name"],
                "name_type": r["name_type"],
                "is_canonical": r["is_canonical"],
            }
            for r in rows
        ]
    return templates.TemplateResponse(
        request,
        "admin/people/partials/_reading_target_search_results.html",
        {"results": results},
    )
