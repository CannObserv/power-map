"""Admin views for people."""

import asyncpg
from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from src.api.admin.deps import (
    AdminUser,
    escape_like,
    flash_trigger,
    get_admin_user,
    get_db,
    is_htmx,
    resolve_query_flash,
)
from src.api.admin.org_dups import get_org_dup_count
from src.api.admin.pagination import pagination_context
from src.api.admin.people_dups import get_person_dup_count
from src.core.db import generate_id

templates = Jinja2Templates(directory="src/templates")
router = APIRouter(prefix="/people", tags=["admin-people"])

_FLASH_MESSAGES: dict[str, tuple[str, str]] = {
    "archived": ("success", "Person archived."),
    "unarchived": ("success", "Person unarchived."),
}


@router.get("/")
async def people_list(
    request: Request,
    q: str = "",
    status: str = "active",
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=10, le=500),
    user: AdminUser = Depends(get_admin_user),
    db=Depends(get_db),
    org_dup_count: int = Depends(get_org_dup_count),
    person_dup_count: int = Depends(get_person_dup_count),
):
    """List people with search and status filter."""

    conditions = []
    params: list = []

    if status == "active":
        conditions.append("p.archived_at IS NULL")
    elif status == "archived":
        conditions.append("p.archived_at IS NOT NULL")

    if q:
        params.append(f"%{escape_like(q)}%")
        conditions.append(f"n.display_name ILIKE ${len(params)} ESCAPE '\\'")

    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
    count_params = params[:]

    count = await db.fetchval(
        f"""SELECT count(DISTINCT p.id)
            FROM people p
            LEFT JOIN v_person_display_names n ON n.person_id = p.id
            {where}""",
        *count_params,
    )

    pctx = pagination_context(page, count, page_size)
    offset = (pctx["page"] - 1) * page_size
    list_params = params + [page_size, offset]

    rows = await db.fetch(
        f"""SELECT p.id, p.archived_at, p.created_at,
                   n.display_name AS canonical_name
            FROM people p
            LEFT JOIN v_person_display_names n ON n.person_id = p.id
            {where}
            ORDER BY n.display_name NULLS LAST
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
        "org_dup_count": org_dup_count,
        "person_dup_count": person_dup_count,
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
    user: AdminUser = Depends(get_admin_user),
    db=Depends(get_db),
    org_dup_count: int = Depends(get_org_dup_count),
    person_dup_count: int = Depends(get_person_dup_count),
):
    """New person form."""
    return templates.TemplateResponse(
        request,
        "admin/people/form.html",
        {
            "user": user,
            "active_section": "people",
            "person": None,
            "canonical_name": "",
            "org_dup_count": org_dup_count,
            "person_dup_count": person_dup_count,
        },
    )


@router.post("/new/")
async def person_create(
    request: Request,
    name: str = Form(...),
    personal_pronouns: str = Form(""),
    notes: str = Form(""),
    user: AdminUser = Depends(get_admin_user),
    db=Depends(get_db),
):
    """Create a new person."""
    person_id = generate_id()
    await db.execute(
        "INSERT INTO people (id, personal_pronouns, notes) VALUES ($1, $2, $3)",
        person_id,
        personal_pronouns or None,
        notes or None,
    )
    await db.execute(
        "INSERT INTO person_names (id, person_id, name, is_canonical) VALUES ($1, $2, $3, TRUE)",
        generate_id(),
        person_id,
        name,
    )
    return RedirectResponse(f"/admin/people/{person_id}/", status_code=303)


@router.get("/search/")
async def people_search(
    request: Request,
    q: str = "",
    user: AdminUser = Depends(get_admin_user),
    db=Depends(get_db),
):
    """Typeahead search — returns HTML fragment of matching people."""
    results = []
    if q.strip():
        results = await db.fetch(
            """SELECT p.id, pn.display_name
               FROM people p
               LEFT JOIN v_person_display_names pn ON pn.person_id = p.id
               WHERE p.archived_at IS NULL
                 AND pn.display_name ILIKE $1 ESCAPE '\\'
               ORDER BY pn.display_name NULLS LAST
               LIMIT 20""",
            f"%{escape_like(q.strip())}%",
        )
    return templates.TemplateResponse(
        request,
        "admin/people/partials/_search_results.html",
        {"results": results},
    )


@router.get("/{person_id}/")
async def person_detail(
    person_id: str,
    request: Request,
    user: AdminUser = Depends(get_admin_user),
    db=Depends(get_db),
    org_dup_count: int = Depends(get_org_dup_count),
    person_dup_count: int = Depends(get_person_dup_count),
    flash: str | None = Query(None),
):
    """Person detail view."""

    person = await db.fetchrow("SELECT * FROM people WHERE id = $1", person_id)
    if not person:
        raise HTTPException(status_code=404, detail="Person not found")

    display_name_row = await db.fetchrow(
        "SELECT display_name FROM v_person_display_names WHERE person_id = $1", person_id
    )
    display_name = display_name_row["display_name"] if display_name_row else None

    # visibility-allowlist (issue #121): admin detail page is the disclosure
    # point — surfaces all names (incl. legal_only / hidden / deadname) so the
    # editor can manage them. Future UI gates these behind a "Show legal/
    # historical names" toggle; the SQL must return everything.
    names = await db.fetch(
        "SELECT * FROM person_names WHERE person_id = $1"
        " ORDER BY is_canonical DESC, name_type, name",
        person_id,
    )
    contacts = await db.fetch(
        "SELECT * FROM contact_methods WHERE entity_type = 'person' AND entity_id = $1"
        " ORDER BY contact_type, value",
        person_id,
    )
    email_contacts = [c for c in contacts if c["contact_type"] == "email"]
    phone_contacts = [c for c in contacts if c["contact_type"] == "phone"]

    addresses = await db.fetch(
        """SELECT ea.id, ea.address_type, ea.display_name,
                  a.id AS address_id, a.standardized, a.address_line_1, a.address_line_2,
                  a.city, a.region, a.postal_code, a.country
           FROM entity_addresses ea JOIN addresses a ON a.id = ea.address_id
           WHERE ea.entity_type = 'person' AND ea.entity_id = $1""",
        person_id,
    )
    links = await db.fetch(
        """SELECT l.*, lt.display_name AS link_type_name, lt.is_social
           FROM links l JOIN link_types lt ON lt.id = l.link_type_id
           WHERE l.entity_type = 'person' AND l.entity_id = $1
           ORDER BY lt.is_social DESC, lt.display_name, l.url""",
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
                  r.id AS role_id, r.title AS role_title,
                  o.id AS org_id, dn.display_name AS org_name
           FROM role_assignments ra
           JOIN roles r ON r.id = ra.role_id
           JOIN organizations o ON o.id = r.organization_id
           LEFT JOIN v_org_display_names dn ON dn.organization_id = o.id
           WHERE ra.person_id = $1
           ORDER BY ra.is_current DESC, ra.start_date DESC NULLS LAST""",
        person_id,
    )

    flash_msg, resp_headers = resolve_query_flash(request, _FLASH_MESSAGES, flash)
    return templates.TemplateResponse(
        request,
        "admin/people/detail.html",
        {
            "user": user,
            "active_section": "people",
            "person": person,
            "display_name": display_name,
            "names": names,
            "email_contacts": email_contacts,
            "phone_contacts": phone_contacts,
            "addresses": addresses,
            "links": links,
            "identifiers": identifiers,
            "role_assignments": role_assignments,
            "org_dup_count": org_dup_count,
            "person_dup_count": person_dup_count,
            "flash_msg": flash_msg,
        },
        headers=resp_headers,
    )


