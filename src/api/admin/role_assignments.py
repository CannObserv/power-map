"""Admin views for role assignments."""

import datetime

import asyncpg
from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from src.api.admin.deps import AdminUser, check_auth, get_admin_user, get_db
from src.core.db import generate_id

templates = Jinja2Templates(directory="src/templates")
router = APIRouter(prefix="/role-assignments", tags=["admin-role-assignments"])

PAGE_SIZE = 50


def _parse_date(value: str) -> datetime.date | None:
    """Parse an ISO date string to datetime.date, or return None if empty."""
    if not value:
        return None
    return datetime.date.fromisoformat(value)


async def _fetch_people(db):
    """Fetch active people for select options."""
    return await db.fetch(
        """SELECT p.id, pn.name
           FROM people p
           LEFT JOIN person_names pn ON pn.person_id = p.id AND pn.is_canonical = TRUE
           WHERE p.archived_at IS NULL
           ORDER BY pn.name NULLS LAST"""
    )


async def _fetch_roles(db):
    """Fetch active roles for select options."""
    return await db.fetch(
        """SELECT r.id, r.title, dn.display_name AS org_name
           FROM roles r
           JOIN organizations o ON o.id = r.organization_id
           LEFT JOIN v_org_display_names dn ON dn.organization_id = o.id
           WHERE r.archived_at IS NULL
           ORDER BY dn.display_name NULLS LAST, r.title"""
    )


_LIST_SELECT = """
    SELECT ra.id, ra.is_current, ra.start_date, ra.end_date, ra.archived_at, ra.created_at,
           p.id AS person_id,
           pn.name AS person_name,
           r.id AS role_id, r.title AS role_title,
           o.id AS org_id,
           dn.display_name AS org_name
    FROM role_assignments ra
    JOIN people p ON p.id = ra.person_id
    LEFT JOIN person_names pn ON pn.person_id = p.id AND pn.is_canonical = TRUE
    JOIN roles r ON r.id = ra.role_id
    JOIN organizations o ON o.id = r.organization_id
    LEFT JOIN v_org_display_names dn ON dn.organization_id = o.id
"""

_LIST_ORDER = "ORDER BY ra.is_current DESC, pn.name NULLS LAST, ra.start_date DESC NULLS LAST"


@router.get("/")
async def ra_list(
    request: Request,
    q: str = "",
    status: str = "active",
    page: int = 1,
    user: AdminUser | RedirectResponse = Depends(get_admin_user),
    db=Depends(get_db),
):
    """List role assignments with search and status filter."""
    redirect, user = check_auth(user)
    if redirect:
        return redirect

    offset = (page - 1) * PAGE_SIZE
    conditions = []
    params: list = []

    if status == "active":
        conditions.append("ra.archived_at IS NULL")
    elif status == "archived":
        conditions.append("ra.archived_at IS NOT NULL")

    if q:
        params.append(f"%{q}%")
        idx = len(params)
        conditions.append(
            f"(pn.name ILIKE ${idx} OR r.title ILIKE ${idx} OR dn.display_name ILIKE ${idx})"
        )

    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
    count_params = params[:]
    list_params = params + [PAGE_SIZE, offset]

    count = await db.fetchval(
        f"""SELECT count(DISTINCT ra.id)
            FROM role_assignments ra
            JOIN people p ON p.id = ra.person_id
            LEFT JOIN person_names pn ON pn.person_id = p.id AND pn.is_canonical = TRUE
            JOIN roles r ON r.id = ra.role_id
            JOIN organizations o ON o.id = r.organization_id
            LEFT JOIN v_org_display_names dn ON dn.organization_id = o.id
            {where}""",
        *count_params,
    )
    rows = await db.fetch(
        f"""{_LIST_SELECT}
            {where}
            {_LIST_ORDER}
            LIMIT ${len(list_params) - 1} OFFSET ${len(list_params)}""",
        *list_params,
    )

    ctx = {
        "user": user,
        "active_section": "role_assignments",
        "assignments": rows,
        "q": q,
        "status": status,
        "page": page,
        "page_size": PAGE_SIZE,
        "total": count,
    }
    template = (
        "admin/role_assignments/_rows.html"
        if request.headers.get("HX-Request")
        else "admin/role_assignments/list.html"
    )
    return templates.TemplateResponse(request, template, ctx)


