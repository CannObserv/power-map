"""Admin CRUD for organization identifiers."""

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from src.api.admin.deps import AdminUser, check_auth, get_admin_user, get_db
from src.core.db import generate_id

templates = Jinja2Templates(directory="src/templates")
router = APIRouter(prefix="/orgs/{org_id}/identifiers", tags=["admin-org-identifiers"])


def _is_htmx(request: Request) -> bool:
    return bool(request.headers.get("HX-Request") and not request.headers.get("HX-Boosted"))


async def _get_org_or_404(org_id: str, db):
    org = await db.fetchrow("SELECT id FROM organizations WHERE id=$1", org_id)
    if not org:
        raise HTTPException(status_code=404, detail="Organization not found")
    return org


async def _get_identifier_or_404(ident_id: str, org_id: str, db):
    row = await db.fetchrow(
        """SELECT i.*, eit.display_name AS type_name, eit.full_name AS type_full_name
           FROM identifiers i
           JOIN entity_identifier_types eit ON eit.id = i.entity_identifier_type_id
           WHERE i.id=$1 AND i.entity_id=$2 AND eit.entity_type='organization'""",
        ident_id,
        org_id,
    )
    if not row:
        raise HTTPException(status_code=404)
    return row


@router.get("/new-row/")
async def identifier_new_row(
    org_id: str,
    request: Request,
    user: AdminUser | RedirectResponse = Depends(get_admin_user),
    db=Depends(get_db),
):
    """Return empty identifier form row."""
    redirect, user = check_auth(user)
    if redirect:
        return redirect
    await _get_org_or_404(org_id, db)
    ident_types = await db.fetch(
        "SELECT * FROM entity_identifier_types"
        " WHERE entity_type='organization' ORDER BY display_name"
    )
    return templates.TemplateResponse(
        request,
        "admin/orgs/partials/_identifier_form_row.html",
        {"org_id": org_id, "ident": None, "ident_types": ident_types},
    )


@router.post("/")
async def identifier_create(
    org_id: str,
    request: Request,
    entity_identifier_type_id: str = Form(...),
    value: str = Form(...),
    user: AdminUser | RedirectResponse = Depends(get_admin_user),
    db=Depends(get_db),
):
    """Create a new organization identifier."""
    redirect, user = check_auth(user)
    if redirect:
        return redirect
    await _get_org_or_404(org_id, db)
    iid = generate_id()
    await db.execute(
        "INSERT INTO identifiers (id, entity_id, entity_identifier_type_id, value)"
        " VALUES ($1, $2, $3, $4)",
        iid,
        org_id,
        entity_identifier_type_id,
        value.strip(),
    )
    row = await _get_identifier_or_404(iid, org_id, db)
    if not _is_htmx(request):
        return RedirectResponse(f"/admin/orgs/{org_id}/", status_code=303)
    return templates.TemplateResponse(
        request, "admin/orgs/partials/_identifier_row.html", {"org_id": org_id, "ident": row}
    )


@router.get("/{ident_id}/edit-row/")
async def identifier_edit_row_get(
    org_id: str,
    ident_id: str,
    request: Request,
    user: AdminUser | RedirectResponse = Depends(get_admin_user),
    db=Depends(get_db),
):
    """Return identifier edit form row."""
    redirect, user = check_auth(user)
    if redirect:
        return redirect
    row = await _get_identifier_or_404(ident_id, org_id, db)
    ident_types = await db.fetch(
        "SELECT * FROM entity_identifier_types"
        " WHERE entity_type='organization' ORDER BY display_name"
    )
    return templates.TemplateResponse(
        request,
        "admin/orgs/partials/_identifier_form_row.html",
        {"org_id": org_id, "ident": row, "ident_types": ident_types},
    )


@router.post("/{ident_id}/edit-row/")
async def identifier_edit_row_post(
    org_id: str,
    ident_id: str,
    request: Request,
    entity_identifier_type_id: str = Form(...),
    value: str = Form(...),
    user: AdminUser | RedirectResponse = Depends(get_admin_user),
    db=Depends(get_db),
):
    """Update an organization identifier."""
    redirect, user = check_auth(user)
    if redirect:
        return redirect
    await _get_identifier_or_404(ident_id, org_id, db)
    await db.execute(
        "UPDATE identifiers SET entity_identifier_type_id=$1, value=$2 WHERE id=$3",
        entity_identifier_type_id,
        value.strip(),
        ident_id,
    )
    row = await _get_identifier_or_404(ident_id, org_id, db)
    if not _is_htmx(request):
        return RedirectResponse(f"/admin/orgs/{org_id}/", status_code=303)
    return templates.TemplateResponse(
        request, "admin/orgs/partials/_identifier_row.html", {"org_id": org_id, "ident": row}
    )


@router.delete("/{ident_id}/")
async def identifier_delete(
    org_id: str,
    ident_id: str,
    request: Request,
    user: AdminUser | RedirectResponse = Depends(get_admin_user),
    db=Depends(get_db),
):
    """Delete an organization identifier."""
    redirect, user = check_auth(user)
    if redirect:
        return redirect
    existing = await db.fetchrow(
        """SELECT i.id FROM identifiers i
           JOIN entity_identifier_types eit ON eit.id = i.entity_identifier_type_id
           WHERE i.id=$1 AND i.entity_id=$2 AND eit.entity_type='organization'""",
        ident_id,
        org_id,
    )
    if not existing:
        raise HTTPException(status_code=404)
    await db.execute("DELETE FROM identifiers WHERE id=$1", ident_id)
    return HTMLResponse(content="", status_code=200)
