"""Admin views for roles."""

import asyncpg
from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from src.api.admin.deps import AdminUser, check_auth, get_admin_user, get_db
from src.core.db import generate_id

templates = Jinja2Templates(directory="src/templates")
router = APIRouter(prefix="/roles", tags=["admin-roles"])

PAGE_SIZE = 50


@router.get("/")
async def roles_list(
    request: Request,
    q: str = "",
    status: str = "active",
    page: int = 1,
    user: AdminUser | RedirectResponse = Depends(get_admin_user),
    db=Depends(get_db),
):
    """List roles with search and status filter."""
    redirect, user = check_auth(user)
    if redirect:
        return redirect

    offset = (page - 1) * PAGE_SIZE
    conditions = []
    params: list = []

    if status == "active":
        conditions.append("r.archived_at IS NULL")
    elif status == "archived":
        conditions.append("r.archived_at IS NOT NULL")

    if q:
        params.append(f"%{q}%")
        conditions.append(f"r.title ILIKE ${len(params)}")

    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
    count_params = params[:]
    list_params = params + [PAGE_SIZE, offset]

    count = await db.fetchval(
        f"""SELECT count(DISTINCT r.id)
            FROM roles r
            JOIN organizations o ON o.id = r.organization_id
            LEFT JOIN organization_names n
              ON n.organization_id = o.id AND n.is_canonical = TRUE
            {where}""",
        *count_params,
    )
    rows = await db.fetch(
        f"""SELECT r.id, r.title, r.notes, r.archived_at, r.created_at,
                   o.id AS org_id,
                   n.name AS org_name
            FROM roles r
            JOIN organizations o ON o.id = r.organization_id
            LEFT JOIN organization_names n
              ON n.organization_id = o.id AND n.is_canonical = TRUE
            {where}
            ORDER BY n.name NULLS LAST, r.title
            LIMIT ${len(list_params) - 1} OFFSET ${len(list_params)}""",
        *list_params,
    )

    return templates.TemplateResponse(
        request,
        "admin/roles/list.html",
        {
            "user": user,
            "active_section": "roles",
            "roles": rows,
            "q": q,
            "status": status,
            "page": page,
            "page_size": PAGE_SIZE,
            "total": count,
        },
    )


@router.get("/new/")
async def role_new_form(
    request: Request,
    user: AdminUser | RedirectResponse = Depends(get_admin_user),
    db=Depends(get_db),
):
    """New role form."""
    redirect, user = check_auth(user)
    if redirect:
        return redirect
    orgs = await db.fetch(
        """SELECT o.id, n.name
           FROM organizations o
           LEFT JOIN organization_names n
             ON n.organization_id = o.id AND n.is_canonical = TRUE
           WHERE o.archived_at IS NULL ORDER BY n.name NULLS LAST"""
    )
    return templates.TemplateResponse(
        request,
        "admin/roles/form.html",
        {
            "user": user,
            "active_section": "roles",
            "role": None,
            "orgs": orgs,
        },
    )


@router.post("/new/")
async def role_create(
    request: Request,
    organization_id: str = Form(...),
    title: str = Form(...),
    notes: str = Form(""),
    user: AdminUser | RedirectResponse = Depends(get_admin_user),
    db=Depends(get_db),
):
    """Create a new role."""
    redirect, user = check_auth(user)
    if redirect:
        return redirect
    role_id = generate_id()
    await db.execute(
        "INSERT INTO roles (id, organization_id, title, notes) VALUES ($1, $2, $3, $4)",
        role_id, organization_id, title, notes or None,
    )
    return RedirectResponse(f"/admin/roles/{role_id}/", status_code=303)