@router.get("/new/")
async def ra_new_form(
    request: Request,
    user: AdminUser | RedirectResponse = Depends(get_admin_user),
    db=Depends(get_db),
):
    """New role assignment form."""
    redirect, user = check_auth(user)
    if redirect:
        return redirect
    people = await _fetch_people(db)
    roles = await _fetch_roles(db)
    return templates.TemplateResponse(
        request,
        "admin/role_assignments/form.html",
        {
            "user": user,
            "active_section": "role_assignments",
            "ra": None,
            "people": people,
            "roles": roles,
            "error": None,
        },
    )


@router.post("/new/")
async def ra_create(
    request: Request,
    person_id: str = Form(...),
    role_id: str = Form(...),
    is_current: str = Form(""),
    start_date: str = Form(""),
    end_date: str = Form(""),
    notes: str = Form(""),
    user: AdminUser | RedirectResponse = Depends(get_admin_user),
    db=Depends(get_db),
):
    """Create a new role assignment."""
    redirect, user = check_auth(user)
    if redirect:
        return redirect

    is_current_bool = is_current == "true"
    start_date_val = _parse_date(start_date)
    end_date_val = _parse_date(end_date)
    ra_id = generate_id()

    try:
        await db.execute(
            """INSERT INTO role_assignments
               (id, person_id, role_id, is_current, start_date, end_date, notes)
               VALUES ($1, $2, $3, $4, $5, $6, $7)""",
            ra_id, person_id, role_id, is_current_bool,
            start_date_val, end_date_val, notes or None,
        )
    except asyncpg.exceptions.CheckViolationError:
        people = await _fetch_people(db)
        roles = await _fetch_roles(db)
        return templates.TemplateResponse(
            request,
            "admin/role_assignments/form.html",
            {
                "user": user,
                "active_section": "role_assignments",
                "ra": None,
                "people": people,
                "roles": roles,
                "error": "Current assignments cannot have an end date.",
                "form_person_id": person_id,
                "form_role_id": role_id,
                "form_is_current": is_current_bool,
                "form_start_date": start_date,
                "form_end_date": end_date,
                "form_notes": notes,
            },
            status_code=200,
        )

    return RedirectResponse(f"/admin/role-assignments/{ra_id}/", status_code=303)


@router.get("/{ra_id}/")
async def ra_detail(
    ra_id: str,
    request: Request,
    user: AdminUser | RedirectResponse = Depends(get_admin_user),
    db=Depends(get_db),
):
    """Role assignment detail view."""
    redirect, user = check_auth(user)
    if redirect:
        return redirect

    ra = await db.fetchrow(
        """SELECT ra.id, ra.is_current, ra.start_date, ra.end_date, ra.archived_at,
                  ra.created_at, ra.notes,
                  p.id AS person_id,
                  pn.name AS person_name,
                  r.id AS role_id, r.title AS role_title,
                  o.id AS org_id,
                  dn.display_name AS org_name
           FROM role_assignments ra
           JOIN people p ON p.id = ra.person_id
           LEFT JOIN person_names pn ON pn.person_id = p.id AND pn.is_canonical = TRUE
           JOIN roles r ON r.id = ra.role_id
           JOIN organizations o ON o.id = r.organization_id
           LEFT JOIN v_org_display_names dn ON dn.organization_id = o.id
           WHERE ra.id = $1""",
        ra_id,
    )
    if not ra:
        raise HTTPException(status_code=404, detail="Role assignment not found")

    return templates.TemplateResponse(
        request,
        "admin/role_assignments/detail.html",
        {
            "user": user,
            "active_section": "role_assignments",
            "ra": ra,
        },
    )


