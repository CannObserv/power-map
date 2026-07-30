"""Admin CRUD for jurisdiction relationship edges (#275 Phase 3).

Makes the detail-page Relationships panel interactive: add a typed edge (target
typeahead + rel_type + direction for asymmetric types + validity + notes),
inline-edit validity (the temporal *end* of a relationship), and hard-delete
(mistake correction — the table has no ``archived_at``). The DB guards
``chk_no_self_rel`` and ``chk_rel_valid_range`` are surfaced as 422.
"""

import asyncpg
from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from src.api.admin.deps import (
    AdminUser,
    flash_trigger,
    get_admin_user,
    get_db,
    is_htmx,
    parse_validity_fields,
)
from src.core.db import generate_id

templates = Jinja2Templates(directory="src/templates")
router = APIRouter(
    prefix="/jurisdictions/{jurisdiction_id}/relationships",
    tags=["admin-jurisdiction-relationships"],
)

# One edge joined to its type + both endpoint names (for row rendering).
_REL_ROW_SQL = """
    SELECT jr.id, jr.from_id, jr.to_id, jr.rel_type_id,
           jr.valid_from, jr.valid_until, jr.notes,
           jrt.display_name AS rel_type_name, jrt.category, jrt.is_symmetric,
           jf.name AS from_name, jto.name AS to_name
    FROM jurisdiction_relationships jr
    JOIN jurisdiction_relationship_types jrt ON jrt.id = jr.rel_type_id
    JOIN jurisdictions jf ON jf.id = jr.from_id
    JOIN jurisdictions jto ON jto.id = jr.to_id
    WHERE jr.id = $1
"""


async def _get_jurisdiction_or_404(jurisdiction_id: str, db):
    row = await db.fetchrow("SELECT id, name FROM jurisdictions WHERE id=$1", jurisdiction_id)
    if not row:
        raise HTTPException(status_code=404, detail="Jurisdiction not found")
    return row


async def _get_edge_or_404(rel_id: str, jurisdiction_id: str, db):
    """Fetch an edge that touches this jurisdiction (either endpoint), or 404."""
    row = await db.fetchrow(
        _REL_ROW_SQL + " AND (jr.from_id = $2 OR jr.to_id = $2)", rel_id, jurisdiction_id
    )
    if not row:
        raise HTTPException(status_code=404, detail="Relationship not found")
    return row


async def _rel_types(db):
    return await db.fetch(
        "SELECT id, slug, display_name, category, is_symmetric"
        " FROM jurisdiction_relationship_types ORDER BY category, display_name"
    )


async def _render_add_form(request, jurisdiction_id, db, *, values, errors, status_code=200):
    jur = await db.fetchrow("SELECT name FROM jurisdictions WHERE id=$1", jurisdiction_id)
    # Preserve the picked target's label so a validation-error re-render keeps the
    # typeahead populated (the hidden target_id alone would leave the box blank).
    target_label = ""
    target_id = (values.get("target_id") or "").strip()
    if target_id:
        t = await db.fetchrow("SELECT name FROM jurisdictions WHERE id=$1", target_id)
        target_label = t["name"] if t else ""
    return templates.TemplateResponse(
        request,
        "admin/jurisdictions/partials/_relationship_form_row.html",
        {
            "jurisdiction_id": jurisdiction_id,
            "jurisdiction_name": jur["name"] if jur else "This jurisdiction",
            "rel_types": await _rel_types(db),
            "values": values,
            "target_label": target_label,
            "errors": errors,
            "rel": None,
        },
        status_code=status_code,
    )


@router.get("/new-row/")
async def relationship_new_row(
    jurisdiction_id: str,
    request: Request,
    user: AdminUser = Depends(get_admin_user),
    db=Depends(get_db),
):
    """Return an empty add-relationship form row."""
    await _get_jurisdiction_or_404(jurisdiction_id, db)
    return await _render_add_form(request, jurisdiction_id, db, values={}, errors={})


@router.post("/")
async def relationship_create(
    jurisdiction_id: str,
    request: Request,
    target_id: str = Form(""),
    rel_type_id: str = Form(""),
    direction: str = Form("outgoing"),
    valid_from: str = Form(""),
    valid_until: str = Form(""),
    notes: str = Form(""),
    user: AdminUser = Depends(get_admin_user),
    db=Depends(get_db),
):
    """Create a relationship edge. Symmetric types store once; asymmetric honor direction."""
    await _get_jurisdiction_or_404(jurisdiction_id, db)
    values = {
        "target_id": target_id,
        "rel_type_id": rel_type_id,
        "direction": direction,
        "valid_from": valid_from,
        "valid_until": valid_until,
        "notes": notes,
    }
    errors: dict[str, str] = {}
    if not target_id.strip():
        errors["target_id"] = "Select a target jurisdiction"
    if not rel_type_id.strip():
        errors["rel_type_id"] = "Select a relationship type"
    if target_id.strip() and target_id.strip() == jurisdiction_id:
        errors["target_id"] = "A jurisdiction can't relate to itself"
    vf, vu = parse_validity_fields(valid_from, valid_until, errors)
    if errors:
        return await _render_add_form(
            request, jurisdiction_id, db, values=values, errors=errors, status_code=422
        )

    # Orientation: symmetric types store once (from = current). Asymmetric honor
    # the direction toggle — "incoming" means the current jurisdiction is the TO side.
    rt = await db.fetchrow(
        "SELECT is_symmetric FROM jurisdiction_relationship_types WHERE id=$1", rel_type_id
    )
    incoming = not (rt and rt["is_symmetric"]) and direction == "incoming"
    from_id, to_id = (target_id, jurisdiction_id) if incoming else (jurisdiction_id, target_id)

    rid = generate_id()
    try:
        await db.execute(
            "INSERT INTO jurisdiction_relationships"
            " (id, from_id, to_id, rel_type_id, valid_from, valid_until, notes)"
            " VALUES ($1, $2, $3, $4, $5, $6, $7)",
            rid,
            from_id,
            to_id,
            rel_type_id,
            vf,
            vu,
            notes.strip() or None,
        )
    except asyncpg.ForeignKeyViolationError:
        errors["target_id"] = "Unknown jurisdiction or relationship type"
        return await _render_add_form(
            request, jurisdiction_id, db, values=values, errors=errors, status_code=422
        )
    except asyncpg.CheckViolationError:
        errors["target_id"] = "Invalid relationship (self-edge or bad date range)"
        return await _render_add_form(
            request, jurisdiction_id, db, values=values, errors=errors, status_code=422
        )

    row = await db.fetchrow(_REL_ROW_SQL, rid)
    if not is_htmx(request):
        return RedirectResponse(f"/admin/jurisdictions/{jurisdiction_id}/", status_code=303)
    return templates.TemplateResponse(
        request,
        "admin/jurisdictions/partials/_relationship_row.html",
        {"jurisdiction_id": jurisdiction_id, "rel": row},
        headers=flash_trigger("success", "Relationship added."),
    )


