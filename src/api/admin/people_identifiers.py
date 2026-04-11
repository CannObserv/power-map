"""Admin CRUD for person identifiers."""

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from markupsafe import escape

from src.api.admin.deps import AdminUser, flash_trigger, get_admin_user, get_db, is_htmx
from src.core.db import generate_id

templates = Jinja2Templates(directory="src/templates")
router = APIRouter(prefix="/people/{person_id}/identifiers", tags=["admin-person-identifiers"])


async def _get_person_or_404(person_id: str, db):
    person = await db.fetchrow("SELECT id FROM people WHERE id=$1", person_id)
    if not person:
        raise HTTPException(status_code=404, detail="Person not found")
    return person


async def _get_identifier_or_404(ident_id: str, person_id: str, db):
    row = await db.fetchrow(
        """SELECT i.*, eit.display_name AS type_name, eit.full_name AS type_full_name
           FROM identifiers i
           JOIN entity_identifier_types eit ON eit.id = i.entity_identifier_type_id
           WHERE i.id=$1 AND i.entity_id=$2 AND eit.entity_type='person'""",
        ident_id,
        person_id,
    )
    if not row:
        raise HTTPException(status_code=404)
    return row


@router.get("/new-row/")
async def identifier_new_row(
    person_id: str,
    request: Request,
    user: AdminUser = Depends(get_admin_user),
    db=Depends(get_db),
):
    """Return empty identifier form row."""
    await _get_person_or_404(person_id, db)
    ident_types = await db.fetch(
        "SELECT * FROM entity_identifier_types"
        " WHERE entity_type='person' ORDER BY display_name"
    )
    return templates.TemplateResponse(
        request,
        "admin/people/partials/_identifier_form_row.html",
        {"person_id": person_id, "ident": None, "ident_types": ident_types},
    )


@router.post("/")
async def identifier_create(
    person_id: str,
    request: Request,
    entity_identifier_type_id: str = Form(...),
    value: str = Form(...),
    user: AdminUser = Depends(get_admin_user),
    db=Depends(get_db),
):
    """Create a new person identifier."""
    await _get_person_or_404(person_id, db)
    iid = generate_id()
    await db.execute(
        "INSERT INTO identifiers (id, entity_id, entity_identifier_type_id, value)"
        " VALUES ($1, $2, $3, $4)",
        iid,
        person_id,
        entity_identifier_type_id,
        value.strip(),
    )
    row = await _get_identifier_or_404(iid, person_id, db)
    if not is_htmx(request):
        return RedirectResponse(f"/admin/people/{person_id}/", status_code=303)
    return templates.TemplateResponse(
        request,
        "admin/people/partials/_identifier_row.html",
        {"person_id": person_id, "ident": row},
        headers=flash_trigger("success", f"<strong>{escape(value.strip())}</strong> added."),
    )


@router.get("/{ident_id}/read-row/")
async def identifier_read_row(
    person_id: str,
    ident_id: str,
    request: Request,
    user: AdminUser = Depends(get_admin_user),
    db=Depends(get_db),
):
    """Return read-only identifier row (used by Cancel on edit form)."""
    row = await _get_identifier_or_404(ident_id, person_id, db)
    return templates.TemplateResponse(
        request,
        "admin/people/partials/_identifier_row.html",
        {"person_id": person_id, "ident": row},
    )


@router.get("/{ident_id}/edit-row/")
async def identifier_edit_row_get(
    person_id: str,
    ident_id: str,
    request: Request,
    user: AdminUser = Depends(get_admin_user),
    db=Depends(get_db),
):
    """Return identifier edit form row."""
    row = await _get_identifier_or_404(ident_id, person_id, db)
    ident_types = await db.fetch(
        "SELECT * FROM entity_identifier_types"
        " WHERE entity_type='person' ORDER BY display_name"
    )
    return templates.TemplateResponse(
        request,
        "admin/people/partials/_identifier_form_row.html",
        {"person_id": person_id, "ident": row, "ident_types": ident_types},
    )


@router.post("/{ident_id}/edit-row/")
async def identifier_edit_row_post(
    person_id: str,
    ident_id: str,
    request: Request,
    entity_identifier_type_id: str = Form(...),
    value: str = Form(...),
    user: AdminUser = Depends(get_admin_user),
    db=Depends(get_db),
):
    """Update a person identifier."""
    await _get_identifier_or_404(ident_id, person_id, db)
    await db.execute(
        "UPDATE identifiers SET entity_identifier_type_id=$1, value=$2 WHERE id=$3",
        entity_identifier_type_id,
        value.strip(),
        ident_id,
    )
    row = await _get_identifier_or_404(ident_id, person_id, db)
    if not is_htmx(request):
        return RedirectResponse(f"/admin/people/{person_id}/", status_code=303)
    return templates.TemplateResponse(
        request,
        "admin/people/partials/_identifier_row.html",
        {"person_id": person_id, "ident": row},
        headers=flash_trigger("success", f"<strong>{escape(value.strip())}</strong> saved."),
    )


@router.delete("/{ident_id}/")
async def identifier_delete(
    person_id: str,
    ident_id: str,
    request: Request,
    user: AdminUser = Depends(get_admin_user),
    db=Depends(get_db),
):
    """Delete a person identifier."""
    existing = await db.fetchrow(
        """SELECT i.id FROM identifiers i
           JOIN entity_identifier_types eit ON eit.id = i.entity_identifier_type_id
           WHERE i.id=$1 AND i.entity_id=$2 AND eit.entity_type='person'""",
        ident_id,
        person_id,
    )
    if not existing:
        raise HTTPException(status_code=404)
    await db.execute("DELETE FROM identifiers WHERE id=$1", ident_id)
    return HTMLResponse(
        content="",
        status_code=200,
        headers=flash_trigger("info", "Identifier removed."),
    )