@router.post("/{person_id}/archive/")
async def person_archive(
    person_id: str,
    user: AdminUser = Depends(get_admin_user),
    db=Depends(get_db),
):
    """Archive a person (soft delete)."""
    person = await db.fetchrow("SELECT id, archived_at FROM people WHERE id = $1", person_id)
    if not person:
        raise HTTPException(status_code=404, detail="Person not found")
    if person["archived_at"]:
        raise HTTPException(status_code=409, detail="Person is already archived")
    await db.execute("UPDATE people SET archived_at = NOW() WHERE id = $1", person_id)
    return RedirectResponse(f"/admin/people/{person_id}/?flash=archived", status_code=303)


@router.post("/{person_id}/unarchive/")
async def person_unarchive(
    person_id: str,
    user: AdminUser = Depends(get_admin_user),
    db=Depends(get_db),
):
    """Restore an archived person."""
    person = await db.fetchrow("SELECT id, archived_at FROM people WHERE id = $1", person_id)
    if not person:
        raise HTTPException(status_code=404, detail="Person not found")
    if not person["archived_at"]:
        raise HTTPException(status_code=409, detail="Person is not archived")
    await db.execute("UPDATE people SET archived_at = NULL WHERE id = $1", person_id)
    return RedirectResponse(f"/admin/people/{person_id}/?flash=unarchived", status_code=303)


