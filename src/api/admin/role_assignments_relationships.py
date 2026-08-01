"""Admin CRUD for role-assignment relationship edges (#301).

Makes the role-assignment detail-page Relationships panel interactive: add a
directional edge (target-assignment typeahead + rel_type + direction + validity +
notes), inline-edit validity/notes, and remove (soft-delete via ``archived_at`` —
the retract model, so the change feed drops the anchor). The admin write path
enforces the temporal invariant via ``check_edge_within_assignments`` (422); the
DB guards ``chk_no_self_rel_assignment`` / ``chk_edge_valid_range`` backstop it.
"""

import asyncpg
from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from src.api.admin.deps import (
    AdminUser,
    escape_like,
    flash_trigger,
    get_admin_user,
    get_db,
    is_htmx,
    parse_validity_fields,
    with_flash,
)
from src.core.assignment_relationships import (
    EdgeOutsideAssignmentWindow,
    check_edge_within_assignments,
)
from src.core.db import generate_id

templates = Jinja2Templates(directory="src/templates")
router = APIRouter(
    prefix="/role-assignments/{ra_id}/relationships",
    tags=["admin-role-assignment-relationships"],
)

# One edge joined to its type + both endpoints' person / role labels.
_REL_ROW_SQL = """
    SELECT r.id, r.from_assignment_id, r.to_assignment_id, r.rel_type_id,
           r.valid_from, r.valid_until, r.notes, r.archived_at,
           t.slug AS rel_type, t.display_name AS rel_type_name,
           fp.display_name AS from_person, fr.title AS from_role,
           tp.display_name AS to_person, tr.title AS to_role
    FROM role_assignment_relationships r
    JOIN role_assignment_relationship_types t ON t.id = r.rel_type_id
    JOIN role_assignments fa ON fa.id = r.from_assignment_id
    JOIN role_assignments ta ON ta.id = r.to_assignment_id
    JOIN roles fr ON fr.id = fa.role_id
    JOIN roles tr ON tr.id = ta.role_id
    LEFT JOIN v_person_display_names fp ON fp.person_id = fa.person_id
    LEFT JOIN v_person_display_names tp ON tp.person_id = ta.person_id
    WHERE r.id = $1
"""

# Assignments touching the given assignment (either side), active edges only —
# the panel's row source, shared with the detail-page context builder.
_PANEL_SQL = _REL_ROW_SQL.replace(
    "WHERE r.id = $1",
    "WHERE (r.from_assignment_id = $1 OR r.to_assignment_id = $1) AND r.archived_at IS NULL"
    " ORDER BY r.created_at DESC, r.id DESC",
)


async def fetch_panel_rows(db, ra_id: str):
    """Active relationship rows touching ``ra_id`` (both directions). Shared with
    the role-assignment detail route so the panel renders on first paint."""
    return await db.fetch(_PANEL_SQL, ra_id)


async def _get_ra_or_404(ra_id: str, db):
    row = await db.fetchrow(
        "SELECT id FROM role_assignments WHERE id=$1 AND archived_at IS NULL", ra_id
    )
    if not row:
        raise HTTPException(status_code=404, detail="Role assignment not found")
    return row


async def _get_edge_or_404(rel_id: str, ra_id: str, db):
    row = await db.fetchrow(
        _REL_ROW_SQL + " AND (r.from_assignment_id = $2 OR r.to_assignment_id = $2)",
        rel_id,
        ra_id,
    )
    if not row:
        raise HTTPException(status_code=404, detail="Relationship not found")
    return row


async def _rel_types(db):
    return await db.fetch(
        "SELECT id, slug, display_name FROM role_assignment_relationship_types"
        " ORDER BY display_name"
    )


def _assignment_label(row) -> str:
    person = row["display_name"] or "(unnamed)"
    return f"{person} — {row['title']}"


async def _render_add_form(request, ra_id, db, *, values, errors, status_code=200):
    return templates.TemplateResponse(
        request,
        "admin/role_assignments/partials/_relationship_form_row.html",
        {
            "ra_id": ra_id,
            "rel_types": await _rel_types(db),
            "values": values,
            "target_label": values.get("target_label", ""),
            "errors": errors,
        },
        status_code=status_code,
    )


