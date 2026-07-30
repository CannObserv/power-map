"""Inline assignment CRUD routes for the person detail page."""

import datetime

import asyncpg
from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates

from src.api.admin._citations_shared import citation_count_lateral
from src.api.admin.deps import AdminUser, flash_trigger, get_admin_user, get_db, is_htmx
from src.core.db import generate_id
from src.core.org_lifecycle import (
    AssignmentOutsideOrgLifespan,
    check_assignment_lifespan,
    lifespan_error_message,
)

templates = Jinja2Templates(directory="src/templates")
router = APIRouter(prefix="/people/{person_id}/assignments", tags=["admin-people-assignments"])


def _parse_date(value: str) -> datetime.date | None:
    """Parse ISO date string, return None if empty."""
    value = value.strip()
    if not value:
        return None
    return datetime.date.fromisoformat(value)


async def _get_person_or_404(person_id: str, db):
    """Fetch person row or raise 404."""
    row = await db.fetchrow("SELECT id FROM people WHERE id = $1", person_id)
    if not row:
        raise HTTPException(status_code=404, detail="Person not found")
    return row


async def fetch_person_assignments(person_id: str, db) -> list:
    """Fetch all assignments for a person, sorted for display."""
    return await db.fetch(
        f"""SELECT ra.id, ra.is_current, ra.start_date, ra.end_date, ra.archived_at, ra.notes,
                  cc_j.citation_count,
                  r.id AS role_id, r.title AS role_title,
                  o.id AS org_id,
                  dn.display_name AS org_name
           FROM role_assignments ra
           JOIN roles r ON r.id = ra.role_id
           JOIN organizations o ON o.id = r.organization_id
           LEFT JOIN v_org_display_names dn ON dn.organization_id = o.id
           {citation_count_lateral("role_assignment", "ra.id")}
           WHERE ra.person_id = $1
           ORDER BY ra.is_current DESC, ra.start_date DESC NULLS LAST""",
        person_id,
    )


async def _get_assignment(assignment_id: str, person_id: str, db):
    """Fetch a single assignment with role/org info, or raise 404."""
    row = await db.fetchrow(
        f"""SELECT ra.id, ra.is_current, ra.start_date, ra.end_date, ra.archived_at, ra.notes,
                  cc_j.citation_count,
                  r.id AS role_id, r.title AS role_title,
                  o.id AS org_id,
                  dn.display_name AS org_name
           FROM role_assignments ra
           JOIN roles r ON r.id = ra.role_id
           JOIN organizations o ON o.id = r.organization_id
           LEFT JOIN v_org_display_names dn ON dn.organization_id = o.id
           {citation_count_lateral("role_assignment", "ra.id")}
           WHERE ra.id = $1 AND ra.person_id = $2""",
        assignment_id,
        person_id,
    )
    if not row:
        raise HTTPException(status_code=404, detail="Assignment not found")
    return row


@router.get("/new-row/")
async def assignment_new_row(
    person_id: str,
    request: Request,
    user: AdminUser = Depends(get_admin_user),
    db=Depends(get_db),
):
    """Return blank inline assignment form row."""
    await _get_person_or_404(person_id, db)
    return templates.TemplateResponse(
        request,
        "admin/people/partials/_assignment_form_row.html",
        {
            "person_id": person_id,
            "start_date_input": "",
            "end_date_input": "",
            "is_current_input": False,
        },
    )


