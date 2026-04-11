"""Admin CRUD for person links."""

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from markupsafe import escape

from src.api.admin.deps import AdminUser, flash_trigger, get_admin_user, get_db, is_htmx
from src.core.db import generate_id

templates = Jinja2Templates(directory="src/templates")
router = APIRouter(prefix="/people/{person_id}/links", tags=["admin-person-links"])


async def _get_person_or_404(person_id: str, db):
    person = await db.fetchrow("SELECT id FROM people WHERE id=$1", person_id)
    if not person:
        raise HTTPException(status_code=404, detail="Person not found")
    return person


async def _get_link_or_404(link_id: str, person_id: str, db):
    row = await db.fetchrow(
        """SELECT l.*, lt.display_name AS link_type_name, lt.is_social
           FROM links l JOIN link_types lt ON lt.id = l.link_type_id
           WHERE l.id=$1 AND l.entity_type='person' AND l.entity_id=$2""",
        link_id,
        person_id,
    )
    if not row:
        raise HTTPException(status_code=404)
    return row


@router.get("/new-row/")
async def link_new_row(
    person_id: str,
    request: Request,
    user: AdminUser = Depends(get_admin_user),
    db=Depends(get_db),
):
    """Return empty link form row."""
    await _get_person_or_404(person_id, db)
    link_types = await db.fetch(
        "SELECT * FROM link_types ORDER BY is_social DESC, display_name"
    )
    return templates.TemplateResponse(
        request,
        "admin/people/partials/_link_form_row.html",
        {"person_id": person_id, "l": None, "link_types": link_types},
    )


@router.post("/")
async def link_create(
    person_id: str,
    request: Request,
    url: str = Form(...),
    link_type_id: str = Form(...),
    is_active: str = Form(""),
    user: AdminUser = Depends(get_admin_user),
    db=Depends(get_db),
):
    """Create a new person link."""
    await _get_person_or_404(person_id, db)
    lid = generate_id()
    await db.execute(
        "INSERT INTO links"
        " (id, entity_type, entity_id, url, link_type_id, is_active)"
        " VALUES ($1, 'person', $2, $3, $4, $5)",
        lid,
        person_id,
        url.strip(),
        link_type_id,
        is_active == "true",
    )
    row = await _get_link_or_404(lid, person_id, db)
    if not is_htmx(request):
        return RedirectResponse(f"/admin/people/{person_id}/", status_code=303)
    return templates.TemplateResponse(
        request,
        "admin/people/partials/_link_row.html",
        {"person_id": person_id, "l": row},
        headers=flash_trigger("success", f"Link <strong>{escape(url.strip())}</strong> added."),
    )


@router.get("/{link_id}/read-row/")
async def link_read_row(
    person_id: str,
    link_id: str,
    request: Request,
    user: AdminUser = Depends(get_admin_user),
    db=Depends(get_db),
):
    """Return read-only link row (used by Cancel on edit form)."""
    row = await _get_link_or_404(link_id, person_id, db)
    return templates.TemplateResponse(
        request, "admin/people/partials/_link_row.html", {"person_id": person_id, "l": row}
    )


@router.get("/{link_id}/edit-row/")
async def link_edit_row_get(
    person_id: str,
    link_id: str,
    request: Request,
    user: AdminUser = Depends(get_admin_user),
    db=Depends(get_db),
):
    """Return link edit form row."""
    row = await _get_link_or_404(link_id, person_id, db)
    link_types = await db.fetch(
        "SELECT * FROM link_types ORDER BY is_social DESC, display_name"
    )
    return templates.TemplateResponse(
        request,
        "admin/people/partials/_link_form_row.html",
        {"person_id": person_id, "l": row, "link_types": link_types},
    )


@router.post("/{link_id}/edit-row/")
async def link_edit_row_post(
    person_id: str,
    link_id: str,
    request: Request,
    url: str = Form(...),
    link_type_id: str = Form(...),
    is_active: str = Form(""),
    user: AdminUser = Depends(get_admin_user),
    db=Depends(get_db),
):
    """Update a person link."""
    await _get_link_or_404(link_id, person_id, db)
    await db.execute(
        "UPDATE links SET url=$1, link_type_id=$2, is_active=$3 WHERE id=$4",
        url.strip(),
        link_type_id,
        is_active == "true",
        link_id,
    )
    row = await _get_link_or_404(link_id, person_id, db)
    if not is_htmx(request):
        return RedirectResponse(f"/admin/people/{person_id}/", status_code=303)
    return templates.TemplateResponse(
        request,
        "admin/people/partials/_link_row.html",
        {"person_id": person_id, "l": row},
        headers=flash_trigger("success", f"Link <strong>{escape(url.strip())}</strong> saved."),
    )


@router.delete("/{link_id}/")
async def link_delete(
    person_id: str,
    link_id: str,
    request: Request,
    user: AdminUser = Depends(get_admin_user),
    db=Depends(get_db),
):
    """Delete a person link."""
    existing = await db.fetchrow(
        "SELECT id FROM links WHERE id=$1 AND entity_type='person' AND entity_id=$2",
        link_id,
        person_id,
    )
    if not existing:
        raise HTTPException(status_code=404)
    await db.execute("DELETE FROM links WHERE id=$1", link_id)
    return HTMLResponse(content="", status_code=200, headers=flash_trigger("info", "Link removed."))
