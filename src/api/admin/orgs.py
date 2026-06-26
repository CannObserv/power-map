"""Admin views for organizations."""

import asyncpg
from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from markupsafe import escape

from src.api.admin._events_shared import fetch_entity_events
from src.api.admin.deps import (
    AdminUser,
    escape_like,
    flash_trigger,
    get_admin_user,
    get_db,
    is_htmx,
    resolve_query_flash,
)
from src.api.admin.pagination import pagination_context
from src.core.db import generate_id
from src.core.logging import get_logger
from src.core.organizations import ActiveOnArchivedOrg, OrgNotFound, set_org_active

logger = get_logger(__name__)

templates = Jinja2Templates(directory="src/templates")
router = APIRouter(prefix="/orgs", tags=["admin-orgs"])


_FLASH_MESSAGES: dict[str, tuple[str, str]] = {
    "archived": ("success", "Organization archived."),
    "unarchived": ("success", "Organization unarchived."),
    "deleted": ("success", "Organization deleted."),
}


@router.get("/")
async def orgs_list(
    request: Request,
    q: str = "",
    status: str = "active",
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=10, le=500),
    flash: str | None = Query(None),
    user: AdminUser = Depends(get_admin_user),
    db=Depends(get_db),
):
    """List organizations with search and status filter."""

    conditions = []
    params: list = []

    if status == "active":
        conditions.append("o.archived_at IS NULL AND o.active = TRUE")
    elif status == "inactive":
        conditions.append("o.archived_at IS NULL AND o.active = FALSE")
    elif status == "archived":
        conditions.append("o.archived_at IS NOT NULL")

    if q:
        params.append(q)
        conditions.append(f"o.search_tsv @@ plainto_tsquery('pm_simple', ${len(params)})")

    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
    count_params = params[:]

    count = await db.fetchval(
        f"""SELECT count(o.id)
            FROM organizations o
            {where}""",
        *count_params,
    )

    pctx = pagination_context(page, count, page_size)
    offset = (pctx["page"] - 1) * page_size
    list_params = params + [page_size, offset]

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

    flash_msg, resp_headers = resolve_query_flash(request, _FLASH_MESSAGES, flash)

    ctx = {
        "user": user,
        "active_section": "orgs",
        "orgs": rows,
        "q": q,
        "status": status,
        "page_size": page_size,
        "total": count,
        "flash_msg": flash_msg,
        **pctx,
    }
    template = "admin/orgs/_region.html" if is_htmx(request) else "admin/orgs/list.html"
    return templates.TemplateResponse(request, template, ctx, headers=resp_headers)


async def _fetch_parents(db) -> list:
    """Fetch all non-archived orgs for the parent dropdown."""
    return await db.fetch(
        """SELECT o.id, dn.display_name AS canonical_name
           FROM organizations o
           LEFT JOIN v_org_display_names dn ON dn.organization_id = o.id
           WHERE o.archived_at IS NULL ORDER BY dn.display_name NULLS LAST"""
    )


@router.get("/new/")
async def org_new_form(
    request: Request,
    user: AdminUser = Depends(get_admin_user),
    db=Depends(get_db),
):
    """New organization form."""
    parents = await _fetch_parents(db)
    return templates.TemplateResponse(
        request,
        "admin/orgs/form.html",
        {
            "user": user,
            "active_section": "orgs",
            "org": None,
            "parents": parents,
            "canonical_name": "",
            "canonical_acronym": "",
            "org_notes": "",
            "errors": {},
        },
    )


