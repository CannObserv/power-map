"""Admin CRUD for organization links."""

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from src.api.admin.deps import AdminUser, check_auth, get_admin_user, get_db, is_htmx
from src.core.db import generate_id

templates = Jinja2Templates(directory="src/templates")
router = APIRouter(prefix="/orgs/{org_id}/links", tags=["admin-org-links"])


async def _get_org_or_404(org_id: str, db):
    org = await db.fetchrow("SELECT id FROM organizations WHERE id=$1", org_id)
    if not org:
        raise HTTPException(status_code=404, detail="Organization not found")
    return org


async def _get_link_or_404(link_id: str, org_id: str, db):
    row = await db.fetchrow(
        """SELECT l.*, lt.display_name AS link_type_name, lt.is_social
           FROM links l JOIN link_types lt ON lt.id = l.link_type_id
           WHERE l.id=$1 AND l.entity_type='organization' AND l.entity_id=$2""",
        link_id,
        org_id,
    )
    if not row:
        raise HTTPException(status_code=404)
    return row


@router.get("/new-row/")
async def link_new_row(
    org_id: str,
    request: Request,
    user: AdminUser | RedirectResponse = Depends(get_admin_user),
    db=Depends(get_db),
):
    """Return empty link form row."""
    redirect, user = check_auth(user)
    if redirect:
        return redirect
    await _get_org_or_404(org_id, db)
    link_types = await db.fetch(
        "SELECT * FROM link_types ORDER BY is_social DESC, display_name"
    )
    return templates.TemplateResponse(
        request,
        "admin/orgs/partials/_link_form_row.html",
        {"org_id": org_id, "l": None, "link_types": link_types},
    )


@router.post("/")
async def link_create(
    org_id: str,
    request: Request,
    url: str = Form(...),
    link_type_id: str = Form(...),
    is_active: str = Form(""),
    is_canonical: str = Form(""),
    user: AdminUser | RedirectResponse = Depends(get_admin_user),
    db=Depends(get_db),
):
    """Create a new organization link."""
    redirect, user = check_auth(user)
    if redirect:
        return redirect
    await _get_org_or_404(org_id, db)
    lid = generate_id()
    canonical = is_canonical == "true"
    async with db.transaction():
        if canonical:
            await db.execute(
                "UPDATE links SET is_canonical=FALSE"
                " WHERE entity_type='organization' AND entity_id=$1 AND is_canonical=TRUE",
                org_id,
            )
        await db.execute(
            "INSERT INTO links"
            " (id, entity_type, entity_id, url, link_type_id, is_active, is_canonical)"
            " VALUES ($1, 'organization', $2, $3, $4, $5, $6)",
            lid,
            org_id,
            url.strip(),
            link_type_id,
            is_active == "true",
            canonical,
        )
    row = await _get_link_or_404(lid, org_id, db)
    if not is_htmx(request):
        return RedirectResponse(f"/admin/orgs/{org_id}/", status_code=303)
    return templates.TemplateResponse(
        request, "admin/orgs/partials/_link_row.html", {"org_id": org_id, "l": row}
    )


@router.get("/{link_id}/read-row/")
async def link_read_row(
    org_id: str,
    link_id: str,
    request: Request,
    user: AdminUser | RedirectResponse = Depends(get_admin_user),
    db=Depends(get_db),
):
    """Return read-only link row (used by Cancel on edit form)."""
    redirect, user = check_auth(user)
    if redirect:
        return redirect
    row = await _get_link_or_404(link_id, org_id, db)
    return templates.TemplateResponse(
        request, "admin/orgs/partials/_link_row.html", {"org_id": org_id, "l": row}
    )


@router.get("/{link_id}/edit-row/")
async def link_edit_row_get(
    org_id: str,
    link_id: str,
    request: Request,
    user: AdminUser | RedirectResponse = Depends(get_admin_user),
    db=Depends(get_db),
):
    """Return link edit form row."""
    redirect, user = check_auth(user)
    if redirect:
        return redirect
    row = await _get_link_or_404(link_id, org_id, db)
    link_types = await db.fetch(
        "SELECT * FROM link_types ORDER BY is_social DESC, display_name"
    )
    return templates.TemplateResponse(
        request,
        "admin/orgs/partials/_link_form_row.html",
        {"org_id": org_id, "l": row, "link_types": link_types},
    )


@router.post("/{link_id}/edit-row/")
async def link_edit_row_post(
    org_id: str,
    link_id: str,
    request: Request,
    url: str = Form(...),
    link_type_id: str = Form(...),
    is_active: str = Form(""),
    is_canonical: str = Form(""),
    user: AdminUser | RedirectResponse = Depends(get_admin_user),
    db=Depends(get_db),
):
    """Update an organization link."""
    redirect, user = check_auth(user)
    if redirect:
        return redirect
    await _get_link_or_404(link_id, org_id, db)
    canonical = is_canonical == "true"
    async with db.transaction():
        if canonical:
            await db.execute(
                "UPDATE links SET is_canonical=FALSE"
                " WHERE entity_type='organization' AND entity_id=$1"
                " AND is_canonical=TRUE AND id!=$2",
                org_id,
                link_id,
            )
        await db.execute(
            "UPDATE links SET url=$1, link_type_id=$2, is_active=$3, is_canonical=$4 WHERE id=$5",
            url.strip(),
            link_type_id,
            is_active == "true",
            canonical,
            link_id,
        )
    row = await _get_link_or_404(link_id, org_id, db)
    if not is_htmx(request):
        return RedirectResponse(f"/admin/orgs/{org_id}/", status_code=303)
    return templates.TemplateResponse(
        request, "admin/orgs/partials/_link_row.html", {"org_id": org_id, "l": row}
    )


@router.delete("/{link_id}/")
async def link_delete(
    org_id: str,
    link_id: str,
    request: Request,
    user: AdminUser | RedirectResponse = Depends(get_admin_user),
    db=Depends(get_db),
):
    """Delete an organization link."""
    redirect, user = check_auth(user)
    if redirect:
        return redirect
    existing = await db.fetchrow(
        "SELECT id FROM links WHERE id=$1 AND entity_type='organization' AND entity_id=$2",
        link_id,
        org_id,
    )
    if not existing:
        raise HTTPException(status_code=404)
    await db.execute("DELETE FROM links WHERE id=$1", link_id)
    return HTMLResponse(content="", status_code=200)
