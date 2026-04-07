"""Inline editing routes for the role detail page."""

import datetime

import asyncpg
from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from markupsafe import escape

from src.api.admin.deps import AdminUser, check_auth, flash_trigger, get_admin_user, get_db, is_htmx
from src.core.db import generate_id

templates = Jinja2Templates(directory="src/templates")
router = APIRouter(prefix="/roles/{role_id}", tags=["admin-roles-detail"])


async def _get_role(role_id: str, db):
    """Fetch role with org display name, or raise 404."""
    row = await db.fetchrow(
        """SELECT r.id, r.title, r.notes, r.archived_at, r.created_at, r.updated_at,
                  r.organization_id AS org_id,
                  dn.display_name AS org_name
           FROM roles r
           LEFT JOIN v_org_display_names dn ON dn.organization_id = r.organization_id
           WHERE r.id = $1""",
        role_id,
    )
    if not row:
        raise HTTPException(status_code=404, detail="Role not found")
    return row


# ---------------------------------------------------------------------------
# Organization inline
# ---------------------------------------------------------------------------


@router.get("/inline/org/")
async def role_inline_org_get(
    role_id: str,
    request: Request,
    user: AdminUser | RedirectResponse = Depends(get_admin_user),
    db=Depends(get_db),
):
    """Return org read partial."""
    redirect, user = check_auth(user)
    if redirect:
        return redirect
    role = await _get_role(role_id, db)
    return templates.TemplateResponse(
        request, "admin/roles/partials/_org_read.html", {"role": role}
    )


@router.get("/inline/org/edit/")
async def role_inline_org_edit_get(
    role_id: str,
    request: Request,
    user: AdminUser | RedirectResponse = Depends(get_admin_user),
    db=Depends(get_db),
):
    """Return org typeahead form partial."""
    redirect, user = check_auth(user)
    if redirect:
        return redirect
    role = await _get_role(role_id, db)
    return templates.TemplateResponse(
        request, "admin/roles/partials/_org_form.html", {"role": role}
    )


@router.post("/inline/org/")
async def role_inline_org_post(
    role_id: str,
    request: Request,
    organization_id: str = Form(""),
    user: AdminUser | RedirectResponse = Depends(get_admin_user),
    db=Depends(get_db),
):
    """Save org change; return updated read partial."""
    redirect, user = check_auth(user)
    if redirect:
        return redirect
    role = await _get_role(role_id, db)
    resolved = organization_id.strip()
    if not resolved:
        if not is_htmx(request):
            return RedirectResponse(f"/admin/roles/{role_id}/", status_code=303)
        return templates.TemplateResponse(
            request,
            "admin/roles/partials/_org_form.html",
            {"role": role},
            headers=flash_trigger("error", "Organization is required."),
        )
    exists = await db.fetchval("SELECT id FROM organizations WHERE id=$1", resolved)
    if not exists:
        if not is_htmx(request):
            return RedirectResponse(f"/admin/roles/{role_id}/", status_code=303)
        return templates.TemplateResponse(
            request,
            "admin/roles/partials/_org_form.html",
            {"role": role},
            headers=flash_trigger("error", "Organization not found."),
        )
    await db.execute(
        "UPDATE roles SET organization_id=$1 WHERE id=$2", resolved, role_id
    )
    role = await _get_role(role_id, db)
    if not is_htmx(request):
        return RedirectResponse(f"/admin/roles/{role_id}/", status_code=303)
    return templates.TemplateResponse(
        request,
        "admin/roles/partials/_org_read.html",
        {"role": role},
        headers=flash_trigger(
            "success",
            f"Organization set to <strong>{escape(role['org_name'])}</strong>.",
        ),
    )


# ---------------------------------------------------------------------------
# Title inline
# ---------------------------------------------------------------------------


@router.get("/inline/title/")
async def role_inline_title_get(
    role_id: str,
    request: Request,
    user: AdminUser | RedirectResponse = Depends(get_admin_user),
    db=Depends(get_db),
):
    """Return title read partial."""
    redirect, user = check_auth(user)
    if redirect:
        return redirect
    role = await _get_role(role_id, db)
    return templates.TemplateResponse(
        request, "admin/roles/partials/_title_read.html", {"role": role}
    )


@router.get("/inline/title/edit/")
async def role_inline_title_edit_get(
    role_id: str,
    request: Request,
    user: AdminUser | RedirectResponse = Depends(get_admin_user),
    db=Depends(get_db),
):
    """Return title edit form partial."""
    redirect, user = check_auth(user)
    if redirect:
        return redirect
    role = await _get_role(role_id, db)
    return templates.TemplateResponse(
        request, "admin/roles/partials/_title_form.html", {"role": role}
    )


