"""Admin views for roles."""

import asyncpg
from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from src.api.admin.deps import (
    AdminUser,
    escape_like,
    get_admin_user,
    get_db,
    is_htmx,
    resolve_query_flash,
)
from src.api.admin.pagination import PAGE_SIZE_DEFAULT, PAGE_SIZE_MAX, PAGE_SIZE_MIN
from src.api.admin.roles_assignments_inline import fetch_role_assignments
from src.api.admin.roles_queries import VALID_STATUSES, query_roles_rows
from src.api.admin.roles_shared import fetch_role_types, positionless_seat_error
from src.core.db import generate_id
from src.core.role_title import synthesize_role_title

templates = Jinja2Templates(directory="src/templates")
router = APIRouter(prefix="/roles", tags=["admin-roles"])

_FLASH_MESSAGES: dict[str, tuple[str, str]] = {
    "archived": ("success", "Role archived."),
}


@router.get("/")
async def roles_list(
    request: Request,
    q: str = "",
    org_q: str = "",
    status: str = "active",
    page: int = Query(1, ge=1),
    page_size: int = Query(PAGE_SIZE_DEFAULT, ge=PAGE_SIZE_MIN, le=PAGE_SIZE_MAX),
    user: AdminUser = Depends(get_admin_user),
    db=Depends(get_db),
):
    """List roles with title/org search and status filter."""
    if status not in VALID_STATUSES:
        status = "active"
    rows, count, pctx, hidden_matches = await query_roles_rows(
        db, q=q, org_q=org_q, status=status, page=page, page_size=page_size
    )

    ctx = {
        "user": user,
        "active_section": "roles",
        "roles": rows,
        "q": q,
        "org_q": org_q,
        "status": status,
        "page_size": page_size,
        "total": count,
        "hidden_matches": hidden_matches,
        **pctx,
    }
    template = (
        "admin/roles/_region.html"
        if request.headers.get("HX-Request") and not request.headers.get("HX-Boosted")
        else "admin/roles/list.html"
    )
    return templates.TemplateResponse(request, template, ctx)


async def _fetch_orgs(db):
    """Active organizations for the role-form org select."""
    return await db.fetch(
        """SELECT o.id, dn.display_name AS name
           FROM organizations o
           LEFT JOIN v_org_display_names dn ON dn.organization_id = o.id
           WHERE o.archived_at IS NULL ORDER BY dn.display_name NULLS LAST"""
    )


@router.get("/new/")
async def role_new_form(
    request: Request,
    user: AdminUser = Depends(get_admin_user),
    db=Depends(get_db),
):
    """New role form."""
    return templates.TemplateResponse(
        request,
        "admin/roles/form.html",
        {
            "user": user,
            "active_section": "roles",
            "role": None,
            "orgs": await _fetch_orgs(db),
            "role_types": await fetch_role_types(db),
            "values": {},
        },
    )