@router.delete("/{person_id}/")
async def person_delete(
    person_id: str,
    request: Request,
    user: AdminUser = Depends(get_admin_user),
    db=Depends(get_db),
):
    """Hard delete an archived person."""
    person = await db.fetchrow("SELECT id, archived_at FROM people WHERE id = $1", person_id)
    if not person:
        raise HTTPException(status_code=404, detail="Person not found")
    if not person["archived_at"]:
        raise HTTPException(status_code=409, detail="Person must be archived before deletion")
    try:
        # visibility-allowlist (issue #121): hard-delete must remove ALL name
        # rows regardless of visibility.
        await db.execute("DELETE FROM person_names WHERE person_id = $1", person_id)
        await db.execute("DELETE FROM people WHERE id = $1", person_id)
    except asyncpg.ForeignKeyViolationError:
        raise HTTPException(
            status_code=409,
            detail="Cannot delete: person has related records (role assignments, etc.)",
        )
    return HTMLResponse(content="", status_code=200)


@router.get("/{person_id}/inline/notes/")
async def person_notes_read(
    person_id: str,
    request: Request,
    user: AdminUser = Depends(get_admin_user),
    db=Depends(get_db),
):
    """Notes read partial."""
    person = await db.fetchrow("SELECT id, notes, archived_at FROM people WHERE id = $1", person_id)
    if not person:
        raise HTTPException(status_code=404, detail="Person not found")
    return templates.TemplateResponse(
        request,
        "admin/people/partials/_notes_read.html",
        {"person": person},
    )


@router.get("/{person_id}/inline/notes/edit/")
async def person_notes_edit(
    person_id: str,
    request: Request,
    user: AdminUser = Depends(get_admin_user),
    db=Depends(get_db),
):
    """Notes edit partial."""
    person = await db.fetchrow("SELECT id, notes FROM people WHERE id = $1", person_id)
    if not person:
        raise HTTPException(status_code=404, detail="Person not found")
    return templates.TemplateResponse(
        request,
        "admin/people/partials/_notes_form.html",
        {"person": person},
    )


@router.post("/{person_id}/inline/notes/")
async def person_notes_save(
    person_id: str,
    request: Request,
    notes: str = Form(""),
    user: AdminUser = Depends(get_admin_user),
    db=Depends(get_db),
):
    """Save notes and return read partial."""
    person = await db.fetchrow("SELECT id FROM people WHERE id = $1", person_id)
    if not person:
        raise HTTPException(status_code=404, detail="Person not found")
    saved = notes.strip() or None
    await db.execute("UPDATE people SET notes = $1 WHERE id = $2", saved, person_id)
    updated = await db.fetchrow(
        "SELECT id, notes, archived_at FROM people WHERE id = $1", person_id
    )
    if not is_htmx(request):
        return RedirectResponse(f"/admin/people/{person_id}/", status_code=303)
    return templates.TemplateResponse(
        request,
        "admin/people/partials/_notes_read.html",
        {"person": updated},
        headers=flash_trigger("success", "Notes saved."),
    )


@router.get("/{person_id}/inline/pronouns/")
async def person_pronouns_read(
    person_id: str,
    request: Request,
    user: AdminUser = Depends(get_admin_user),
    db=Depends(get_db),
):
    """Pronouns read partial."""
    person = await db.fetchrow(
        "SELECT id, personal_pronouns, archived_at FROM people WHERE id = $1", person_id
    )
    if not person:
        raise HTTPException(status_code=404, detail="Person not found")
    return templates.TemplateResponse(
        request,
        "admin/people/partials/_pronouns_read.html",
        {"person": person},
    )


@router.get("/{person_id}/inline/pronouns/edit/")
async def person_pronouns_edit(
    person_id: str,
    request: Request,
    user: AdminUser = Depends(get_admin_user),
    db=Depends(get_db),
):
    """Pronouns edit partial."""
    person = await db.fetchrow("SELECT id, personal_pronouns FROM people WHERE id = $1", person_id)
    if not person:
        raise HTTPException(status_code=404, detail="Person not found")
    return templates.TemplateResponse(
        request,
        "admin/people/partials/_pronouns_form.html",
        {"person": person},
    )


@router.post("/{person_id}/inline/pronouns/")
async def person_pronouns_save(
    person_id: str,
    request: Request,
    personal_pronouns: str = Form(""),
    user: AdminUser = Depends(get_admin_user),
    db=Depends(get_db),
):
    """Save pronouns and return read partial."""
    person = await db.fetchrow("SELECT id FROM people WHERE id = $1", person_id)
    if not person:
        raise HTTPException(status_code=404, detail="Person not found")
    saved = personal_pronouns.strip() or None
    await db.execute("UPDATE people SET personal_pronouns = $1 WHERE id = $2", saved, person_id)
    updated = await db.fetchrow(
        "SELECT id, personal_pronouns, archived_at FROM people WHERE id = $1", person_id
    )
    if not is_htmx(request):
        return RedirectResponse(f"/admin/people/{person_id}/", status_code=303)
    return templates.TemplateResponse(
        request,
        "admin/people/partials/_pronouns_read.html",
        {"person": updated},
        headers=flash_trigger("success", "Pronouns saved."),
    )


