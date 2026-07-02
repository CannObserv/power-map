"""Admin views for people."""

import asyncpg
from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request
from fastapi.responses import RedirectResponse, Response
from fastapi.templating import Jinja2Templates

from src.api.admin._events_shared import fetch_entity_events
from src.api.admin.deps import (
    AdminUser,
    build_parts_summary,
    flash_trigger,
    get_admin_user,
    get_db,
    is_htmx,
    resolve_query_flash,
)
from src.api.admin.entity_lookup import search_entities
from src.api.admin.pagination import PAGE_SIZE_DEFAULT, PAGE_SIZE_MAX, PAGE_SIZE_MIN
from src.api.admin.people_queries import query_people_rows
from src.core.db import generate_id

templates = Jinja2Templates(directory="src/templates")
router = APIRouter(prefix="/people", tags=["admin-people"])

_FLASH_MESSAGES: dict[str, tuple[str, str]] = {
    "archived": ("success", "Person archived."),
    "unarchived": ("success", "Person unarchived."),
    "deleted": ("success", "Person deleted."),
}

_READING_TYPES = ("reading", "romanization", "mrz")


def _interleave_visuals_with_readings(rows: list) -> list:
    """Reorder person_names rows: visual row, then its reading children, repeat.

    Input is expected to already be sorted by `is_canonical DESC, name_type,
    name` (the SQL ORDER BY supplies this). The interleave reads as:
      - Walk the sorted list once, separating visual rows from children
        (children = rows with non-NULL reading_of_id).
      - Emit each visual row, then any children whose reading_of_id matches
        — sorted by (name_type, name) for stable per-group ordering.
      - Orphaned children (parent absent from the visible set, e.g.
        filtered out by visibility) trail at the end so they're never lost.

    Each output row is a dict (not a Record) so we can attach the derived
    `parts_summary` field without losing the SQL columns.
    """
    enriched: list[dict] = [
        {
            **dict(r),
            "parts_summary": build_parts_summary(
                r["pnp_family_names"],
                r["pnp_given_names"],
                r["pnp_additional_names"],
            ),
        }
        for r in rows
    ]
    visuals: list = []
    children_by_parent: dict[str, list] = {}
    for r in enriched:
        if r["reading_of_id"]:
            children_by_parent.setdefault(r["reading_of_id"], []).append(r)
        else:
            visuals.append(r)

    def _child_sort_key(r):
        return (r["name_type"], r["name"])

    ordered: list = []
    for v in visuals:
        ordered.append(v)
        for c in sorted(children_by_parent.get(v["id"], []), key=_child_sort_key):
            ordered.append(c)
    visible_visual_ids = {v["id"] for v in visuals}
    for parent_id, kids in children_by_parent.items():
        if parent_id not in visible_visual_ids:
            ordered.extend(sorted(kids, key=_child_sort_key))
    return ordered


@router.get("/")
async def people_list(
    request: Request,
    q: str = "",
    status: str = "active",
    page: int = Query(1, ge=1),
    page_size: int = Query(PAGE_SIZE_DEFAULT, ge=PAGE_SIZE_MIN, le=PAGE_SIZE_MAX),
    flash: str | None = Query(None),
    user: AdminUser = Depends(get_admin_user),
    db=Depends(get_db),
):
    """List people with search and status filter."""

    rows, count, pctx = await query_people_rows(
        db, q=q, status=status, page=page, page_size=page_size
    )

    flash_msg, resp_headers = resolve_query_flash(request, _FLASH_MESSAGES, flash)

    ctx = {
        "user": user,
        "active_section": "people",
        "people": rows,
        "q": q,
        "status": status,
        "page_size": page_size,
        "total": count,
        "flash_msg": flash_msg,
        **pctx,
    }
    template = (
        "admin/people/_region.html"
        if request.headers.get("HX-Request") and not request.headers.get("HX-Boosted")
        else "admin/people/list.html"
    )
    return templates.TemplateResponse(request, template, ctx, headers=resp_headers)


