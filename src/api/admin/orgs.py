"""Admin views for organizations."""

import asyncpg
from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from src.api.admin.deps import AdminUser, check_auth, get_admin_user, get_db
from src.core.db import generate_id

templates = Jinja2Templates(directory="src/templates")
router = APIRouter(prefix="/orgs", tags=["admin-orgs"])

PAGE_SIZE = 50


@router.get("/")
async def orgs_list(
    request: Request,
    q: str = "",
    status: str = "active",
    page: int = 1,
    user: AdminUser | RedirectResponse = Depends(get_admin_user),
    db=Depends(get_db),
):
    """List organizations with search and status filter."""
    redirect, user = check_auth(user)
    if redirect:
        return redirect

    offset = (page - 1) * PAGE_SIZE
    conditions = []
    params: list = []

    if status == "active":
        conditions.append("o.archived_at IS NULL AND o.active = TRUE")
    elif status == "inactive":
        conditions.append("o.archived_at IS NULL AND o.active = FALSE")
    elif status == "archived":
        conditions.append("o.archived_at IS NOT NULL")

    if q:
        params.append(f"%{q}%")
        conditions.append(f"dn.display_name ILIKE ${len(params)}")

    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
    count_params = params[:]
    list_params = params + [PAGE_SIZE, offset]

    count = await db.fetchval(
        f"""SELECT count(DISTINCT o.id)
            FROM organizations o
            LEFT JOIN v_org_display_names dn ON dn.organization_id = o.id
            {where}""",
        *count_params,
    )
    rows = await db.fetch(
        f"""SELECT o.id, o.active, o.archived_at, o.created_at,
                   dn.display_name AS canonical_name
            FROM organizations o
            LEFT JOIN v_org_display_names dn ON dn.organization_id = o.id
            {where}
            ORDER BY dn.display_name NULLS LAST
            LIMIT ${len(list_params) - 1} OFFSET ${len(list_params)}""",
        *list_params,
    )

    ctx = {
        "user": user,
        "active_section": "orgs",
        "orgs": rows,
        "q": q,
        "status": status,
        "page": page,
        "page_size": PAGE_SIZE,
        "total": count,
    }
    template = (
        "admin/orgs/_rows.html"
        if request.headers.get("HX-Request")
        else "admin/orgs/list.html"
    )
    return templates.TemplateResponse(request, template, ctx)


@router.get("/new/")
async def org_new_form(
    request: Request,
    user: AdminUser | RedirectResponse = Depends(get_admin_user),
    db=Depends(get_db),
):
    """New organization form."""
    redirect, user = check_auth(user)
    if redirect:
        return redirect
    parents = await db.fetch(
        """SELECT o.id, dn.display_name AS canonical_name
           FROM organizations o
           LEFT JOIN v_org_display_names dn ON dn.organization_id = o.id
           WHERE o.archived_at IS NULL ORDER BY dn.display_name NULLS LAST"""
    )
    return templates.TemplateResponse(
        request,
        "admin/orgs/form.html",
        {
            "user": user,
            "active_section": "orgs",
            "org": None,
            "parents": parents,
            "canonical_name": "",
        },
    )


@router.post("/new/")
async def org_create(
    request: Request,
    name: str = Form(...),
    active: str = Form(""),
    parent_id: str = Form(""),
    notes: str = Form(""),
    user: AdminUser | RedirectResponse = Depends(get_admin_user),
    db=Depends(get_db),
):
    """Create a new organization."""
    redirect, user = check_auth(user)
    if redirect:
        return redirect
    org_id = generate_id()
    await db.execute(
        "INSERT INTO organizations (id, active, parent_id, notes) VALUES ($1, $2, $3, $4)",
        org_id, active == "true", parent_id or None, notes or None,
    )
    await db.execute(
        "INSERT INTO organization_names"
        " (id, organization_id, name, is_canonical) VALUES ($1, $2, $3, TRUE)",
        generate_id(), org_id, name,
    )
    return RedirectResponse(f"/admin/orgs/{org_id}/", status_code=303)


@router.get("/{org_id}/")
async def org_detail(
    org_id: str,
    request: Request,
    user: AdminUser | RedirectResponse = Depends(get_admin_user),
    db=Depends(get_db),
):
    """Organization detail view."""
    redirect, user = check_auth(user)
    if redirect:
        return redirect

    org = await db.fetchrow("SELECT * FROM organizations WHERE id = $1", org_id)
    if not org:
        raise HTTPException(status_code=404, detail="Organization not found")

    names = await db.fetch(
        "SELECT * FROM organization_names WHERE organization_id = $1 ORDER BY is_canonical DESC",
        org_id,
    )
    addresses = await db.fetch(
        """SELECT ea.*, a.standardized, a.address_line_1, a.city, a.region, a.postal_code
           FROM entity_addresses ea JOIN addresses a ON a.id = ea.address_id
           WHERE ea.entity_type = 'organization' AND ea.entity_id = $1""",
        org_id,
    )
    contacts = await db.fetch(
        "SELECT * FROM contact_methods WHERE entity_type = 'organization' AND entity_id = $1",
        org_id,
    )
    urls = await db.fetch(
        """SELECT u.*, ut.display_name AS url_type_name
           FROM urls u JOIN url_types ut ON ut.id = u.url_type_id
           WHERE u.entity_type = 'organization' AND u.entity_id = $1""",
        org_id,
    )
    social = await db.fetch(
        """SELECT sl.*, p.display_name AS platform_name
           FROM social_links sl JOIN platforms p ON p.id = sl.platform_id
           WHERE sl.entity_type = 'organization' AND sl.entity_id = $1""",
        org_id,
    )
    identifiers = await db.fetch(
        """SELECT i.*, eit.display_name AS type_name, eit.full_name AS type_full_name
           FROM identifiers i
           JOIN entity_identifier_types eit ON eit.id = i.entity_identifier_type_id
           WHERE i.entity_id = $1""",
        org_id,
    )
    children = await db.fetch(
        """SELECT o.id, o.active, o.archived_at, dn.display_name AS canonical_name
           FROM organizations o
           LEFT JOIN v_org_display_names dn ON dn.organization_id = o.id
           WHERE o.parent_id = $1 ORDER BY dn.display_name""",
        org_id,
    )
    roles = await db.fetch(
        "SELECT * FROM roles WHERE organization_id = $1 AND archived_at IS NULL ORDER BY title",
        org_id,
    )

    return templates.TemplateResponse(
        request,
        "admin/orgs/detail.html",
        {
            "user": user,
            "active_section": "orgs",
            "org": org,
            "names": names,
            "addresses": addresses,
            "contacts": contacts,
            "urls": urls,
            "social": social,
            "identifiers": identifiers,
            "children": children,
            "roles": roles,
        },
    )