@router.post("/new/")
async def role_create(
    request: Request,
    organization_id: str = Form(...),
    title: str = Form(""),
    role_type_id: str = Form(""),
    jurisdiction_id: str = Form(""),
    qualifier: str = Form(""),
    notes: str = Form(""),
    user: AdminUser = Depends(get_admin_user),
    db=Depends(get_db),
):
    """Create a new role — plain or with a role type + jurisdiction (+ qualifier).

    Validates the two DB check-constraints up front with clear errors, and, for a
    role with a jurisdiction, synthesizes the canonical WA title (#267) — falling
    back to requiring a manual title when synthesis is unavailable.
    """
    title_c = title.strip()
    role_type_id_c = role_type_id.strip() or None
    jurisdiction_id_c = jurisdiction_id.strip() or None
    qualifier_c = qualifier.strip() or None
    notes_c = notes.strip() or None

    async def _reload(error: str):
        jur_name = None
        if jurisdiction_id_c:
            jur_name = await db.fetchval(
                "SELECT name FROM jurisdictions WHERE id=$1", jurisdiction_id_c
            )
        return templates.TemplateResponse(
            request,
            "admin/roles/form.html",
            {
                "user": user,
                "active_section": "roles",
                "role": None,
                "orgs": await _fetch_orgs(db),
                "role_types": await fetch_role_types(db),
                "values": {
                    "organization_id": organization_id,
                    "title": title_c,
                    "role_type_id": role_type_id_c,
                    "jurisdiction_id": jurisdiction_id_c,
                    "jurisdiction_name": jur_name,
                    "qualifier": qualifier_c,
                    "notes": notes_c,
                },
                "error": error,
            },
            status_code=200,
        )

    # Mirror the DB check-constraints (chk_role_qualifier_needs_jurisdiction,
    # chk_role_jurisdiction_needs_role_type) so the admin sees a clear message
    # rather than a raw IntegrityError.
    if qualifier_c is not None and jurisdiction_id_c is None:
        return await _reload("A qualifier requires a jurisdiction.")
    if jurisdiction_id_c is not None and role_type_id_c is None:
        return await _reload("A jurisdiction requires a role type.")

    if jurisdiction_id_c is not None:
        # With a jurisdiction, PM curates the title. When the formatter can render
        # one, ALWAYS synthesize — any supplied title is ignored so the admin
        # can't diverge from the canonical form (#264 CR-1, matching the inline
        # editor). Keep/require a manual title only when synthesis is unavailable
        # (non-WA jurisdictions).
        rt_row = await db.fetchrow(
            "SELECT slug, requires_qualifier FROM role_types WHERE id=$1", role_type_id_c
        )
        rt_slug = rt_row["slug"] if rt_row else None
        jur_slug = await db.fetchval(
            "SELECT slug FROM jurisdictions WHERE id=$1", jurisdiction_id_c
        )
        synthesized = synthesize_role_title(rt_slug, jur_slug, qualifier_c) if rt_slug else None
        if synthesized is not None:
            title_c = synthesized
        elif not title_c:
            return await _reload("Could not auto-generate a title for this role — enter one.")
        # Mirror the requires_qualifier guard + DB trigger (#273), after the title
        # check so a missing title still reports first.
        seat_error = positionless_seat_error(
            rt_row["requires_qualifier"] if rt_row else False, qualifier_c
        )
        if seat_error:
            return await _reload(seat_error)
    elif not title_c:
        return await _reload("Title is required for a role without a jurisdiction.")

    role_id = generate_id()
    try:
        await db.execute(
            "INSERT INTO roles"
            " (id, organization_id, title, notes, role_type_id, jurisdiction_id, qualifier)"
            " VALUES ($1, $2, $3, $4, $5, $6, $7)",
            role_id,
            organization_id,
            title_c,
            notes_c,
            role_type_id_c,
            jurisdiction_id_c,
            qualifier_c,
        )
    except asyncpg.UniqueViolationError:
        if jurisdiction_id_c is not None:
            return await _reload(
                "A role with this role type, jurisdiction, and qualifier already exists."
            )
        return await _reload(f"A role titled “{title_c}” already exists for this organization.")
    except asyncpg.ForeignKeyViolationError:
        return await _reload(
            "The selected organization, role type, or jurisdiction no longer exists."
        )
    return RedirectResponse(f"/admin/roles/{role_id}/", status_code=303)


@router.get("/search/")
async def roles_search(
    request: Request,
    q: str = "",
    user: AdminUser = Depends(get_admin_user),
    db=Depends(get_db),
):
    """Typeahead search — returns HTML fragment of matching roles."""
    results = []
    if q.strip():
        results = await db.fetch(
            """SELECT r.id, r.title, dn.display_name AS org_name
               FROM roles r
               LEFT JOIN v_org_display_names dn ON dn.organization_id = r.organization_id
               WHERE r.archived_at IS NULL
                 AND (r.title ILIKE $1 ESCAPE '\\' OR dn.display_name ILIKE $1 ESCAPE '\\')
               ORDER BY dn.display_name NULLS LAST, r.title
               LIMIT 20""",
            f"%{escape_like(q.strip())}%",
        )
    return templates.TemplateResponse(
        request,
        "admin/roles/partials/_search_results.html",
        {"results": results},
    )