@router.post("/new/")
async def org_create(
    request: Request,
    name: str = Form(""),
    acronym: str = Form(""),
    active: str = Form(""),
    parent_id: str = Form(""),
    notes: str = Form(""),
    user: AdminUser = Depends(get_admin_user),
    db=Depends(get_db),
):
    """Create a new organization."""
    if not name.strip():
        parents = await _fetch_parents(db)
        return templates.TemplateResponse(
            request,
            "admin/orgs/form.html",
            {
                "user": user,
                "active_section": "orgs",
                "org": None,
                "parents": parents,
                "canonical_name": name,
                "canonical_acronym": acronym,
                "org_notes": notes,
                "errors": {"name": "Name is required"},
            },
            status_code=422,
        )
    org_id = generate_id()
    async with db.transaction():
        await db.execute(
            "INSERT INTO organizations (id, active, parent_id, notes) VALUES ($1, $2, $3, $4)",
            org_id,
            active == "true",
            parent_id or None,
            notes or None,
        )
        await db.execute(
            "INSERT INTO organization_names"
            " (id, organization_id, name, is_canonical) VALUES ($1, $2, $3, TRUE)",
            generate_id(),
            org_id,
            name.strip(),
        )
        if acronym.strip():
            await db.execute(
                "INSERT INTO organization_acronyms"
                " (id, organization_id, acronym, is_canonical) VALUES ($1, $2, $3, TRUE)",
                generate_id(),
                org_id,
                acronym.strip(),
            )
    return RedirectResponse(f"/admin/orgs/{org_id}/", status_code=303)


@router.get("/search/")
async def orgs_search(
    request: Request,
    q: str = "",
    user: AdminUser = Depends(get_admin_user),
    db=Depends(get_db),
):
    """Typeahead search — returns an HTML fragment of matching org options."""
    results = []
    if q.strip():
        results = await db.fetch(
            """SELECT o.id, dn.display_name
               FROM organizations o
               LEFT JOIN v_org_display_names dn ON dn.organization_id = o.id
               WHERE o.archived_at IS NULL
                 AND dn.display_name ILIKE $1 ESCAPE '\\'
               ORDER BY dn.display_name NULLS LAST
               LIMIT 20""",
            f"%{escape_like(q.strip())}%",
        )
    return templates.TemplateResponse(
        request,
        "admin/orgs/partials/_search_results.html",
        {"results": results},
    )


@router.post("/{org_id}/inline/active/")
async def org_inline_active_post(
    org_id: str,
    request: Request,
    active: str = Form(""),
    user: AdminUser = Depends(get_admin_user),
    db=Depends(get_db),
):
    """Toggle org active flag; return updated active-toggle partial.

    Shares the archived + no-op guards with the public observation path via
    ``set_org_active`` (#241): the toggle is rejected with 409 on an archived org
    (the UI already disables the checkbox, so this only guards out-of-band POSTs)
    and a redundant re-assertion is a true no-op (no entity_changes event). The
    transaction holds the helper's ``FOR UPDATE`` lock until commit, keeping the
    archived check atomic against a concurrent archive. ``set_org_active`` is the
    single existence gate: a missing org raises OrgNotFound → 404.
    """
    new_active = active == "true"
    try:
        async with db.transaction():
            await set_org_active(db, org_id, new_active)
    except ActiveOnArchivedOrg as exc:
        raise HTTPException(
            status_code=409, detail="Cannot change active on an archived organization."
        ) from exc
    except OrgNotFound as exc:
        raise HTTPException(status_code=404) from exc
    org = await db.fetchrow("SELECT * FROM organizations WHERE id=$1", org_id)
    if not org:
        # Org hard-deleted in the window between commit and this re-fetch.
        raise HTTPException(status_code=404)
    if not is_htmx(request):
        return RedirectResponse(f"/admin/orgs/{org_id}/", status_code=303)
    label = "Marked active." if new_active else "Marked inactive."
    level = "success" if new_active else "info"
    return templates.TemplateResponse(
        request,
        "admin/orgs/partials/_active_toggle.html",
        {"org": org},
        headers=flash_trigger(level, label),
    )


