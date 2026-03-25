"""Admin CRUD for organization contact methods."""

from typing import Literal

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from markupsafe import escape

from src.api.admin.deps import AdminUser, check_auth, flash_trigger, get_admin_user, get_db, is_htmx
from src.core.db import generate_id

templates = Jinja2Templates(directory="src/templates")
router = APIRouter(prefix="/orgs/{org_id}/contacts", tags=["admin-org-contacts"])


async def _get_org_or_404(org_id: str, db):
    org = await db.fetchrow("SELECT id FROM organizations WHERE id=$1", org_id)
    if not org:
        raise HTTPException(status_code=404, detail="Organization not found")
    return org


@router.get("/new-row/")
async def contact_new_row(
    org_id: str,
    request: Request,
    contact_type: Literal["email", "phone"] = Query(...),
    user: AdminUser | RedirectResponse = Depends(get_admin_user),
    db=Depends(get_db),
):
    """Return empty contact form row for the given contact_type (email|phone)."""
    redirect, user = check_auth(user)
    if redirect:
        return redirect
    await _get_org_or_404(org_id, db)
    return templates.TemplateResponse(
        request,
        "admin/orgs/partials/_contact_form_row.html",
        {"org_id": org_id, "c": None, "contact_type": contact_type},
    )


@router.post("/")
async def contact_create(
    org_id: str,
    request: Request,
    contact_type: str = Form(...),
    value: str = Form(...),
    display_label: str = Form(""),
    user: AdminUser | RedirectResponse = Depends(get_admin_user),
    db=Depends(get_db),
):
    """Create a new organization contact method."""
    redirect, user = check_auth(user)
    if redirect:
        return redirect
    await _get_org_or_404(org_id, db)
    cid = generate_id()
    await db.execute(
        "INSERT INTO contact_methods"
        " (id, entity_type, entity_id, contact_type, value, display_label)"
        " VALUES ($1, 'organization', $2, $3, $4, $5)",
        cid,
        org_id,
        contact_type,
        value.strip(),
        display_label.strip() or None,
    )
    row = await db.fetchrow("SELECT * FROM contact_methods WHERE id=$1", cid)
    if not is_htmx(request):
        return RedirectResponse(f"/admin/orgs/{org_id}/", status_code=303)
    return templates.TemplateResponse(
        request,
        "admin/orgs/partials/_contact_row.html",
        {"org_id": org_id, "c": row},
        headers=flash_trigger("success", f"<strong>{escape(value.strip())}</strong> added."),
    )


@router.get("/{contact_id}/read-row/")
async def contact_read_row(
    org_id: str,
    contact_id: str,
    request: Request,
    user: AdminUser | RedirectResponse = Depends(get_admin_user),
    db=Depends(get_db),
):
    """Return read-only contact row (used by Cancel on edit form)."""
    redirect, user = check_auth(user)
    if redirect:
        return redirect
    contact = await db.fetchrow(
        "SELECT * FROM contact_methods"
        " WHERE id=$1 AND entity_type='organization' AND entity_id=$2",
        contact_id,
        org_id,
    )
    if not contact:
        raise HTTPException(status_code=404)
    return templates.TemplateResponse(
        request, "admin/orgs/partials/_contact_row.html", {"org_id": org_id, "c": contact}
    )


@router.get("/{contact_id}/edit-row/")
async def contact_edit_row_get(
    org_id: str,
    contact_id: str,
    request: Request,
    user: AdminUser | RedirectResponse = Depends(get_admin_user),
    db=Depends(get_db),
):
    """Return contact edit form row."""
    redirect, user = check_auth(user)
    if redirect:
        return redirect
    contact = await db.fetchrow(
        "SELECT * FROM contact_methods"
        " WHERE id=$1 AND entity_type='organization' AND entity_id=$2",
        contact_id,
        org_id,
    )
    if not contact:
        raise HTTPException(status_code=404)
    return templates.TemplateResponse(
        request,
        "admin/orgs/partials/_contact_form_row.html",
        {"org_id": org_id, "c": contact, "contact_type": contact["contact_type"]},
    )


@router.post("/{contact_id}/edit-row/")
async def contact_edit_row_post(
    org_id: str,
    contact_id: str,
    request: Request,
    value: str = Form(...),
    display_label: str = Form(""),
    user: AdminUser | RedirectResponse = Depends(get_admin_user),
    db=Depends(get_db),
):
    """Update an organization contact method (contact_type is immutable)."""
    redirect, user = check_auth(user)
    if redirect:
        return redirect
    existing = await db.fetchrow(
        "SELECT * FROM contact_methods"
        " WHERE id=$1 AND entity_type='organization' AND entity_id=$2",
        contact_id,
        org_id,
    )
    if not existing:
        raise HTTPException(status_code=404)
    await db.execute(
        "UPDATE contact_methods SET value=$1, display_label=$2 WHERE id=$3",
        value.strip(),
        display_label.strip() or None,
        contact_id,
    )
    row = await db.fetchrow("SELECT * FROM contact_methods WHERE id=$1", contact_id)
    if not is_htmx(request):
        return RedirectResponse(f"/admin/orgs/{org_id}/", status_code=303)
    return templates.TemplateResponse(
        request,
        "admin/orgs/partials/_contact_row.html",
        {"org_id": org_id, "c": row},
        headers=flash_trigger("success", f"<strong>{escape(value.strip())}</strong> saved."),
    )


@router.delete("/{contact_id}/")
async def contact_delete(
    org_id: str,
    contact_id: str,
    request: Request,
    user: AdminUser | RedirectResponse = Depends(get_admin_user),
    db=Depends(get_db),
):
    """Delete an organization contact method."""
    redirect, user = check_auth(user)
    if redirect:
        return redirect
    existing = await db.fetchrow(
        "SELECT id FROM contact_methods"
        " WHERE id=$1 AND entity_type='organization' AND entity_id=$2",
        contact_id,
        org_id,
    )
    if not existing:
        raise HTTPException(status_code=404)
    await db.execute("DELETE FROM contact_methods WHERE id=$1", contact_id)
    return HTMLResponse(
        content="",
        status_code=200,
        headers=flash_trigger("info", "Contact removed."),
    )
