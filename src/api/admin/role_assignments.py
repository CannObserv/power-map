"""Admin views for role assignments."""

import datetime

import asyncpg
from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request
from fastapi.responses import RedirectResponse, Response
from fastapi.templating import Jinja2Templates

from src.api.admin.deps import (
    AdminUser,
    flash_trigger,
    get_admin_user,
    get_db,
    is_htmx,
    resolve_query_flash,
)
from src.api.admin.pagination import PAGE_SIZE_DEFAULT, PAGE_SIZE_MAX, PAGE_SIZE_MIN
from src.api.admin.role_assignments_queries import VALID_STATUSES, query_role_assignments_rows
from src.core.citations import CITABLE_FIELDS
from src.core.db import generate_id
from src.core.org_lifecycle import (
    AssignmentOutsideOrgLifespan,
    check_assignment_lifespan,
    lifespan_error_message,
)

templates = Jinja2Templates(directory="src/templates")
router = APIRouter(prefix="/role-assignments", tags=["admin-role-assignments"])


_FLASH_MESSAGES: dict[str, tuple[str, str]] = {
    "archived": ("success", "Assignment archived."),
    "unarchived": ("success", "Assignment unarchived."),
    "deleted": ("success", "Assignment deleted."),
}


def _parse_date(value: str) -> datetime.date | None:
    """Parse an ISO date string to datetime.date, or return None if empty.

    Raises HTTPException(400) on malformed input.
    """
    if not value:
        return None
    try:
        return datetime.date.fromisoformat(value)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid date: {value!r}") from exc