@router.get("/{org_id}/inline/notes/")
async def org_inline_notes_get(
    org_id: str,
    request: Request,
    user: AdminUser = Depends(get_admin_user),
    db=Depends(get_db),
):
    """Return notes read partial."""
    org = await db.fetchrow("SELECT * FROM organizations WHERE id=$1", org_id)
    if not org:
        raise HTTPException(status_code=404)
    return templates.TemplateResponse(request, "admin/orgs/partials/_notes_read.html", {"org": org})


@router.get("/{org_id}/inline/notes/edit/")
async def org_inline_notes_edit_get(
    org_id: str,
    request: Request,
    user: AdminUser = Depends(get_admin_user),
    db=Depends(get_db),
):
    """Return notes edit form partial."""
    org = await db.fetchrow("SELECT * FROM organizations WHERE id=$1", org_id)
    if not org:
        raise HTTPException(status_code=404)
    return templates.TemplateResponse(request, "admin/orgs/partials/_notes_form.html", {"org": org})


@router.post("/{org_id}/inline/notes/")
async def org_inline_notes_post(
    org_id: str,
    request: Request,
    notes: str = Form(""),
    user: AdminUser = Depends(get_admin_user),
    db=Depends(get_db),
):
    """Save notes; return updated notes read partial."""
    org = await db.fetchrow("SELECT * FROM organizations WHERE id=$1", org_id)
    if not org:
        raise HTTPException(status_code=404)
    await db.execute(
        "UPDATE organizations SET notes=$1 WHERE id=$2",
        notes.strip() or None,
        org_id,
    )
    org = await db.fetchrow("SELECT * FROM organizations WHERE id=$1", org_id)
    if not is_htmx(request):
        return RedirectResponse(f"/admin/orgs/{org_id}/", status_code=303)
    return templates.TemplateResponse(
        request,
        "admin/orgs/partials/_notes_read.html",
        {"org": org},
        headers=flash_trigger("success", "Notes saved."),
    )


@router.get("/{org_id}/inline/parent/")
async def org_inline_parent_get(
    org_id: str,
    request: Request,
    user: AdminUser = Depends(get_admin_user),
    db=Depends(get_db),
):
    """Return read partial for parent org field."""
    org = await db.fetchrow("SELECT * FROM organizations WHERE id=$1", org_id)
    if not org:
        raise HTTPException(status_code=404)
    parent = None
    if org["parent_id"]:
        parent = await db.fetchrow(
            "SELECT o.id, dn.display_name FROM organizations o"
            " LEFT JOIN v_org_display_names dn ON dn.organization_id=o.id"
            " WHERE o.id=$1",
            org["parent_id"],
        )
    return templates.TemplateResponse(
        request,
        "admin/orgs/partials/_parent_read.html",
        {"org": org, "parent": parent},
    )


@router.post("/{org_id}/inline/parent/")
async def org_inline_parent_post(
    org_id: str,
    request: Request,
    parent_id: str = Form(""),
    user: AdminUser = Depends(get_admin_user),
    db=Depends(get_db),
):
    """Save parent org inline; return updated read partial."""
    if parent_id and parent_id == org_id:
        raise HTTPException(status_code=422, detail="An organization cannot be its own parent")
    org = await db.fetchrow("SELECT * FROM organizations WHERE id=$1", org_id)
    if not org:
        raise HTTPException(status_code=404)
    resolved = parent_id.strip() or None
    if resolved:
        exists = await db.fetchval("SELECT id FROM organizations WHERE id=$1", resolved)
        if not exists:
            raise HTTPException(status_code=422, detail="Parent organization not found")
    await db.execute("UPDATE organizations SET parent_id=$1 WHERE id=$2", resolved, org_id)
    org = await db.fetchrow("SELECT * FROM organizations WHERE id=$1", org_id)
    parent = None
    if org["parent_id"]:
        parent = await db.fetchrow(
            "SELECT o.id, dn.display_name FROM organizations o"
            " LEFT JOIN v_org_display_names dn ON dn.organization_id=o.id"
            " WHERE o.id=$1",
            org["parent_id"],
        )
    if not is_htmx(request):
        return RedirectResponse(f"/admin/orgs/{org_id}/", status_code=303)
    if parent:
        flash_body = f"Parent set to <strong>{escape(parent['display_name'])}</strong>."
        flash_level = "success"
    else:
        flash_body = "Parent organization cleared."
        flash_level = "info"
    return templates.TemplateResponse(
        request,
        "admin/orgs/partials/_parent_read.html",
        {"org": org, "parent": parent},
        headers=flash_trigger(flash_level, flash_body),
    )


