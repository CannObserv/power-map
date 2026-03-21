"""Admin views for people."""

import asyncpg
from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from src.api.admin.deps import AdminUser, check_auth, get_admin_user, get_db
from src.api.admin.pagination import pagination_context
from src.core.db import generate_id

templates = Jinja2Templates(directory="src/templates")
router = APIRouter(prefix="/people", tags=["admin-people"])


@router.get("/")
async def people_list(
    request: Request,
    q: str = "",
    status: str = "active",
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=10, le=500),
    user: AdminUser | RedirectResponse = Depends(get_admin_user),
    db=Depends(get_db),
):
    """List people with search and status filter."""
    redirect, user = check_auth(user)
    if redirect:
        return redirect

    conditions = []
    params: list = []

    if status == "active":
        conditions.append("p.archived_at IS NULL")
    elif status == "archived":
        conditions.append("p.archived_at IS NOT NULL")

    if q:
        params.append(f"%{q}%")
        conditions.append(f"n.name ILIKE ${len(params)}")

    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
    count_params = params[:]

    count = await db.fetchval(
        f"""SELECT count(DISTINCT p.id)
            FROM people p
            LEFT JOIN person_names n
              ON n.person_id = p.id AND n.is_canonical = TRUE
            {where}""",
        *count_params,
    )

    pctx = pagination_context(page, count, page_size)
    offset = (pctx["page"] - 1) * page_size
    list_params = params + [page_size, offset]

    rows = await db.fetch(
        f"""SELECT p.id, p.archived_at, p.created_at,
                   n.name AS canonical_name
            FROM people p
            LEFT JOIN person_names n
              ON n.person_id = p.id AND n.is_canonical = TRUE
            {where}
            ORDER BY n.name NULLS LAST
            LIMIT ${len(list_params) - 1} OFFSET ${len(list_params)}""",
        *list_params,
    )

    ctx = {
        "user": user,
        "active_section": "people",
        "people": rows,
        "q": q,
        "status": status,
        "page_size": page_size,
        "total": count,
        **pctx,
    }
    template = (
        "admin/people/_region.html"
        if request.headers.get("HX-Request") and not request.headers.get("HX-Boosted")
        else "admin/people/list.html"
    )
    return templates.TemplateResponse(request, template, ctx)


@router.get("/new/")
async def person_new_form(
    request: Request,
    user: AdminUser | RedirectResponse = Depends(get_admin_user),
    db=Depends(get_db),
):
    """New person form."""
    redirect, user = check_auth(user)
    if redirect:
        return redirect
    return templates.TemplateResponse(
        request,
        "admin/people/form.html",
        {
            "user": user,
            "active_section": "people",
            "person": None,
            "canonical_name": "",
        },
    )


@router.post("/new/")
async def person_create(
    request: Request,
    name: str = Form(...),
    personal_pronouns: str = Form(""),
    notes: str = Form(""),
    user: AdminUser | RedirectResponse = Depends(get_admin_user),
    db=Depends(get_db),
):
    """Create a new person."""
    redirect, user = check_auth(user)
    if redirect:
        return redirect
    person_id = generate_id()
    await db.execute(
        "INSERT INTO people (id, personal_pronouns, notes) VALUES ($1, $2, $3)",
        person_id, personal_pronouns or None, notes or None,
    )
    await db.execute(
        "INSERT INTO person_names"
        " (id, person_id, name, is_canonical) VALUES ($1, $2, $3, TRUE)",
        generate_id(), person_id, name,
    )
    return RedirectResponse(f"/admin/people/{person_id}/", status_code=303)


@router.get("/{person_id}/")
async def person_detail(
    person_id: str,
    request: Request,
    user: AdminUser | RedirectResponse = Depends(get_admin_user),
    db=Depends(get_db),
):
    """Person detail view."""
    redirect, user = check_auth(user)
    if redirect:
        return redirect

    person = await db.fetchrow("SELECT * FROM people WHERE id = $1", person_id)
    if not person:
        raise HTTPException(status_code=404, detail="Person not found")

    names = await db.fetch(
        "SELECT * FROM person_names WHERE person_id = $1 ORDER BY is_canonical DESC",
        person_id,
    )
    contacts = await db.fetch(
        "SELECT * FROM contact_methods WHERE entity_type = 'person' AND entity_id = $1",
        person_id,
    )
    addresses = await db.fetch(
        """SELECT ea.*, a.standardized, a.address_line_1, a.city, a.region, a.postal_code
           FROM entity_addresses ea JOIN addresses a ON a.id = ea.address_id
           WHERE ea.entity_type = 'person' AND ea.entity_id = $1""",
        person_id,
    )
    urls = await db.fetch(
        """SELECT u.*, ut.display_name AS url_type_name
           FROM urls u JOIN url_types ut ON ut.id = u.url_type_id
           WHERE u.entity_type = 'person' AND u.entity_id = $1""",
        person_id,
    )
    social = await db.fetch(
        """SELECT sl.*, p.display_name AS platform_name
           FROM social_links sl JOIN platforms p ON p.id = sl.platform_id
           WHERE sl.entity_type = 'person' AND sl.entity_id = $1""",
        person_id,
    )
    identifiers = await db.fetch(
        """SELECT i.*, eit.display_name AS type_name, eit.full_name AS type_full_name
           FROM identifiers i
           JOIN entity_identifier_types eit ON eit.id = i.entity_identifier_type_id
           WHERE i.entity_id = $1""",
        person_id,
    )
    role_assignments = await db.fetch(
        """SELECT ra.id, ra.is_current, ra.start_date, ra.end_date, ra.archived_at,
                  r.title, o.id AS org_id, dn.display_name AS org_name
           FROM role_assignments ra
           JOIN roles r ON r.id = ra.role_id
           JOIN organizations o ON o.id = r.organization_id
           LEFT JOIN v_org_display_names dn ON dn.organization_id = o.id
           WHERE ra.person_id = $1
           ORDER BY ra.is_current DESC, ra.start_date DESC NULLS LAST""",
        person_id,
    )

    return templates.TemplateResponse(
        request,
        "admin/people/detail.html",
        {
            "user": user,
            "active_section": "people",
            "person": person,
            "names": names,
            "contacts": contacts,
            "addresses": addresses,
            "urls": urls,
            "social": social,
            "identifiers": identifiers,
            "role_assignments": role_assignments,
        },
    )


