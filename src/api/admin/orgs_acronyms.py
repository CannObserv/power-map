"""Admin CRUD for organization acronyms."""

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from src.api.admin.deps import AdminUser, check_auth, get_admin_user, get_db, is_htmx
from src.core.db import generate_id

templates = Jinja2Templates(directory="src/templates")
router = APIRouter(prefix="/orgs/{org_id}/acronyms", tags=["admin-org-acronyms"])


async def _get_org_or_404(org_id: str, db):
    org = await db.fetchrow("SELECT id FROM organizations WHERE id=$1", org_id)
    if not org:
        raise HTTPException(status_code=404, detail="Organization not found")
    return org


@router.get("/new-row/")
async def acronym_new_row(
    org_id: str,
    request: Request,
    user: AdminUser | RedirectResponse = Depends(get_admin_user),
    db=Depends(get_db),
):
    """Return empty acronym form row."""
    redirect, user = check_auth(user)
    if redirect:
        return redirect
    await _get_org_or_404(org_id, db)
    return templates.TemplateResponse(
        request,
        "admin/orgs/partials/_acronym_form_row.html",
        {"org_id": org_id, "a": None},
    )


@router.post("/")
async def acronym_create(
    org_id: str,
    request: Request,
    acronym: str = Form(...),
    is_canonical: str = Form(""),
    user: AdminUser | RedirectResponse = Depends(get_admin_user),
    db=Depends(get_db),
):
    """Create a new organization acronym."""
    redirect, user = check_auth(user)
    if redirect:
        return redirect
    await _get_org_or_404(org_id, db)
    aid = generate_id()
    await db.execute(
        "INSERT INTO organization_acronyms (id, organization_id, acronym, is_canonical)"
        " VALUES ($1, $2, $3, $4)",
        aid,
        org_id,
        acronym.strip(),
        is_canonical == "true",
    )
    row = await db.fetchrow("SELECT * FROM organization_acronyms WHERE id=$1", aid)
    if not is_htmx(request):
        return RedirectResponse(f"/admin/orgs/{org_id}/", status_code=303)
    return templates.TemplateResponse(
        request, "admin/orgs/partials/_acronym_row.html", {"org_id": org_id, "a": row}
    )


@router.get("/{acronym_id}/read-row/")
async def acronym_read_row(
    org_id: str,
    acronym_id: str,
    request: Request,
    user: AdminUser | RedirectResponse = Depends(get_admin_user),
    db=Depends(get_db),
):
    """Return read-only acronym row (used by Cancel on edit form)."""
    redirect, user = check_auth(user)
    if redirect:
        return redirect
    row = await db.fetchrow(
        "SELECT * FROM organization_acronyms WHERE id=$1 AND organization_id=$2",
        acronym_id,
        org_id,
    )
    if not row:
        raise HTTPException(status_code=404)
    return templates.TemplateResponse(
        request,
        "admin/orgs/partials/_acronym_row.html",
        {"org_id": org_id, "a": row},
    )


@router.get("/{acronym_id}/edit-row/")
async def acronym_edit_row_get(
    org_id: str,
    acronym_id: str,
    request: Request,
    user: AdminUser | RedirectResponse = Depends(get_admin_user),
    db=Depends(get_db),
):
    """Return acronym edit form row."""
    redirect, user = check_auth(user)
    if redirect:
        return redirect
    row = await db.fetchrow(
        "SELECT * FROM organization_acronyms WHERE id=$1 AND organization_id=$2",
        acronym_id,
        org_id,
    )
    if not row:
        raise HTTPException(status_code=404)
    return templates.TemplateResponse(
        request,
        "admin/orgs/partials/_acronym_form_row.html",
        {"org_id": org_id, "a": row},
    )


@router.post("/{acronym_id}/edit-row/")
async def acronym_edit_row_post(
    org_id: str,
    acronym_id: str,
    request: Request,
    acronym: str = Form(...),
    is_canonical: str = Form(""),
    user: AdminUser | RedirectResponse = Depends(get_admin_user),
    db=Depends(get_db),
):
    """Update an organization acronym."""
    redirect, user = check_auth(user)
    if redirect:
        return redirect
    existing = await db.fetchrow(
        "SELECT * FROM organization_acronyms WHERE id=$1 AND organization_id=$2",
        acronym_id,
        org_id,
    )
    if not existing:
        raise HTTPException(status_code=404)
    await db.execute(
        "UPDATE organization_acronyms SET acronym=$1, is_canonical=$2 WHERE id=$3",
        acronym.strip(),
        is_canonical == "true",
        acronym_id,
    )
    row = await db.fetchrow(
        "SELECT * FROM organization_acronyms WHERE id=$1", acronym_id
    )
    if not is_htmx(request):
        return RedirectResponse(f"/admin/orgs/{org_id}/", status_code=303)
    return templates.TemplateResponse(
        request,
        "admin/orgs/partials/_acronym_row.html",
        {"org_id": org_id, "a": row},
    )


@router.delete("/{acronym_id}/")
async def acronym_delete(
    org_id: str,
    acronym_id: str,
    request: Request,
    user: AdminUser | RedirectResponse = Depends(get_admin_user),
    db=Depends(get_db),
):
    """Delete an organization acronym."""
    redirect, user = check_auth(user)
    if redirect:
        return redirect
    existing = await db.fetchrow(
        "SELECT id FROM organization_acronyms WHERE id=$1 AND organization_id=$2",
        acronym_id,
        org_id,
    )
    if not existing:
        raise HTTPException(status_code=404)
    await db.execute("DELETE FROM organization_acronyms WHERE id=$1", acronym_id)
    return HTMLResponse(content="", status_code=200)