@router.post("/")
async def assignment_create(
    person_id: str,
    request: Request,
    role_id: str = Form(""),
    start_date: str = Form(""),
    end_date: str = Form(""),
    is_current: str = Form(""),
    user: AdminUser = Depends(get_admin_user),
    db=Depends(get_db),
):
    """Create a new role assignment (inline HTMX path)."""
    await _get_person_or_404(person_id, db)

    role_id_val = role_id.strip()
    is_current_val = bool(is_current)

    def _form_error(msg: str):
        return templates.TemplateResponse(
            request,
            "admin/people/partials/_assignment_form_row.html",
            {
                "person_id": person_id,
                "start_date_input": start_date,
                "end_date_input": end_date,
                "is_current_input": is_current_val,
            },
            headers={
                **flash_trigger("error", msg),
                "HX-Retarget": "#person-assignment-row-new",
                "HX-Reswap": "outerHTML",
            },
        )

    if not role_id_val:
        if not is_htmx(request):
            return RedirectResponse(f"/admin/people/{person_id}/", status_code=303)
        return _form_error("Role is required.")

    try:
        start_date_val = _parse_date(start_date)
        end_date_val = _parse_date(end_date)
    except ValueError:
        if not is_htmx(request):
            return RedirectResponse(f"/admin/people/{person_id}/", status_code=303)
        return _form_error("Invalid date format. Use YYYY-MM-DD.")

    try:
        await check_assignment_lifespan(
            db,
            role_id_val,
            is_current=is_current_val,
            start_date=start_date_val,
            end_date=end_date_val,
        )
    except AssignmentOutsideOrgLifespan as exc:
        if not is_htmx(request):
            return RedirectResponse(f"/admin/people/{person_id}/", status_code=303)
        return _form_error(lifespan_error_message(exc))

    ra_id = generate_id()
    try:
        await db.execute(
            """INSERT INTO role_assignments
               (id, person_id, role_id, is_current, start_date, end_date)
               VALUES ($1, $2, $3, $4, $5, $6)""",
            ra_id,
            person_id,
            role_id_val,
            is_current_val,
            start_date_val,
            end_date_val,
        )
    except asyncpg.CheckViolationError:
        if not is_htmx(request):
            return RedirectResponse(f"/admin/people/{person_id}/", status_code=303)
        return _form_error("Current assignments cannot have an end date.")
    except asyncpg.UniqueViolationError:
        if not is_htmx(request):
            return RedirectResponse(f"/admin/people/{person_id}/", status_code=303)
        return _form_error("An assignment for this role with this start date already exists.")
    except asyncpg.ForeignKeyViolationError:
        if not is_htmx(request):
            return RedirectResponse(f"/admin/people/{person_id}/", status_code=303)
        return _form_error("Role not found.")

    if not is_htmx(request):
        return RedirectResponse(f"/admin/people/{person_id}/", status_code=303)

    assignments = await fetch_person_assignments(person_id, db)
    return templates.TemplateResponse(
        request,
        "admin/people/partials/_assignment_rows.html",
        {"assignments": assignments, "person_id": person_id},
        headers=flash_trigger("success", "Assignment added."),
    )


@router.get("/{assignment_id}/read-row/")
async def assignment_read_row(
    person_id: str,
    assignment_id: str,
    request: Request,
    user: AdminUser = Depends(get_admin_user),
    db=Depends(get_db),
):
    """Return read partial for a single assignment row."""
    ra = await _get_assignment(assignment_id, person_id, db)
    return templates.TemplateResponse(
        request,
        "admin/people/partials/_assignment_row.html",
        {"ra": ra, "person_id": person_id},
    )


@router.get("/{assignment_id}/edit-row/")
async def assignment_edit_row_get(
    person_id: str,
    assignment_id: str,
    request: Request,
    user: AdminUser = Depends(get_admin_user),
    db=Depends(get_db),
):
    """Return edit form partial for a single assignment row."""
    ra = await _get_assignment(assignment_id, person_id, db)
    if ra["archived_at"]:
        raise HTTPException(status_code=409, detail="Cannot edit an archived assignment")
    return templates.TemplateResponse(
        request,
        "admin/people/partials/_assignment_edit_row.html",
        {
            "ra": ra,
            "person_id": person_id,
            "start_date_input": ra["start_date"].isoformat() if ra["start_date"] else "",
            "end_date_input": ra["end_date"].isoformat() if ra["end_date"] else "",
            "is_current_input": ra["is_current"],
        },
    )


