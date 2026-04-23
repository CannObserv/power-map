"""Inline assignment CRUD routes for the role detail page."""

import asyncpg
from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates

from src.api.admin.deps import AdminUser, flash_trigger, get_admin_user, get_db, is_htmx
from src.api.admin.roles_shared import (
    _check_assignment_within_bounds,
    _get_role,
    _parse_date,
)
from src.core.db import generate_id

templates = Jinja2Templates(directory="src/templates")
router = APIRouter(prefix="/roles/{role_id}", tags=["admin-roles-assignments"])


async def fetch_role_assignments(role_id: str, db) -> list:
    """Fetch all assignments for a role, sorted for display."""
    return await db.fetch(
        """SELECT ra.id, ra.is_current, ra.start_date, ra.end_date, ra.archived_at,
                  p.id AS person_id,
                  pn.display_name AS person_name
           FROM role_assignments ra
           JOIN people p ON p.id = ra.person_id
           LEFT JOIN v_person_display_names pn ON pn.person_id = p.id
           WHERE ra.role_id = $1
           ORDER BY ra.is_current DESC, ra.start_date DESC NULLS LAST""",
        role_id,
    )


async def _get_assignment(assignment_id: str, role_id: str, db):
    """Fetch a single assignment with person name, or raise 404."""
    row = await db.fetchrow(
        """SELECT ra.id, ra.is_current, ra.start_date, ra.end_date, ra.archived_at,
                  p.id AS person_id,
                  pn.display_name AS person_name
           FROM role_assignments ra
           JOIN people p ON p.id = ra.person_id
           LEFT JOIN v_person_display_names pn ON pn.person_id = p.id
           WHERE ra.id = $1 AND ra.role_id = $2""",
        assignment_id, role_id,
    )
    if not row:
        raise HTTPException(status_code=404, detail="Assignment not found")
    return row


@router.get("/assignments/new-row/")
async def assignment_new_row(
    role_id: str,
    request: Request,
    user: AdminUser = Depends(get_admin_user),
    db=Depends(get_db),
):
    """Return blank inline assignment form row."""
    role = await _get_role(role_id, db)
    return templates.TemplateResponse(
        request,
        "admin/roles/partials/_assignment_form_row.html",
        {
            "role_id": role_id,
            "role": role,
            "start_date_input": "",
            "end_date_input": "",
            "is_current_input": False,
        },
    )