@router.get("/{org_id}/inline/parent/edit/")
async def org_inline_parent_edit_get(
    org_id: str,
    request: Request,
    user: AdminUser = Depends(get_admin_user),
    db=Depends(get_db),
):
    """Return the edit form partial for parent org field."""
    org = await db.fetchrow("SELECT * FROM organizations WHERE id=$1", org_id)
    if not org:
        raise HTTPException(status_code=404)
    parent = None
    if org["parent_id"]:
        parent = await db.fetchrow(
            "SELECT o.id, dn.display_name FROM organizations o"
            " LEFT JOIN v_org_display_names dn ON dn.organization_id=o.id"
            " WHERE o.id=$1",
            org["parent_id"],
        )
    return templates.TemplateResponse(
        request,
        "admin/orgs/partials/_parent_form.html",
        {"org": org, "parent": parent},
    )


@router.get("/{org_id}/")
async def org_detail(
    org_id: str,
    request: Request,
    user: AdminUser = Depends(get_admin_user),
    db=Depends(get_db),
    flash: str | None = Query(None),
):
    """Organization detail view."""

    org = await db.fetchrow("SELECT * FROM organizations WHERE id = $1", org_id)
    if not org:
        raise HTTPException(status_code=404, detail="Organization not found")

    names = await db.fetch(
        "SELECT * FROM organization_names WHERE organization_id = $1"
        " ORDER BY is_canonical DESC, name_type, name",
        org_id,
    )
    acronyms = await db.fetch(
        "SELECT * FROM organization_acronyms WHERE organization_id = $1"
        " ORDER BY is_canonical DESC, acronym",
        org_id,
    )
    addresses = await db.fetch(
        """SELECT ea.*, a.standardized, a.address_line_1, a.city, a.region, a.postal_code
           FROM entity_addresses ea JOIN addresses a ON a.id = ea.address_id
           WHERE ea.entity_type = 'organization' AND ea.entity_id = $1""",
        org_id,
    )
    email_contacts = await db.fetch(
        "SELECT * FROM contact_methods"
        " WHERE entity_type = 'organization' AND entity_id = $1 AND contact_type = 'email'",
        org_id,
    )
    phone_contacts = await db.fetch(
        "SELECT * FROM contact_methods"
        " WHERE entity_type = 'organization' AND entity_id = $1 AND contact_type = 'phone'",
        org_id,
    )
    links = await db.fetch(
        """SELECT l.*, lt.display_name AS link_type_name, lt.is_social
           FROM links l JOIN link_types lt ON lt.id = l.link_type_id
           WHERE l.entity_type = 'organization' AND l.entity_id = $1""",
        org_id,
    )
    identifiers = await db.fetch(
        """SELECT i.*, eit.display_name AS type_name, eit.full_name AS type_full_name
           FROM identifiers i
           JOIN entity_identifier_types eit ON eit.id = i.entity_identifier_type_id
           WHERE i.entity_id = $1 AND eit.entity_type = 'organization'""",
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
        """SELECT r.id, r.title, sub.assignment_count, sub.current_count
           FROM roles r
           CROSS JOIN LATERAL (
               SELECT COUNT(*) AS assignment_count,
                      COUNT(*) FILTER (WHERE is_current) AS current_count
               FROM role_assignments
               WHERE role_id = r.id AND archived_at IS NULL
           ) sub
           WHERE r.organization_id = $1 AND r.archived_at IS NULL
           ORDER BY r.title""",
        org_id,
    )
    events = await fetch_entity_events(org_id, "organization", db)
    parent = None
    if org["parent_id"]:
        parent = await db.fetchrow(
            """SELECT o.id, dn.display_name
               FROM organizations o
               LEFT JOIN v_org_display_names dn ON dn.organization_id = o.id
               WHERE o.id = $1""",
            org["parent_id"],
        )

    canonical_name = next((n["name"] for n in names if n["is_canonical"]), "")
    canonical_acronym = next((a["acronym"] for a in acronyms if a["is_canonical"]), "")

    display_name_row = await db.fetchrow(
        "SELECT display_name FROM v_org_display_names WHERE organization_id = $1", org_id
    )
    display_name = display_name_row["display_name"] if display_name_row else None

    flash_msg, resp_headers = resolve_query_flash(request, _FLASH_MESSAGES, flash)
    return templates.TemplateResponse(
        request,
        "admin/orgs/detail.html",
        {
            "user": user,
            "active_section": "orgs",
            "org": org,
            "org_id": org_id,
            "canonical_name": canonical_name,
            "canonical_acronym": canonical_acronym,
            "display_name": display_name,
            "names": names,
            "acronyms": acronyms,
            "addresses": addresses,
            "email_contacts": email_contacts,
            "phone_contacts": phone_contacts,
            "links": links,
            "identifiers": identifiers,
            "children": children,
            "roles": roles,
            "events": events,
            "parent": parent,
            "flash_msg": flash_msg,
        },
        headers=resp_headers,
    )