@router.get("/{rel_id}/read-row/")
async def relationship_read_row(
    jurisdiction_id: str,
    rel_id: str,
    request: Request,
    user: AdminUser = Depends(get_admin_user),
    db=Depends(get_db),
):
    """Return the read-only edge row (Cancel target on the edit form)."""
    row = await _get_edge_or_404(rel_id, jurisdiction_id, db)
    return templates.TemplateResponse(
        request,
        "admin/jurisdictions/partials/_relationship_row.html",
        {"jurisdiction_id": jurisdiction_id, "rel": row},
    )


@router.get("/{rel_id}/edit-row/")
async def relationship_edit_row(
    jurisdiction_id: str,
    rel_id: str,
    request: Request,
    user: AdminUser = Depends(get_admin_user),
    db=Depends(get_db),
):
    """Return the inline validity-edit form for an existing edge."""
    row = await _get_edge_or_404(rel_id, jurisdiction_id, db)
    return templates.TemplateResponse(
        request,
        "admin/jurisdictions/partials/_relationship_edit_row.html",
        {"jurisdiction_id": jurisdiction_id, "rel": row, "errors": {}},
    )


@router.post("/{rel_id}/edit-row/")
async def relationship_edit_row_post(
    jurisdiction_id: str,
    rel_id: str,
    request: Request,
    valid_from: str = Form(""),
    valid_until: str = Form(""),
    notes: str = Form(""),
    user: AdminUser = Depends(get_admin_user),
    db=Depends(get_db),
):
    """Save a validity/notes edit on an existing edge (its temporal end)."""
    row = await _get_edge_or_404(rel_id, jurisdiction_id, db)
    errors: dict[str, str] = {}
    vf, vu = parse_validity_fields(valid_from, valid_until, errors)
    if errors:
        return templates.TemplateResponse(
            request,
            "admin/jurisdictions/partials/_relationship_edit_row.html",
            {"jurisdiction_id": jurisdiction_id, "rel": row, "errors": errors},
            status_code=422,
        )
    try:
        await db.execute(
            "UPDATE jurisdiction_relationships"
            " SET valid_from=$1, valid_until=$2, notes=$3 WHERE id=$4",
            vf,
            vu,
            notes.strip() or None,
            rel_id,
        )
    except asyncpg.CheckViolationError:
        # DB-layer backstop for chk_rel_valid_range — parse_validity_fields above
        # normally rejects an inverted range first, so this fires only on a direct
        # constraint violation the app didn't pre-check.
        errors["valid_until"] = "Valid-until must not precede valid-from"
        return templates.TemplateResponse(
            request,
            "admin/jurisdictions/partials/_relationship_edit_row.html",
            {"jurisdiction_id": jurisdiction_id, "rel": row, "errors": errors},
            status_code=422,
        )
    updated = await db.fetchrow(_REL_ROW_SQL, rel_id)
    if not is_htmx(request):
        return RedirectResponse(f"/admin/jurisdictions/{jurisdiction_id}/", status_code=303)
    return templates.TemplateResponse(
        request,
        "admin/jurisdictions/partials/_relationship_row.html",
        {"jurisdiction_id": jurisdiction_id, "rel": updated},
        headers=flash_trigger("success", "Relationship saved."),
    )


@router.delete("/{rel_id}/")
async def relationship_delete(
    jurisdiction_id: str,
    rel_id: str,
    request: Request,
    user: AdminUser = Depends(get_admin_user),
    db=Depends(get_db),
):
    """Hard-delete a relationship edge (mistake correction)."""
    await _get_edge_or_404(rel_id, jurisdiction_id, db)
    await db.execute("DELETE FROM jurisdiction_relationships WHERE id=$1", rel_id)
    if not is_htmx(request):
        return RedirectResponse(f"/admin/jurisdictions/{jurisdiction_id}/", status_code=303)
    return HTMLResponse(
        content="", status_code=200, headers=flash_trigger("info", "Relationship removed.")
    )