@router.post("/assignments/")
async def assignment_create(
    role_id: str,
    request: Request,
    person_id: str = Form(""),
    start_date: str = Form(""),
    end_date: str = Form(""),
    is_current: str = Form(""),
    user: AdminUser = Depends(get_admin_user),
    db=Depends(get_db),
):
    """Create a new role assignment (inline HTMX path)."""
    role = await _get_role(role_id, db)

    person_id_val = person_id.strip()
    is_current_val = bool(is_current)

    # Validate person_id
    if not person_id_val:
        if not is_htmx(request):
            return RedirectResponse(f"/admin/roles/{role_id}/", status_code=303)
        return templates.TemplateResponse(
            request,
            "admin/roles/partials/_assignment_form_row.html",
            {
                "role_id": role_id,
                "role": role,
                "start_date_input": start_date,
                "end_date_input": end_date,
                "is_current_input": is_current_val,
            },
            headers={
                **flash_trigger("error", "Person is required."),
                "HX-Retarget": "#assignment-row-new",
                "HX-Reswap": "outerHTML",
            },
        )

    try:
        start_date_val = _parse_date(start_date)
        end_date_val = _parse_date(end_date)
    except ValueError:
        if not is_htmx(request):
            return RedirectResponse(f"/admin/roles/{role_id}/", status_code=303)
        return templates.TemplateResponse(
            request,
            "admin/roles/partials/_assignment_form_row.html",
            {
                "role_id": role_id,
                "role": role,
                "start_date_input": start_date,
                "end_date_input": end_date,
                "is_current_input": is_current_val,
            },
            headers={
                **flash_trigger("error", "Invalid date format. Use YYYY-MM-DD."),
                "HX-Retarget": "#assignment-row-new",
                "HX-Reswap": "outerHTML",
            },
        )

    bound_err = _check_assignment_within_bounds(
        start_date_val, end_date_val,
        role["established_on"], role["abolished_on"],
    )
    if bound_err:
        if not is_htmx(request):
            return RedirectResponse(f"/admin/roles/{role_id}/", status_code=303)
        return templates.TemplateResponse(
            request,
            "admin/roles/partials/_assignment_form_row.html",
            {
                "role_id": role_id,
                "role": role,
                "start_date_input": start_date,
                "end_date_input": end_date,
                "is_current_input": is_current_val,
            },
            headers={
                **flash_trigger("error", bound_err),
                "HX-Retarget": "#assignment-row-new",
                "HX-Reswap": "outerHTML",
            },
        )

    ra_id = generate_id()
    try:
        await db.execute(
            """INSERT INTO role_assignments
               (id, person_id, role_id, is_current, start_date, end_date)
               VALUES ($1, $2, $3, $4, $5, $6)""",
            ra_id, person_id_val, role_id, is_current_val, start_date_val, end_date_val,
        )
    except asyncpg.CheckViolationError:
        if not is_htmx(request):
            return RedirectResponse(f"/admin/roles/{role_id}/", status_code=303)
        return templates.TemplateResponse(
            request,
            "admin/roles/partials/_assignment_form_row.html",
            {
                "role_id": role_id,
                "role": role,
                "start_date_input": start_date,
                "end_date_input": end_date,
                "is_current_input": is_current_val,
            },
            headers={
                **flash_trigger("error", "Current assignments cannot have an end date."),
                "HX-Retarget": "#assignment-row-new",
                "HX-Reswap": "outerHTML",
            },
        )
    except asyncpg.UniqueViolationError:
        if not is_htmx(request):
            return RedirectResponse(f"/admin/roles/{role_id}/", status_code=303)
        return templates.TemplateResponse(
            request,
            "admin/roles/partials/_assignment_form_row.html",
            {
                "role_id": role_id,
                "role": role,
                "start_date_input": start_date,
                "end_date_input": end_date,
                "is_current_input": is_current_val,
            },
            headers={
                **flash_trigger(
                    "error",
                    "An assignment for this person with this start date already exists.",
                ),
                "HX-Retarget": "#assignment-row-new",
                "HX-Reswap": "outerHTML",
            },
        )

    if not is_htmx(request):
        return RedirectResponse(f"/admin/roles/{role_id}/", status_code=303)

    assignments = await fetch_role_assignments(role_id, db)
    return templates.TemplateResponse(
        request,
        "admin/roles/partials/_assignment_rows.html",
        {"assignments": assignments, "role_id": role_id},
        headers=flash_trigger("success", "Assignment added."),
    )


# ---------------------------------------------------------------------------
# Assignment read-row / edit-row
# ---------------------------------------------------------------------------


@router.get("/assignments/{assignment_id}/read-row/")
async def assignment_read_row(
    role_id: str,
    assignment_id: str,
    request: Request,
    user: AdminUser = Depends(get_admin_user),
    db=Depends(get_db),
):
    """Return read partial for a single assignment row."""
    ra = await _get_assignment(assignment_id, role_id, db)
    return templates.TemplateResponse(
        request,
        "admin/roles/partials/_assignment_row.html",
        {"ra": ra, "role_id": role_id},
    )


@router.get("/assignments/{assignment_id}/edit-row/")
async def assignment_edit_row_get(
    role_id: str,
    assignment_id: str,
    request: Request,
    user: AdminUser = Depends(get_admin_user),
    db=Depends(get_db),
):
    """Return edit form partial for a single assignment row."""
    ra = await _get_assignment(assignment_id, role_id, db)
    if ra["archived_at"]:
        raise HTTPException(status_code=409, detail="Cannot edit an archived assignment")
    role = await _get_role(role_id, db)
    return templates.TemplateResponse(
        request,
        "admin/roles/partials/_assignment_edit_row.html",
        {
            "ra": ra,
            "role_id": role_id,
            "role": role,
            "start_date_input": ra["start_date"].isoformat() if ra["start_date"] else "",
            "end_date_input": ra["end_date"].isoformat() if ra["end_date"] else "",
            "is_current_input": ra["is_current"],
        },
    )