@router.get("/{role_id}/")
async def role_detail(
    role_id: str,
    request: Request,
    user: AdminUser = Depends(get_admin_user),
    db=Depends(get_db),
    flash: str | None = Query(None),
):
    """Role detail view."""

    role = await db.fetchrow(
        """SELECT r.id, r.title, r.notes, r.archived_at, r.created_at, r.updated_at,
                  r.organization_id AS org_id,
                  r.role_type_id, r.jurisdiction_id, r.qualifier,
                  dn.display_name AS org_name,
                  rt.display_name AS role_type_name,
                  rt.slug AS role_type_slug,
                  jdn.display_name AS jurisdiction_name,
                  jdn.slug AS jurisdiction_slug
           FROM roles r
           LEFT JOIN v_org_display_names dn ON dn.organization_id = r.organization_id
           LEFT JOIN role_types rt ON rt.id = r.role_type_id
           LEFT JOIN v_jurisdiction_display_names jdn
                  ON jdn.jurisdiction_id = r.jurisdiction_id
           WHERE r.id = $1""",
        role_id,
    )
    if not role:
        raise HTTPException(status_code=404, detail="Role not found")

    assignments = await fetch_role_assignments(role_id, db)

    flash_msg, resp_headers = resolve_query_flash(request, _FLASH_MESSAGES, flash)
    return templates.TemplateResponse(
        request,
        "admin/roles/detail.html",
        {
            "user": user,
            "active_section": "roles",
            "role": role,
            "role_id": role_id,
            "assignments": assignments,
            "flash_msg": flash_msg,
        },
        headers=resp_headers,
    )


@router.post("/{role_id}/archive/")
async def role_archive(
    role_id: str,
    user: AdminUser = Depends(get_admin_user),
    db=Depends(get_db),
):
    """Archive a role (soft delete)."""
    role = await db.fetchrow("SELECT id, archived_at FROM roles WHERE id = $1", role_id)
    if not role:
        raise HTTPException(status_code=404, detail="Role not found")
    if role["archived_at"]:
        raise HTTPException(status_code=409, detail="Role is already archived")
    await db.execute("UPDATE roles SET archived_at = NOW() WHERE id = $1", role_id)
    return RedirectResponse(f"/admin/roles/{role_id}/?flash=archived", status_code=303)


@router.delete("/{role_id}/")
async def role_delete(
    role_id: str,
    request: Request,
    user: AdminUser = Depends(get_admin_user),
    db=Depends(get_db),
):
    """Hard delete an archived role."""
    role = await db.fetchrow("SELECT id, archived_at FROM roles WHERE id = $1", role_id)
    if not role:
        raise HTTPException(status_code=404, detail="Role not found")
    if not role["archived_at"]:
        raise HTTPException(status_code=409, detail="Role must be archived before deletion")
    try:
        async with db.transaction():
            await db.execute("DELETE FROM roles WHERE id = $1", role_id)
            # Tombstone (issue #277): emit a 'deleted' signal for subscribers.
            await db.execute(
                "INSERT INTO deleted_entities (entity_type, entity_id) VALUES ('role', $1)"
                " ON CONFLICT DO NOTHING",
                role_id,
            )
    except asyncpg.ForeignKeyViolationError:
        raise HTTPException(status_code=409, detail="Cannot delete role with existing assignments")
    if is_htmx(request):
        return HTMLResponse(content="", status_code=200, headers={"HX-Redirect": "/admin/roles/"})
    return RedirectResponse("/admin/roles/", status_code=303)