@router.get("/{org_id}/children/search/")
async def children_search(
    org_id: str,
    request: Request,
    q: str = "",
    user: AdminUser = Depends(get_admin_user),
    db=Depends(get_db),
):
    """Typeahead search for adding a child org — excludes self and existing children."""
    results = []
    if q.strip():
        results = await db.fetch(
            """SELECT o.id, dn.display_name
               FROM organizations o
               LEFT JOIN v_org_display_names dn ON dn.organization_id = o.id
               WHERE o.archived_at IS NULL
                 AND o.id != $2
                 AND (o.parent_id IS NULL OR o.parent_id != $2)
                 AND dn.display_name ILIKE $1 ESCAPE '\\'
               ORDER BY dn.display_name NULLS LAST
               LIMIT 20""",
            f"%{escape_like(q.strip())}%",
            org_id,
        )
    return templates.TemplateResponse(
        request,
        "admin/orgs/partials/_search_results.html",
        {"results": results},
    )


@router.get("/{org_id}/children/new-row/")
async def children_new_row(
    org_id: str,
    request: Request,
    user: AdminUser = Depends(get_admin_user),
    db=Depends(get_db),
):
    """Return empty child search form row."""
    return templates.TemplateResponse(
        request, "admin/orgs/partials/_child_form_row.html", {"org_id": org_id}
    )


@router.post("/{org_id}/children/")
async def children_add(
    org_id: str,
    request: Request,
    child_id: str = Form(...),
    user: AdminUser = Depends(get_admin_user),
    db=Depends(get_db),
):
    """Link an existing org as a child of this org."""
    if child_id == org_id:
        raise HTTPException(status_code=422, detail="An organization cannot be its own child")
    child = await db.fetchrow("SELECT id FROM organizations WHERE id=$1", child_id)
    if not child:
        raise HTTPException(status_code=422, detail="Child organization not found")
    await db.execute("UPDATE organizations SET parent_id=$1 WHERE id=$2", org_id, child_id)
    row = await db.fetchrow(
        """SELECT o.id, o.active, o.archived_at, dn.display_name AS canonical_name
           FROM organizations o
           LEFT JOIN v_org_display_names dn ON dn.organization_id=o.id
           WHERE o.id=$1""",
        child_id,
    )
    if not is_htmx(request):
        return RedirectResponse(f"/admin/orgs/{org_id}/", status_code=303)
    return templates.TemplateResponse(
        request,
        "admin/orgs/partials/_child_row.html",
        {"org_id": org_id, "child": row},
        headers=flash_trigger(
            "success",
            f"<strong>{escape(row['canonical_name'])}</strong> linked as child.",
        ),
    )