@router.get("/{org_id}/edit/")
async def org_edit_form(
    org_id: str,
    request: Request,
    user: AdminUser | RedirectResponse = Depends(get_admin_user),
    db=Depends(get_db),
):
    """Edit organization form."""
    redirect, user = check_auth(user)
    if redirect:
        return redirect
    org = await db.fetchrow("SELECT * FROM organizations WHERE id = $1", org_id)
    if not org:
        raise HTTPException(status_code=404, detail="Organization not found")
    canonical = await db.fetchrow(
        "SELECT name FROM organization_names WHERE organization_id = $1 AND is_canonical = TRUE",
        org_id,
    )
    parents = await db.fetch(
        """SELECT o.id, dn.display_name AS canonical_name
           FROM organizations o
           LEFT JOIN v_org_display_names dn ON dn.organization_id = o.id
           WHERE o.archived_at IS NULL AND o.id != $1 ORDER BY dn.display_name NULLS LAST""",
        org_id,
    )
    return templates.TemplateResponse(
        request,
        "admin/orgs/form.html",
        {
            "user": user,
            "active_section": "orgs",
            "org": org,
            "canonical_name": canonical["name"] if canonical else "",
            "parents": parents,
        },
    )


@router.post("/{org_id}/edit/")
async def org_update(
    org_id: str,
    request: Request,
    name: str = Form(...),
    active: str = Form(""),
    parent_id: str = Form(""),
    notes: str = Form(""),
    user: AdminUser | RedirectResponse = Depends(get_admin_user),
    db=Depends(get_db),
):
    """Update an organization."""
    redirect, user = check_auth(user)
    if redirect:
        return redirect
    org = await db.fetchrow("SELECT id FROM organizations WHERE id = $1", org_id)
    if not org:
        raise HTTPException(status_code=404, detail="Organization not found")
    await db.execute(
        "UPDATE organizations SET active = $1, parent_id = $2, notes = $3 WHERE id = $4",
        active == "true", parent_id or None, notes or None, org_id,
    )
    existing = await db.fetchrow(
        "SELECT id FROM organization_names WHERE organization_id = $1 AND is_canonical = TRUE",
        org_id,
    )
    if existing:
        await db.execute(
            "UPDATE organization_names SET name = $1 WHERE id = $2", name, existing["id"]
        )
    else:
        await db.execute(
            "INSERT INTO organization_names"
            " (id, organization_id, name, is_canonical) VALUES ($1, $2, $3, TRUE)",
            generate_id(), org_id, name,
        )
    return RedirectResponse(f"/admin/orgs/{org_id}/", status_code=303)


@router.post("/{org_id}/archive/")
async def org_archive(
    org_id: str,
    request: Request,
    user: AdminUser | RedirectResponse = Depends(get_admin_user),
    db=Depends(get_db),
):
    """Archive an organization (soft delete)."""
    redirect, user = check_auth(user)
    if redirect:
        return redirect
    org = await db.fetchrow("SELECT id FROM organizations WHERE id = $1", org_id)
    if not org:
        raise HTTPException(status_code=404, detail="Organization not found")
    await db.execute("UPDATE organizations SET archived_at = NOW() WHERE id = $1", org_id)
    return RedirectResponse(f"/admin/orgs/{org_id}/", status_code=303)


@router.delete("/{org_id}/")
async def org_delete(
    org_id: str,
    request: Request,
    user: AdminUser | RedirectResponse = Depends(get_admin_user),
    db=Depends(get_db),
):
    """Hard delete an archived organization."""
    redirect, user = check_auth(user)
    if redirect:
        return redirect
    org = await db.fetchrow("SELECT id, archived_at FROM organizations WHERE id = $1", org_id)
    if not org:
        raise HTTPException(status_code=404, detail="Organization not found")
    if not org["archived_at"]:
        raise HTTPException(status_code=409, detail="Organization must be archived before deletion")
    try:
        await db.execute("DELETE FROM organization_names WHERE organization_id = $1", org_id)
        await db.execute("DELETE FROM organizations WHERE id = $1", org_id)
    except asyncpg.ForeignKeyViolationError:
        raise HTTPException(
            status_code=409,
            detail="Cannot delete: organization has related records (roles, etc.)",
        )
    return HTMLResponse(content="", status_code=200)