@router.get("/new/")
async def person_new_form(
    request: Request,
    user: AdminUser = Depends(get_admin_user),
    db=Depends(get_db),
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
    results = await search_entities(db, "person", q)
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
    flash: str | None = Query(None),
    show_historical: bool = Query(False),
):
    """Person detail view.

    `show_historical=1` reveals legal_only / hidden rows on the names table;
    default keeps them collapsed behind the toggle (issue #123 Phase 2a Task 3).
    """

    person = await db.fetchrow("SELECT * FROM people WHERE id = $1", person_id)
    if not person:
        raise HTTPException(status_code=404, detail="Person not found")

    display_name_row = await db.fetchrow(
        "SELECT display_name FROM v_person_display_names WHERE person_id = $1", person_id
    )
    display_name = display_name_row["display_name"] if display_name_row else None

    # visibility-allowlist (issue #121): the admin detail page is the
    # disclosure point — when show_historical=True, surface all rows so the
    # editor can manage legal_only / hidden / deadname names. Default hides
    # them behind a toggle that displays the historical count.
    # Phase 2c enrichment: also surface each row's `reading_of_name` (parent
    # row's `name`) and `reading_child_count` (cascade-impact for delete
    # confirm). Visual rows are then interleaved with their reading
    # children in Python so the table groups them.
    visibility_filter = "" if show_historical else " AND pn.visibility = 'public'"
    raw_names = await db.fetch(
        "SELECT pn.*, parent.name AS reading_of_name,"
        "       COALESCE(c.cnt, 0) AS reading_child_count,"
        "       pnp.given_names      AS pnp_given_names,"
        "       pnp.family_names     AS pnp_family_names,"
        "       pnp.additional_names AS pnp_additional_names"
        " FROM person_names pn"
        " LEFT JOIN person_names parent ON parent.id = pn.reading_of_id"
        " LEFT JOIN person_name_parts pnp ON pnp.person_name_id = pn.id"
        " LEFT JOIN LATERAL ("
        "   SELECT COUNT(*) AS cnt FROM person_names ch"
        "   WHERE ch.reading_of_id = pn.id"
        " ) c ON TRUE"
        f" WHERE pn.person_id = $1{visibility_filter}"
        " ORDER BY pn.is_canonical DESC, pn.name_type, pn.name",
        person_id,
    )
    names = _interleave_visuals_with_readings(raw_names)
    if show_historical:
        # All rows already in `names`; derive the count without a second query.
        historical_count = sum(1 for n in names if n["visibility"] != "public")
    else:
        historical_count = await db.fetchval(
            "SELECT COUNT(*) FROM person_names WHERE person_id = $1 AND visibility != 'public'",
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
        """SELECT ea.id, ea.address_type, ea.display_name, ea.valid_from, ea.valid_until,
                  a.id AS address_id, a.standardized, a.address_line_1, a.address_line_2,
                  a.city, a.region, a.postal_code, a.country
           FROM entity_addresses ea JOIN addresses a ON a.id = ea.address_id
           WHERE ea.entity_type = 'person' AND ea.entity_id = $1
           ORDER BY (ea.valid_until IS NOT NULL AND ea.valid_until < CURRENT_DATE),
                    ea.valid_from DESC NULLS LAST""",
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

    events = await fetch_entity_events(person_id, "person", db)

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
            "show_historical": show_historical,
            "historical_count": historical_count,
            "email_contacts": email_contacts,
            "phone_contacts": phone_contacts,
            "addresses": addresses,
            "links": links,
            "identifiers": identifiers,
            "role_assignments": role_assignments,
            "events": events,
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
        async with db.transaction():
            # visibility-allowlist (issue #121): hard-delete must remove ALL name
            # rows regardless of visibility.
            await db.execute("DELETE FROM person_names WHERE person_id = $1", person_id)
            await db.execute("DELETE FROM people WHERE id = $1", person_id)
            await db.execute(
                "INSERT INTO deleted_entities (entity_type, entity_id) VALUES ('person', $1)"
                " ON CONFLICT DO NOTHING",
                person_id,
            )
    except asyncpg.ForeignKeyViolationError:
        raise HTTPException(
            status_code=409,
            detail="Cannot delete: person has related records (role assignments, etc.)",
        )
    if is_htmx(request):
        return Response(
            status_code=204,
            headers={"HX-Location": "/admin/people/?flash=deleted"},
        )
    return RedirectResponse("/admin/people/?flash=deleted", status_code=303)


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