@router.delete("/{org_id}/children/{child_id}/")
async def children_remove(
    org_id: str,
    child_id: str,
    request: Request,
    user: AdminUser = Depends(get_admin_user),
    db=Depends(get_db),
):
    """Unlink a child org (clears its parent_id)."""
    child = await db.fetchrow(
        "SELECT id FROM organizations WHERE id=$1 AND parent_id=$2", child_id, org_id
    )
    if not child:
        raise HTTPException(status_code=404)
    await db.execute("UPDATE organizations SET parent_id=NULL WHERE id=$1", child_id)
    return HTMLResponse(
        content="",
        status_code=200,
        headers=flash_trigger("info", "Child organization unlinked."),
    )


@router.post("/{org_id}/archive/")
async def org_archive(
    org_id: str,
    user: AdminUser = Depends(get_admin_user),
    db=Depends(get_db),
):
    """Archive an organization (soft delete)."""
    org = await db.fetchrow("SELECT id, archived_at FROM organizations WHERE id = $1", org_id)
    if not org:
        raise HTTPException(status_code=404, detail="Organization not found")
    if org["archived_at"]:
        raise HTTPException(status_code=409, detail="Organization is already archived")
    await db.execute("UPDATE organizations SET archived_at = NOW() WHERE id = $1", org_id)
    return RedirectResponse(f"/admin/orgs/{org_id}/?flash=archived", status_code=303)


@router.post("/{org_id}/unarchive/")
async def org_unarchive(
    org_id: str,
    user: AdminUser = Depends(get_admin_user),
    db=Depends(get_db),
):
    """Restore an archived organization."""
    org = await db.fetchrow("SELECT id, archived_at FROM organizations WHERE id = $1", org_id)
    if not org:
        raise HTTPException(status_code=404, detail="Organization not found")
    if not org["archived_at"]:
        raise HTTPException(status_code=409, detail="Organization is not archived")
    await db.execute("UPDATE organizations SET archived_at = NULL WHERE id = $1", org_id)
    return RedirectResponse(f"/admin/orgs/{org_id}/?flash=unarchived", status_code=303)


@router.delete("/{org_id}/")
async def org_delete(
    org_id: str,
    request: Request,
    user: AdminUser = Depends(get_admin_user),
    db=Depends(get_db),
):
    """Hard delete an archived organization."""
    org = await db.fetchrow("SELECT id, archived_at FROM organizations WHERE id = $1", org_id)
    if not org:
        raise HTTPException(status_code=404, detail="Organization not found")
    if not org["archived_at"]:
        raise HTTPException(status_code=409, detail="Organization must be archived before deletion")
    try:
        async with db.transaction():
            await db.execute("DELETE FROM organization_acronyms WHERE organization_id = $1", org_id)
            await db.execute("DELETE FROM organization_names WHERE organization_id = $1", org_id)
            await db.execute("DELETE FROM organizations WHERE id = $1", org_id)
            await db.execute(
                "INSERT INTO deleted_entities (entity_type, entity_id) VALUES ('organization', $1)"
                " ON CONFLICT DO NOTHING",
                org_id,
            )
    except asyncpg.ForeignKeyViolationError:
        raise HTTPException(
            status_code=409,
            detail="Cannot delete: organization has related records (roles, etc.)",
        )
    if is_htmx(request):
        return Response(
            status_code=204,
            headers={"HX-Location": "/admin/orgs/?flash=deleted"},
        )
    return RedirectResponse("/admin/orgs/?flash=deleted", status_code=303)