@router.post("/assignments/{assignment_id}/edit-row/")
async def assignment_edit_row_post(
    role_id: str,
    assignment_id: str,
    request: Request,
    start_date: str = Form(""),
    end_date: str = Form(""),
    is_current: str = Form(""),
    user: AdminUser = Depends(get_admin_user),
    db=Depends(get_db),
):
    """Save assignment edits; return full sorted tbody."""
    ra = await _get_assignment(assignment_id, role_id, db)
    if ra["archived_at"]:
        raise HTTPException(status_code=409, detail="Cannot edit an archived assignment")
    role = await _get_role(role_id, db)
    is_current_val = bool(is_current)

    def _error_ctx():
        return {
            "ra": ra,
            "role_id": role_id,
            "role": role,
            "start_date_input": start_date,
            "end_date_input": end_date,
            "is_current_input": is_current_val,
        }

    try:
        start_date_val = _parse_date(start_date)
        end_date_val = _parse_date(end_date)
    except ValueError:
        if not is_htmx(request):
            return RedirectResponse(f"/admin/roles/{role_id}/", status_code=303)
        return templates.TemplateResponse(
            request,
            "admin/roles/partials/_assignment_edit_row.html",
            _error_ctx(),
            headers={
                **flash_trigger("error", "Invalid date format. Use YYYY-MM-DD."),
                "HX-Retarget": f"#assignment-row-{assignment_id}",
                "HX-Reswap": "outerHTML",
            },
        )

    bound_err = _check_assignment_within_bounds(
        start_date_val, end_date_val,
        role["established_on"], role["abolished_on"],
    )
    if bound_err:
        if not is_htmx(request):
            return RedirectResponse(f"/admin/roles/{role_id}/", status_code=303)
        return templates.TemplateResponse(
            request,
            "admin/roles/partials/_assignment_edit_row.html",
            _error_ctx(),
            headers={
                **flash_trigger("error", bound_err),
                "HX-Retarget": f"#assignment-row-{assignment_id}",
                "HX-Reswap": "outerHTML",
            },
        )

    try:
        await db.execute(
            """UPDATE role_assignments
               SET is_current=$1, start_date=$2, end_date=$3
               WHERE id=$4""",
            is_current_val, start_date_val, end_date_val, assignment_id,
        )
    except asyncpg.CheckViolationError:
        if not is_htmx(request):
            return RedirectResponse(f"/admin/roles/{role_id}/", status_code=303)
        return templates.TemplateResponse(
            request,
            "admin/roles/partials/_assignment_edit_row.html",
            _error_ctx(),
            headers={
                **flash_trigger("error", "Current assignments cannot have an end date."),
                "HX-Retarget": f"#assignment-row-{assignment_id}",
                "HX-Reswap": "outerHTML",
            },
        )
    except asyncpg.UniqueViolationError:
        if not is_htmx(request):
            return RedirectResponse(f"/admin/roles/{role_id}/", status_code=303)
        return templates.TemplateResponse(
            request,
            "admin/roles/partials/_assignment_edit_row.html",
            _error_ctx(),
            headers={
                **flash_trigger(
                    "error",
                    "An assignment for this person with this start date already exists.",
                ),
                "HX-Retarget": f"#assignment-row-{assignment_id}",
                "HX-Reswap": "outerHTML",
            },
        )

    if not is_htmx(request):
        return RedirectResponse(f"/admin/roles/{role_id}/", status_code=303)

    assignments = await fetch_role_assignments(role_id, db)
    return templates.TemplateResponse(
        request,
        "admin/roles/partials/_assignment_rows.html",
        {"assignments": assignments, "role_id": role_id},
        headers=flash_trigger("success", "Assignment saved."),
    )


@router.post("/assignments/{assignment_id}/archive/")
async def assignment_archive(
    role_id: str,
    assignment_id: str,
    request: Request,
    user: AdminUser = Depends(get_admin_user),
    db=Depends(get_db),
):
    """Archive a role assignment from role detail. Returns 409 if already archived."""
    ra = await _get_assignment(assignment_id, role_id, db)
    if ra["archived_at"]:
        raise HTTPException(
            status_code=409, detail="Role assignment is already archived"
        )
    await db.execute(
        "UPDATE role_assignments SET archived_at = NOW() WHERE id=$1", assignment_id
    )
    if not is_htmx(request):
        return RedirectResponse(f"/admin/roles/{role_id}/", status_code=303)
    assignments = await fetch_role_assignments(role_id, db)
    return templates.TemplateResponse(
        request,
        "admin/roles/partials/_assignment_rows.html",
        {"assignments": assignments, "role_id": role_id},
        headers=flash_trigger("success", "Assignment archived."),
    )