@router.get("/search/")
async def relationship_search(
    ra_id: str,
    request: Request,
    q: str = "",
    user: AdminUser = Depends(get_admin_user),
    db=Depends(get_db),
):
    """Typeahead search — active assignments (person + role) matching q, excluding self."""
    results = []
    if q.strip():
        results = await db.fetch(
            """SELECT ra.id, pdn.display_name, r.title
               FROM role_assignments ra
               JOIN roles r ON r.id = ra.role_id
               LEFT JOIN v_person_display_names pdn ON pdn.person_id = ra.person_id
               WHERE ra.archived_at IS NULL AND ra.id <> $2
                 AND (pdn.display_name ILIKE $1 ESCAPE '\\' OR r.title ILIKE $1 ESCAPE '\\')
               ORDER BY pdn.display_name NULLS LAST, r.title
               LIMIT 20""",
            f"%{escape_like(q.strip())}%",
            ra_id,
        )
    return templates.TemplateResponse(
        request,
        "admin/role_assignments/partials/_relationship_search_results.html",
        {"results": [{"id": r["id"], "label": _assignment_label(r)} for r in results]},
    )


@router.get("/new-row/")
async def relationship_new_row(
    ra_id: str,
    request: Request,
    user: AdminUser = Depends(get_admin_user),
    db=Depends(get_db),
):
    """Return an empty add-relationship form row."""
    await _get_ra_or_404(ra_id, db)
    return await _render_add_form(request, ra_id, db, values={}, errors={})