@router.get("/{ra_id}/edit/")
async def ra_edit_form(
    ra_id: str,
    request: Request,
    user: AdminUser | RedirectResponse = Depends(get_admin_user),
    db=Depends(get_db),
):
    """Edit role assignment form."""
    redirect, user = check_auth(user)
    if redirect:
        return redirect

    ra = await db.fetchrow(
        "SELECT * FROM role_assignments WHERE id = $1",
        ra_id,
    )
    if not ra:
        raise HTTPException(status_code=404, detail="Role assignment not found")

    people = await _fetch_people(db)
    roles = await _fetch_roles(db)
    return templates.TemplateResponse(
        request,
        "admin/role_assignments/form.html",
        {
            "user": user,
            "active_section": "role_assignments",
            "ra": ra,
            "people": people,
            "roles": roles,
            "error": None,
        },
    )


@router.post("/{ra_id}/edit/")
async def ra_update(
    ra_id: str,
    request: Request,
    person_id: str = Form(...),
    role_id: str = Form(...),
    is_current: str = Form(""),
    start_date: str = Form(""),
    end_date: str = Form(""),
    notes: str = Form(""),
    user: AdminUser | RedirectResponse = Depends(get_admin_user),
    db=Depends(get_db),
):
    """Update a role assignment."""
    redirect, user = check_auth(user)
    if redirect:
        return redirect

    ra = await db.fetchrow("SELECT id FROM role_assignments WHERE id = $1", ra_id)
    if not ra:
        raise HTTPException(status_code=404, detail="Role assignment not found")

    is_current_bool = is_current == "true"
    start_date_val = _parse_date(start_date)
    end_date_val = _parse_date(end_date)

    try:
        await db.execute(
            """UPDATE role_assignments
               SET person_id = $1, role_id = $2, is_current = $3,
                   start_date = $4, end_date = $5, notes = $6
               WHERE id = $7""",
            person_id, role_id, is_current_bool,
            start_date_val, end_date_val, notes or None, ra_id,
        )
    except asyncpg.exceptions.CheckViolationError:
        full_ra = await db.fetchrow("SELECT * FROM role_assignments WHERE id = $1", ra_id)
        people = await _fetch_people(db)
        roles = await _fetch_roles(db)
        return templates.TemplateResponse(
            request,
            "admin/role_assignments/form.html",
            {
                "user": user,
                "active_section": "role_assignments",
                "ra": full_ra,
                "people": people,
                "roles": roles,
                "error": "Current assignments cannot have an end date.",
                "form_person_id": person_id,
                "form_role_id": role_id,
                "form_is_current": is_current_bool,
                "form_start_date": start_date,
                "form_end_date": end_date,
                "form_notes": notes,
            },
            status_code=200,
        )

    return RedirectResponse(f"/admin/role-assignments/{ra_id}/", status_code=303)


@router.post("/{ra_id}/archive/")
async def ra_archive(
    ra_id: str,
    request: Request,
    user: AdminUser | RedirectResponse = Depends(get_admin_user),
    db=Depends(get_db),
):
    """Archive a role assignment (soft delete)."""
    redirect, user = check_auth(user)
    if redirect:
        return redirect

    ra = await db.fetchrow("SELECT id FROM role_assignments WHERE id = $1", ra_id)
    if not ra:
        raise HTTPException(status_code=404, detail="Role assignment not found")

    await db.execute(
        "UPDATE role_assignments SET archived_at = NOW() WHERE id = $1", ra_id
    )
    return RedirectResponse(f"/admin/role-assignments/{ra_id}/", status_code=303)


@router.delete("/{ra_id}/")
async def ra_delete(
    ra_id: str,
    request: Request,
    user: AdminUser | RedirectResponse = Depends(get_admin_user),
    db=Depends(get_db),
):
    """Hard delete an archived role assignment."""
    redirect, user = check_auth(user)
    if redirect:
        return redirect

    ra = await db.fetchrow(
        "SELECT id, archived_at FROM role_assignments WHERE id = $1", ra_id
    )
    if not ra:
        raise HTTPException(status_code=404, detail="Role assignment not found")
    if not ra["archived_at"]:
        raise HTTPException(
            status_code=409, detail="Role assignment must be archived before deletion"
        )

    await db.execute("DELETE FROM role_assignments WHERE id = $1", ra_id)
    return HTMLResponse(content="", status_code=200)