@router.post("/inline/title/")
async def role_inline_title_post(
    role_id: str,
    request: Request,
    title: str = Form(""),
    user: AdminUser | RedirectResponse = Depends(get_admin_user),
    db=Depends(get_db),
):
    """Save title; return updated read partial."""
    redirect, user = check_auth(user)
    if redirect:
        return redirect
    role = await _get_role(role_id, db)
    cleaned = title.strip()
    if not cleaned:
        if not is_htmx(request):
            return RedirectResponse(f"/admin/roles/{role_id}/", status_code=303)
        return templates.TemplateResponse(
            request,
            "admin/roles/partials/_title_form.html",
            {"role": role},
            headers=flash_trigger("error", "Title cannot be empty."),
        )
    try:
        await db.execute("UPDATE roles SET title=$1 WHERE id=$2", cleaned, role_id)
    except asyncpg.UniqueViolationError:
        if not is_htmx(request):
            return RedirectResponse(f"/admin/roles/{role_id}/", status_code=303)
        return templates.TemplateResponse(
            request,
            "admin/roles/partials/_title_form.html",
            {"role": role},
            headers=flash_trigger("error", f"A role named <strong>{escape(cleaned)}</strong> already exists for this organization."),
        )
    role = await _get_role(role_id, db)
    if not is_htmx(request):
        return RedirectResponse(f"/admin/roles/{role_id}/", status_code=303)
    return templates.TemplateResponse(
        request,
        "admin/roles/partials/_title_read.html",
        {"role": role},
        headers=flash_trigger("success", "Title saved."),
    )


# ---------------------------------------------------------------------------
# Notes inline
# ---------------------------------------------------------------------------


@router.get("/inline/notes/")
async def role_inline_notes_get(
    role_id: str,
    request: Request,
    user: AdminUser | RedirectResponse = Depends(get_admin_user),
    db=Depends(get_db),
):
    """Return notes read partial."""
    redirect, user = check_auth(user)
    if redirect:
        return redirect
    role = await _get_role(role_id, db)
    return templates.TemplateResponse(
        request, "admin/roles/partials/_notes_read.html", {"role": role}
    )


@router.get("/inline/notes/edit/")
async def role_inline_notes_edit_get(
    role_id: str,
    request: Request,
    user: AdminUser | RedirectResponse = Depends(get_admin_user),
    db=Depends(get_db),
):
    """Return notes edit form partial."""
    redirect, user = check_auth(user)
    if redirect:
        return redirect
    role = await _get_role(role_id, db)
    return templates.TemplateResponse(
        request, "admin/roles/partials/_notes_form.html", {"role": role}
    )


@router.post("/inline/notes/")
async def role_inline_notes_post(
    role_id: str,
    request: Request,
    notes: str = Form(""),
    user: AdminUser | RedirectResponse = Depends(get_admin_user),
    db=Depends(get_db),
):
    """Save notes; return updated read partial."""
    redirect, user = check_auth(user)
    if redirect:
        return redirect
    await _get_role(role_id, db)  # 404 check
    await db.execute(
        "UPDATE roles SET notes=$1 WHERE id=$2", notes.strip() or None, role_id
    )
    role = await _get_role(role_id, db)
    if not is_htmx(request):
        return RedirectResponse(f"/admin/roles/{role_id}/", status_code=303)
    return templates.TemplateResponse(
        request,
        "admin/roles/partials/_notes_read.html",
        {"role": role},
        headers=flash_trigger("success", "Notes saved."),
    )


# ---------------------------------------------------------------------------
# Assignments inline
# ---------------------------------------------------------------------------


def _parse_date(value: str) -> datetime.date | None:
    """Parse ISO date string, return None if empty."""
    value = value.strip()
    if not value:
        return None
    return datetime.date.fromisoformat(value)


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


@router.get("/assignments/new-row/")
async def assignment_new_row(
    role_id: str,
    request: Request,
    user: AdminUser | RedirectResponse = Depends(get_admin_user),
    db=Depends(get_db),
):
    """Return blank inline assignment form row."""
    redirect, user = check_auth(user)
    if redirect:
        return redirect
    await _get_role(role_id, db)  # 404 check
    return templates.TemplateResponse(
        request,
        "admin/roles/partials/_assignment_form_row.html",
        {
            "role_id": role_id,
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
    user: AdminUser | RedirectResponse = Depends(get_admin_user),
    db=Depends(get_db),
):
    """Create a new role assignment (inline HTMX path)."""
    redirect, user = check_auth(user)
    if redirect:
        return redirect
    await _get_role(role_id, db)  # 404 check

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

    ra_id = generate_id()
    try:
        await db.execute(
            """INSERT INTO role_assignments (id, person_id, role_id, is_current, start_date, end_date)
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
        {"assignments": assignments},
        headers=flash_trigger("success", "Assignment added."),
    )