@router.post("/{assignment_id}/edit-row/")
async def assignment_edit_row_post(
    person_id: str,
    assignment_id: str,
    request: Request,
    start_date: str = Form(""),
    end_date: str = Form(""),
    is_current: str = Form(""),
    user: AdminUser = Depends(get_admin_user),
    db=Depends(get_db),
):
    """Save assignment edits; return full sorted tbody."""
    ra = await _get_assignment(assignment_id, person_id, db)
    if ra["archived_at"]:
        raise HTTPException(status_code=409, detail="Cannot edit an archived assignment")
    is_current_val = bool(is_current)

    def _error_ctx():
        return {
            "ra": ra,
            "person_id": person_id,
            "start_date_input": start_date,
            "end_date_input": end_date,
            "is_current_input": is_current_val,
        }

    try:
        start_date_val = _parse_date(start_date)
        end_date_val = _parse_date(end_date)
    except ValueError:
        if not is_htmx(request):
            return RedirectResponse(f"/admin/people/{person_id}/", status_code=303)
        return templates.TemplateResponse(
            request,
            "admin/people/partials/_assignment_edit_row.html",
            _error_ctx(),
            headers={
                **flash_trigger("error", "Invalid date format. Use YYYY-MM-DD."),
                "HX-Retarget": f"#person-assignment-row-{assignment_id}",
                "HX-Reswap": "outerHTML",
            },
        )

    try:
        await check_assignment_lifespan(
            db,
            ra["role_id"],
            is_current=is_current_val,
            start_date=start_date_val,
            end_date=end_date_val,
        )
    except AssignmentOutsideOrgLifespan as exc:
        if not is_htmx(request):
            return RedirectResponse(f"/admin/people/{person_id}/", status_code=303)
        return templates.TemplateResponse(
            request,
            "admin/people/partials/_assignment_edit_row.html",
            _error_ctx(),
            headers={
                **flash_trigger("error", lifespan_error_message(exc)),
                "HX-Retarget": f"#person-assignment-row-{assignment_id}",
                "HX-Reswap": "outerHTML",
            },
        )

    try:
        await db.execute(
            """UPDATE role_assignments
               SET is_current=$1, start_date=$2, end_date=$3
               WHERE id=$4""",
            is_current_val,
            start_date_val,
            end_date_val,
            assignment_id,
        )
    except asyncpg.CheckViolationError:
        if not is_htmx(request):
            return RedirectResponse(f"/admin/people/{person_id}/", status_code=303)
        return templates.TemplateResponse(
            request,
            "admin/people/partials/_assignment_edit_row.html",
            _error_ctx(),
            headers={
                **flash_trigger("error", "Current assignments cannot have an end date."),
                "HX-Retarget": f"#person-assignment-row-{assignment_id}",
                "HX-Reswap": "outerHTML",
            },
        )

    if not is_htmx(request):
        return RedirectResponse(f"/admin/people/{person_id}/", status_code=303)

    assignments = await fetch_person_assignments(person_id, db)
    return templates.TemplateResponse(
        request,
        "admin/people/partials/_assignment_rows.html",
        {"assignments": assignments, "person_id": person_id},
        headers=flash_trigger("success", "Assignment saved."),
    )


@router.post("/{assignment_id}/archive/")
async def assignment_archive(
    person_id: str,
    assignment_id: str,
    request: Request,
    user: AdminUser = Depends(get_admin_user),
    db=Depends(get_db),
):
    """Archive a role assignment from person detail. Returns 409 if already archived."""
    ra = await _get_assignment(assignment_id, person_id, db)
    if ra["archived_at"]:
        raise HTTPException(status_code=409, detail="Role assignment is already archived")
    await db.execute("UPDATE role_assignments SET archived_at = NOW() WHERE id=$1", assignment_id)
    if not is_htmx(request):
        return RedirectResponse(f"/admin/people/{person_id}/", status_code=303)
    assignments = await fetch_person_assignments(person_id, db)
    return templates.TemplateResponse(
        request,
        "admin/people/partials/_assignment_rows.html",
        {"assignments": assignments, "person_id": person_id},
        headers=flash_trigger("success", "Assignment archived."),
    )