@router.post("/")
async def relationship_create(
    ra_id: str,
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
    """Create a directional edge. ``outgoing`` = this assignment is the from (staffer)."""
    await _get_ra_or_404(ra_id, db)
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
        errors["target_id"] = "Select a target assignment"
    if not rel_type_id.strip():
        errors["rel_type_id"] = "Select a relationship type"
    if target_id.strip() and target_id.strip() == ra_id:
        errors["target_id"] = "An assignment can't relate to itself"
    vf, vu = parse_validity_fields(valid_from, valid_until, errors)
    if errors:
        return await _render_add_form(
            request, ra_id, db, values=values, errors=errors, status_code=422
        )

    from_id, to_id = (ra_id, target_id) if direction != "incoming" else (target_id, ra_id)

    try:
        await check_edge_within_assignments(db, from_id, to_id, vf, vu)
    except EdgeOutsideAssignmentWindow as exc:
        errors["valid_from"] = f"Edge window falls outside the assignment windows: {exc}"
        return await _render_add_form(
            request, ra_id, db, values=values, errors=errors, status_code=422
        )

    rid = generate_id()
    try:
        await db.execute(
            "INSERT INTO role_assignment_relationships (id, from_assignment_id,"
            " to_assignment_id, rel_type_id, valid_from, valid_until, notes)"
            " VALUES ($1,$2,$3,$4,$5,$6,$7)",
            rid,
            from_id,
            to_id,
            rel_type_id,
            vf,
            vu,
            notes.strip() or None,
        )
    except asyncpg.UniqueViolationError:
        errors["target_id"] = "That relationship already exists"
        return await _render_add_form(
            request, ra_id, db, values=values, errors=errors, status_code=422
        )
    except asyncpg.ForeignKeyViolationError:
        errors["target_id"] = "Unknown assignment or relationship type"
        return await _render_add_form(
            request, ra_id, db, values=values, errors=errors, status_code=422
        )
    except asyncpg.CheckViolationError:
        errors["target_id"] = "Invalid relationship (self-edge or bad date range)"
        return await _render_add_form(
            request, ra_id, db, values=values, errors=errors, status_code=422
        )

    row = await db.fetchrow(_REL_ROW_SQL, rid)
    if not is_htmx(request):
        return RedirectResponse(
            with_flash(f"/admin/role-assignments/{ra_id}/", "saved"), status_code=303
        )
    return templates.TemplateResponse(
        request,
        "admin/role_assignments/partials/_relationship_row.html",
        {"ra_id": ra_id, "rel": row},
        headers=flash_trigger("success", "Relationship added."),
    )


@router.get("/{rel_id}/read-row/")
async def relationship_read_row(
    ra_id: str,
    rel_id: str,
    request: Request,
    user: AdminUser = Depends(get_admin_user),
    db=Depends(get_db),
):
    """Return the read-only edge row (Cancel target on the edit form)."""
    row = await _get_edge_or_404(rel_id, ra_id, db)
    return templates.TemplateResponse(
        request,
        "admin/role_assignments/partials/_relationship_row.html",
        {"ra_id": ra_id, "rel": row},
    )


@router.get("/{rel_id}/edit-row/")
async def relationship_edit_row(
    ra_id: str,
    rel_id: str,
    request: Request,
    user: AdminUser = Depends(get_admin_user),
    db=Depends(get_db),
):
    """Return the inline validity/notes-edit form for an existing edge."""
    row = await _get_edge_or_404(rel_id, ra_id, db)
    return templates.TemplateResponse(
        request,
        "admin/role_assignments/partials/_relationship_edit_row.html",
        {"ra_id": ra_id, "rel": row, "errors": {}},
    )


@router.post("/{rel_id}/edit-row/")
async def relationship_edit_row_post(
    ra_id: str,
    rel_id: str,
    request: Request,
    valid_from: str = Form(""),
    valid_until: str = Form(""),
    notes: str = Form(""),
    user: AdminUser = Depends(get_admin_user),
    db=Depends(get_db),
):
    """Save a validity/notes edit on an existing edge."""
    row = await _get_edge_or_404(rel_id, ra_id, db)
    errors: dict[str, str] = {}
    vf, vu = parse_validity_fields(valid_from, valid_until, errors)
    if not errors:
        try:
            await check_edge_within_assignments(
                db, row["from_assignment_id"], row["to_assignment_id"], vf, vu
            )
        except EdgeOutsideAssignmentWindow as exc:
            errors["valid_from"] = f"Edge window falls outside the assignment windows: {exc}"
    if errors:
        return templates.TemplateResponse(
            request,
            "admin/role_assignments/partials/_relationship_edit_row.html",
            {"ra_id": ra_id, "rel": row, "errors": errors},
            status_code=422,
        )
    try:
        await db.execute(
            "UPDATE role_assignment_relationships"
            " SET valid_from=$1, valid_until=$2, notes=$3 WHERE id=$4 AND archived_at IS NULL",
            vf,
            vu,
            notes.strip() or None,
            rel_id,
        )
    except asyncpg.CheckViolationError:
        errors["valid_until"] = "Valid-until must not precede valid-from"
        return templates.TemplateResponse(
            request,
            "admin/role_assignments/partials/_relationship_edit_row.html",
            {"ra_id": ra_id, "rel": row, "errors": errors},
            status_code=422,
        )
    updated = await db.fetchrow(_REL_ROW_SQL, rel_id)
    if not is_htmx(request):
        return RedirectResponse(
            with_flash(f"/admin/role-assignments/{ra_id}/", "saved"), status_code=303
        )
    return templates.TemplateResponse(
        request,
        "admin/role_assignments/partials/_relationship_row.html",
        {"ra_id": ra_id, "rel": updated},
        headers=flash_trigger("success", "Relationship saved."),
    )


@router.delete("/{rel_id}/")
async def relationship_delete(
    ra_id: str,
    rel_id: str,
    request: Request,
    user: AdminUser = Depends(get_admin_user),
    db=Depends(get_db),
):
    """Remove an edge — soft-delete via ``archived_at`` (retract model, #322)."""
    await _get_edge_or_404(rel_id, ra_id, db)
    await db.execute(
        "UPDATE role_assignment_relationships SET archived_at = NOW()"
        " WHERE id=$1 AND archived_at IS NULL",
        rel_id,
    )
    if not is_htmx(request):
        return RedirectResponse(
            with_flash(f"/admin/role-assignments/{ra_id}/", "removed"), status_code=303
        )
    return HTMLResponse(
        content="", status_code=200, headers=flash_trigger("success", "Relationship removed.")
    )