@router.get("/{person_id}/edit/")
async def person_edit_form(
    person_id: str,
    request: Request,
    user: AdminUser | RedirectResponse = Depends(get_admin_user),
    db=Depends(get_db),
):
    """Edit person form."""
    redirect, user = check_auth(user)
    if redirect:
        return redirect
    person = await db.fetchrow("SELECT * FROM people WHERE id = $1", person_id)
    if not person:
        raise HTTPException(status_code=404, detail="Person not found")
    canonical = await db.fetchrow(
        "SELECT name FROM person_names WHERE person_id = $1 AND is_canonical = TRUE",
        person_id,
    )
    return templates.TemplateResponse(
        request,
        "admin/people/form.html",
        {
            "user": user,
            "active_section": "people",
            "person": person,
            "canonical_name": canonical["name"] if canonical else "",
        },
    )


@router.post("/{person_id}/edit/")
async def person_update(
    person_id: str,
    request: Request,
    name: str = Form(...),
    personal_pronouns: str = Form(""),
    notes: str = Form(""),
    user: AdminUser | RedirectResponse = Depends(get_admin_user),
    db=Depends(get_db),
):
    """Update a person."""
    redirect, user = check_auth(user)
    if redirect:
        return redirect
    person = await db.fetchrow("SELECT id FROM people WHERE id = $1", person_id)
    if not person:
        raise HTTPException(status_code=404, detail="Person not found")
    await db.execute(
        "UPDATE people SET personal_pronouns = $1, notes = $2 WHERE id = $3",
        personal_pronouns or None, notes or None, person_id,
    )
    existing = await db.fetchrow(
        "SELECT id FROM person_names WHERE person_id = $1 AND is_canonical = TRUE",
        person_id,
    )
    if existing:
        await db.execute(
            "UPDATE person_names SET name = $1 WHERE id = $2", name, existing["id"]
        )
    else:
        await db.execute(
            "INSERT INTO person_names"
            " (id, person_id, name, is_canonical) VALUES ($1, $2, $3, TRUE)",
            generate_id(), person_id, name,
        )
    return RedirectResponse(f"/admin/people/{person_id}/", status_code=303)


@router.post("/{person_id}/archive/")
async def person_archive(
    person_id: str,
    request: Request,
    user: AdminUser | RedirectResponse = Depends(get_admin_user),
    db=Depends(get_db),
):
    """Archive a person (soft delete)."""
    redirect, user = check_auth(user)
    if redirect:
        return redirect
    person = await db.fetchrow("SELECT id FROM people WHERE id = $1", person_id)
    if not person:
        raise HTTPException(status_code=404, detail="Person not found")
    await db.execute("UPDATE people SET archived_at = NOW() WHERE id = $1", person_id)
    return RedirectResponse(f"/admin/people/{person_id}/", status_code=303)


@router.delete("/{person_id}/")
async def person_delete(
    person_id: str,
    request: Request,
    user: AdminUser | RedirectResponse = Depends(get_admin_user),
    db=Depends(get_db),
):
    """Hard delete an archived person."""
    redirect, user = check_auth(user)
    if redirect:
        return redirect
    person = await db.fetchrow("SELECT id, archived_at FROM people WHERE id = $1", person_id)
    if not person:
        raise HTTPException(status_code=404, detail="Person not found")
    if not person["archived_at"]:
        raise HTTPException(status_code=409, detail="Person must be archived before deletion")
    try:
        await db.execute("DELETE FROM person_names WHERE person_id = $1", person_id)
        await db.execute("DELETE FROM people WHERE id = $1", person_id)
    except asyncpg.ForeignKeyViolationError:
        raise HTTPException(
            status_code=409,
            detail="Cannot delete: person has related records (role assignments, etc.)",
        )
    return HTMLResponse(content="", status_code=200)