async def _fetch_people(db):
    """Fetch active people for select options."""
    return await db.fetch(
        """SELECT p.id, pn.display_name AS name
           FROM people p
           LEFT JOIN v_person_display_names pn ON pn.person_id = p.id
           WHERE p.archived_at IS NULL
           ORDER BY pn.display_name NULLS LAST"""
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


@router.get("/")
async def ra_list(
    request: Request,
    q: str = "",
    status: str = "active",
    page: int = Query(1, ge=1),
    page_size: int = Query(PAGE_SIZE_DEFAULT, ge=PAGE_SIZE_MIN, le=PAGE_SIZE_MAX),
    flash: str | None = Query(None),
    user: AdminUser = Depends(get_admin_user),
    db=Depends(get_db),
):
    """List role assignments with search and status filter."""
    if status not in VALID_STATUSES:
        status = "active"
    rows, count, pctx, hidden_matches = await query_role_assignments_rows(
        db, q=q, status=status, page=page, page_size=page_size
    )

    flash_msg, resp_headers = resolve_query_flash(request, _FLASH_MESSAGES, flash)

    ctx = {
        "user": user,
        "active_section": "role_assignments",
        "assignments": rows,
        "q": q,
        "status": status,
        "page_size": page_size,
        "total": count,
        "hidden_matches": hidden_matches,
        "flash_msg": flash_msg,
        **pctx,
    }
    template = (
        "admin/role_assignments/_region.html"
        if is_htmx(request)
        else "admin/role_assignments/list.html"
    )
    return templates.TemplateResponse(request, template, ctx, headers=resp_headers)


@router.get("/new/")
async def ra_new_form(
    request: Request,
    user: AdminUser = Depends(get_admin_user),
    db=Depends(get_db),
):
    """New role assignment form."""
    people = await _fetch_people(db)
    roles = await _fetch_roles(db)
    return templates.TemplateResponse(
        request,
        "admin/role_assignments/form.html",
        {
            "user": user,
            "active_section": "role_assignments",
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
    user: AdminUser = Depends(get_admin_user),
    db=Depends(get_db),
):
    """Create a new role assignment."""

    is_current_bool = is_current == "true"
    start_date_val = _parse_date(start_date)
    end_date_val = _parse_date(end_date)
    ra_id = generate_id()

    try:
        await check_assignment_lifespan(
            db,
            role_id,
            is_current=is_current_bool,
            start_date=start_date_val,
            end_date=end_date_val,
        )
    except AssignmentOutsideOrgLifespan as exc:
        people = await _fetch_people(db)
        roles = await _fetch_roles(db)
        return templates.TemplateResponse(
            request,
            "admin/role_assignments/form.html",
            {
                "user": user,
                "active_section": "role_assignments",
                "people": people,
                "roles": roles,
                "error": lifespan_error_message(exc),
                "form_person_id": person_id,
                "form_role_id": role_id,
                "form_is_current": is_current_bool,
                "form_start_date": start_date,
                "form_end_date": end_date,
                "form_notes": notes,
            },
            status_code=200,
        )

    try:
        # Savepoint so a CHECK violation aborts only this write, not the ambient
        # transaction — keeps the except-block re-render queries usable when the
        # request runs inside a wrapping transaction (test harness, #288).
        async with db.transaction():
            await db.execute(
                """INSERT INTO role_assignments
                   (id, person_id, role_id, is_current, start_date, end_date, notes)
                   VALUES ($1, $2, $3, $4, $5, $6, $7)""",
                ra_id,
                person_id,
                role_id,
                is_current_bool,
                start_date_val,
                end_date_val,
                notes or None,
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
    flash: str | None = Query(None),
    user: AdminUser = Depends(get_admin_user),
    db=Depends(get_db),
):
    """Role assignment detail view."""

    ra = await _get_ra(ra_id, db)
    flash_msg, resp_headers = resolve_query_flash(request, _FLASH_MESSAGES, flash)

    email_contacts = await db.fetch(
        "SELECT * FROM contact_methods"
        " WHERE entity_type = 'role_assignment' AND entity_id = $1 AND contact_type = 'email'"
        " ORDER BY value",
        ra_id,
    )
    phone_contacts = await db.fetch(
        "SELECT * FROM contact_methods"
        " WHERE entity_type = 'role_assignment' AND entity_id = $1 AND contact_type = 'phone'"
        " ORDER BY value",
        ra_id,
    )
    links = await db.fetch(
        """SELECT l.*, lt.display_name AS link_type_name, lt.is_social
           FROM links l JOIN link_types lt ON lt.id = l.link_type_id
           WHERE l.entity_type = 'role_assignment' AND l.entity_id = $1
           ORDER BY lt.display_name, l.url""",
        ra_id,
    )
    identifiers = await db.fetch(
        """SELECT i.*, eit.display_name AS type_name, eit.full_name AS type_full_name
           FROM identifiers i
           JOIN entity_identifier_types eit ON eit.id = i.entity_identifier_type_id
           WHERE i.entity_id = $1 AND eit.entity_type = 'role_assignment'
           ORDER BY eit.display_name, i.value""",
        ra_id,
    )

    citations = await db.fetch(
        "SELECT * FROM citations WHERE entity_type='role_assignment' AND entity_id=$1"
        " AND archived_at IS NULL ORDER BY created_at DESC, id DESC",
        ra_id,
    )

    return templates.TemplateResponse(
        request,
        "admin/role_assignments/detail.html",
        {
            "user": user,
            "active_section": "role_assignments",
            "ra": ra,
            "email_contacts": email_contacts,
            "phone_contacts": phone_contacts,
            "links": links,
            "identifiers": identifiers,
            "citations": citations,
            "entity_id": ra_id,
            "cit_base": f"/role-assignments/{ra_id}/citations",
            "citable_fields": sorted(CITABLE_FIELDS["role_assignment"]),
            "flash_msg": flash_msg,
        },
        headers=resp_headers,
    )


async def _get_ra(ra_id: str, db):
    """Fetch enriched RA row for detail/partial rendering, or raise 404."""
    row = await db.fetchrow(
        """SELECT ra.id, ra.is_current, ra.start_date, ra.end_date, ra.archived_at,
                  ra.created_at, ra.notes,
                  p.id AS person_id,
                  pn.display_name AS person_name,
                  r.id AS role_id, r.title AS role_title,
                  o.id AS org_id,
                  dn.display_name AS org_name
           FROM role_assignments ra
           JOIN people p ON p.id = ra.person_id
           LEFT JOIN v_person_display_names pn ON pn.person_id = p.id
           JOIN roles r ON r.id = ra.role_id
           JOIN organizations o ON o.id = r.organization_id
           LEFT JOIN v_org_display_names dn ON dn.organization_id = o.id
           WHERE ra.id = $1""",
        ra_id,
    )
    if not row:
        raise HTTPException(status_code=404, detail="Role assignment not found")
    return row


# ---------------------------------------------------------------------------
# is_current toggle (auto-save)
# ---------------------------------------------------------------------------


@router.post("/{ra_id}/inline/is_current/")
async def ra_inline_is_current(
    ra_id: str,
    request: Request,
    is_current: str = Form(""),
    user: AdminUser = Depends(get_admin_user),
    db=Depends(get_db),
):
    """Toggle is_current; on CHECK violation, re-render prior state + error flash."""
    new_val = is_current == "true"
    if new_val:
        ra = await _get_ra(ra_id, db)
        try:
            await check_assignment_lifespan(
                db,
                ra["role_id"],
                is_current=True,
                start_date=ra["start_date"],
                end_date=ra["end_date"],
            )
        except AssignmentOutsideOrgLifespan as exc:
            if not is_htmx(request):
                raise HTTPException(status_code=400, detail=lifespan_error_message(exc)) from exc
            return templates.TemplateResponse(
                request,
                "admin/role_assignments/partials/_is_current_toggle.html",
                {"ra": ra},
                headers=flash_trigger("error", lifespan_error_message(exc)),
            )
    try:
        # Savepoint: a CHECK violation aborts only this write (see create above).
        async with db.transaction():
            updated = await db.fetchval(
                "UPDATE role_assignments SET is_current=$1 WHERE id=$2 RETURNING id",
                new_val,
                ra_id,
            )
    except asyncpg.exceptions.CheckViolationError as exc:
        if not is_htmx(request):
            raise HTTPException(
                status_code=400,
                detail="Current assignments cannot have an end date.",
            ) from exc
        ra = await _get_ra(ra_id, db)
        return templates.TemplateResponse(
            request,
            "admin/role_assignments/partials/_is_current_toggle.html",
            {"ra": ra},
            headers=flash_trigger(
                "error",
                "Current assignments cannot have an end date. Clear the end date first.",
            ),
        )
    if not updated:
        raise HTTPException(status_code=404, detail="Role assignment not found")
    if not is_htmx(request):
        return RedirectResponse(f"/admin/role-assignments/{ra_id}/", status_code=303)
    ra = await _get_ra(ra_id, db)
    return templates.TemplateResponse(
        request,
        "admin/role_assignments/partials/_is_current_toggle.html",
        {"ra": ra},
        headers=flash_trigger(
            "success",
            "Marked as current." if new_val else "Marked as former.",
        ),
    )


# ---------------------------------------------------------------------------
# Dates inline
# ---------------------------------------------------------------------------


@router.get("/{ra_id}/inline/dates/")
async def ra_inline_dates_get(
    ra_id: str,
    request: Request,
    user: AdminUser = Depends(get_admin_user),
    db=Depends(get_db),
):
    """Return dates read partial."""
    ra = await _get_ra(ra_id, db)
    return templates.TemplateResponse(
        request, "admin/role_assignments/partials/_dates_read.html", {"ra": ra}
    )


@router.get("/{ra_id}/inline/dates/edit/")
async def ra_inline_dates_edit_get(
    ra_id: str,
    request: Request,
    user: AdminUser = Depends(get_admin_user),
    db=Depends(get_db),
):
    """Return dates edit form partial."""
    ra = await _get_ra(ra_id, db)
    return templates.TemplateResponse(
        request,
        "admin/role_assignments/partials/_dates_form.html",
        {"ra": ra, "error": None, "start_date_value": None, "end_date_value": None},
    )


@router.post("/{ra_id}/inline/dates/")
async def ra_inline_dates_post(
    ra_id: str,
    request: Request,
    start_date: str = Form(""),
    end_date: str = Form(""),
    user: AdminUser = Depends(get_admin_user),
    db=Depends(get_db),
):
    """Save dates; on CHECK violation, re-render form with inline error."""
    start_val = _parse_date(start_date)
    end_val = _parse_date(end_date)
    ra = await _get_ra(ra_id, db)
    try:
        # is_current=False: this form can't change currency, so it must not
        # enforce it — a current-on-ended row would otherwise get a circular
        # error on its own repair path (and on unrelated start_date edits).
        # chk_current_no_end_date still yields the "mark as former first"
        # message when an end date is posted for a current row.
        await check_assignment_lifespan(
            db,
            ra["role_id"],
            is_current=False,
            start_date=start_val,
            end_date=end_val,
        )
    except AssignmentOutsideOrgLifespan as exc:
        if not is_htmx(request):
            raise HTTPException(status_code=400, detail=lifespan_error_message(exc)) from exc
        return templates.TemplateResponse(
            request,
            "admin/role_assignments/partials/_dates_form.html",
            {
                "ra": ra,
                "error": lifespan_error_message(exc),
                "start_date_value": start_date,
                "end_date_value": end_date,
            },
        )
    try:
        # Savepoint: a CHECK violation aborts only this write (see create above).
        async with db.transaction():
            updated = await db.fetchval(
                "UPDATE role_assignments SET start_date=$1, end_date=$2 WHERE id=$3 RETURNING id",
                start_val,
                end_val,
                ra_id,
            )
    except asyncpg.exceptions.CheckViolationError as exc:
        if not is_htmx(request):
            raise HTTPException(
                status_code=400,
                detail="Current assignments cannot have an end date.",
            ) from exc
        ra = await _get_ra(ra_id, db)
        return templates.TemplateResponse(
            request,
            "admin/role_assignments/partials/_dates_form.html",
            {
                "ra": ra,
                "error": "Current assignments cannot have an end date. Mark as former first.",
                "start_date_value": start_date,
                "end_date_value": end_date,
            },
        )
    if not updated:
        raise HTTPException(status_code=404, detail="Role assignment not found")
    if not is_htmx(request):
        return RedirectResponse(f"/admin/role-assignments/{ra_id}/", status_code=303)
    ra = await _get_ra(ra_id, db)
    return templates.TemplateResponse(
        request,
        "admin/role_assignments/partials/_dates_read.html",
        {"ra": ra},
        headers=flash_trigger("success", "Dates saved."),
    )


# ---------------------------------------------------------------------------
# Notes inline
# ---------------------------------------------------------------------------


@router.get("/{ra_id}/inline/notes/")
async def ra_inline_notes_get(
    ra_id: str,
    request: Request,
    user: AdminUser = Depends(get_admin_user),
    db=Depends(get_db),
):
    """Return notes read partial."""
    ra = await _get_ra(ra_id, db)
    return templates.TemplateResponse(
        request, "admin/role_assignments/partials/_notes_read.html", {"ra": ra}
    )


@router.get("/{ra_id}/inline/notes/edit/")
async def ra_inline_notes_edit_get(
    ra_id: str,
    request: Request,
    user: AdminUser = Depends(get_admin_user),
    db=Depends(get_db),
):
    """Return notes edit form partial."""
    ra = await _get_ra(ra_id, db)
    return templates.TemplateResponse(
        request, "admin/role_assignments/partials/_notes_form.html", {"ra": ra}
    )


@router.post("/{ra_id}/inline/notes/")
async def ra_inline_notes_post(
    ra_id: str,
    request: Request,
    notes: str = Form(""),
    user: AdminUser = Depends(get_admin_user),
    db=Depends(get_db),
):
    """Save notes; return read partial."""
    updated = await db.fetchval(
        "UPDATE role_assignments SET notes=$1 WHERE id=$2 RETURNING id",
        notes.strip() or None,
        ra_id,
    )
    if not updated:
        raise HTTPException(status_code=404, detail="Role assignment not found")
    if not is_htmx(request):
        return RedirectResponse(f"/admin/role-assignments/{ra_id}/", status_code=303)
    ra = await _get_ra(ra_id, db)
    return templates.TemplateResponse(
        request,
        "admin/role_assignments/partials/_notes_read.html",
        {"ra": ra},
        headers=flash_trigger("success", "Notes saved."),
    )


@router.post("/{ra_id}/archive/")
async def ra_archive(
    ra_id: str,
    user: AdminUser = Depends(get_admin_user),
    db=Depends(get_db),
):
    """Archive a role assignment (soft delete). Returns 409 if already archived."""

    updated = await db.fetchval(
        "UPDATE role_assignments SET archived_at = NOW() "
        "WHERE id = $1 AND archived_at IS NULL RETURNING id",
        ra_id,
    )
    if updated:
        return RedirectResponse(f"/admin/role-assignments/{ra_id}/?flash=archived", status_code=303)
    exists = await db.fetchval("SELECT 1 FROM role_assignments WHERE id = $1", ra_id)
    if not exists:
        raise HTTPException(status_code=404, detail="Role assignment not found")
    raise HTTPException(status_code=409, detail="Role assignment is already archived")


@router.post("/{ra_id}/unarchive/")
async def ra_unarchive(
    ra_id: str,
    user: AdminUser = Depends(get_admin_user),
    db=Depends(get_db),
):
    """Restore an archived role assignment. Returns 409 if not archived."""
    ra = await db.fetchrow("SELECT id, archived_at FROM role_assignments WHERE id = $1", ra_id)
    if not ra:
        raise HTTPException(status_code=404, detail="Role assignment not found")
    if not ra["archived_at"]:
        raise HTTPException(status_code=409, detail="Role assignment is not archived")
    await db.execute("UPDATE role_assignments SET archived_at = NULL WHERE id = $1", ra_id)
    return RedirectResponse(f"/admin/role-assignments/{ra_id}/?flash=unarchived", status_code=303)


@router.delete("/{ra_id}/")
async def ra_delete(
    ra_id: str,
    request: Request,
    user: AdminUser = Depends(get_admin_user),
    db=Depends(get_db),
):
    """Hard delete an archived role assignment."""

    ra = await db.fetchrow("SELECT id, archived_at FROM role_assignments WHERE id = $1", ra_id)
    if not ra:
        raise HTTPException(status_code=404, detail="Role assignment not found")
    if not ra["archived_at"]:
        raise HTTPException(
            status_code=409, detail="Role assignment must be archived before deletion"
        )

    async with db.transaction():
        await db.execute("DELETE FROM role_assignments WHERE id = $1", ra_id)
        # Tombstone (issue #277): emit a 'deleted' signal for subscribers.
        await db.execute(
            "INSERT INTO deleted_entities (entity_type, entity_id)"
            " VALUES ('role_assignment', $1) ON CONFLICT DO NOTHING",
            ra_id,
        )
    if is_htmx(request):
        return Response(
            status_code=204,
            headers={"HX-Location": "/admin/role-assignments/?flash=deleted"},
        )
    return RedirectResponse("/admin/role-assignments/?flash=deleted", status_code=303)