@router.get("/{role_id}/")
async def role_detail(
    role_id: str,
    request: Request,
    user: AdminUser | RedirectResponse = Depends(get_admin_user),
    db=Depends(get_db),
):
    """Role detail view."""
    redirect, user = check_auth(user)
    if redirect:
        return redirect

    role = await db.fetchrow(
        """SELECT r.id, r.title, r.notes, r.archived_at, r.created_at, r.updated_at,
                  o.id AS org_id, n.name AS org_name
           FROM roles r
           JOIN organizations o ON o.id = r.organization_id
           LEFT JOIN organization_names n
             ON n.organization_id = o.id AND n.is_canonical = TRUE
           WHERE r.id = $1""",
        role_id,
    )
    if not role:
        raise HTTPException(status_code=404, detail="Role not found")

    assignments = await db.fetch(
        """SELECT ra.id, ra.is_current, ra.start_date, ra.end_date, ra.archived_at,
                  p.id AS person_id,
                  pn.name AS person_name
           FROM role_assignments ra
           JOIN people p ON p.id = ra.person_id
           LEFT JOIN person_names pn ON pn.person_id = p.id AND pn.is_canonical = TRUE
           WHERE ra.role_id = $1
           ORDER BY ra.is_current DESC, ra.start_date DESC NULLS LAST""",
        role_id,
    )

    return templates.TemplateResponse(
        request,
        "admin/roles/detail.html",
        {
            "user": user,
            "active_section": "roles",
            "role": role,
            "assignments": assignments,
        },
    )


@router.get("/{role_id}/edit/")
async def role_edit_form(
    role_id: str,
    request: Request,
    user: AdminUser | RedirectResponse = Depends(get_admin_user),
    db=Depends(get_db),
):
    """Edit role form."""
    redirect, user = check_auth(user)
    if redirect:
        return redirect
    role = await db.fetchrow("SELECT * FROM roles WHERE id = $1", role_id)
    if not role:
        raise HTTPException(status_code=404, detail="Role not found")
    orgs = await db.fetch(
        """SELECT o.id, n.name
           FROM organizations o
           LEFT JOIN organization_names n
             ON n.organization_id = o.id AND n.is_canonical = TRUE
           WHERE o.archived_at IS NULL ORDER BY n.name NULLS LAST"""
    )
    return templates.TemplateResponse(
        request,
        "admin/roles/form.html",
        {
            "user": user,
            "active_section": "roles",
            "role": role,
            "orgs": orgs,
        },
    )


@router.post("/{role_id}/edit/")
async def role_update(
    role_id: str,
    request: Request,
    organization_id: str = Form(...),
    title: str = Form(...),
    notes: str = Form(""),
    user: AdminUser | RedirectResponse = Depends(get_admin_user),
    db=Depends(get_db),
):
    """Update a role."""
    redirect, user = check_auth(user)
    if redirect:
        return redirect
    role = await db.fetchrow("SELECT id FROM roles WHERE id = $1", role_id)
    if not role:
        raise HTTPException(status_code=404, detail="Role not found")
    await db.execute(
        "UPDATE roles SET organization_id = $1, title = $2, notes = $3 WHERE id = $4",
        organization_id, title, notes or None, role_id,
    )
    return RedirectResponse(f"/admin/roles/{role_id}/", status_code=303)


@router.post("/{role_id}/archive/")
async def role_archive(
    role_id: str,
    request: Request,
    user: AdminUser | RedirectResponse = Depends(get_admin_user),
    db=Depends(get_db),
):
    """Archive a role (soft delete)."""
    redirect, user = check_auth(user)
    if redirect:
        return redirect
    role = await db.fetchrow("SELECT id FROM roles WHERE id = $1", role_id)
    if not role:
        raise HTTPException(status_code=404, detail="Role not found")
    await db.execute("UPDATE roles SET archived_at = NOW() WHERE id = $1", role_id)
    return RedirectResponse(f"/admin/roles/{role_id}/", status_code=303)


@router.delete("/{role_id}/")
async def role_delete(
    role_id: str,
    request: Request,
    user: AdminUser | RedirectResponse = Depends(get_admin_user),
    db=Depends(get_db),
):
    """Hard delete an archived role."""
    redirect, user = check_auth(user)
    if redirect:
        return redirect
    role = await db.fetchrow("SELECT id, archived_at FROM roles WHERE id = $1", role_id)
    if not role:
        raise HTTPException(status_code=404, detail="Role not found")
    if not role["archived_at"]:
        raise HTTPException(status_code=409, detail="Role must be archived before deletion")
    try:
        await db.execute("DELETE FROM roles WHERE id = $1", role_id)
    except asyncpg.ForeignKeyViolationError:
        raise HTTPException(status_code=409, detail="Cannot delete role with existing assignments")
    return HTMLResponse(content="", status_code=200)
