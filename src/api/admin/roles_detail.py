"""Inline editing routes for the role detail page (org, title, notes, boundary dates)."""

import asyncpg
from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from markupsafe import escape

from src.api.admin.deps import AdminUser, flash_trigger, get_admin_user, get_db, is_htmx
from src.api.admin.roles_shared import (
    _check_assignment_within_bounds,
    _get_role,
    _parse_date,
)

templates = Jinja2Templates(directory="src/templates")
router = APIRouter(prefix="/roles/{role_id}", tags=["admin-roles-detail"])


# ---------------------------------------------------------------------------
# Organization inline
# ---------------------------------------------------------------------------


@router.get("/inline/org/")
async def role_inline_org_get(
    role_id: str,
    request: Request,
    user: AdminUser = Depends(get_admin_user),
    db=Depends(get_db),
):
    """Return org read partial."""
    role = await _get_role(role_id, db)
    return templates.TemplateResponse(
        request, "admin/roles/partials/_org_read.html", {"role": role}
    )


@router.get("/inline/org/edit/")
async def role_inline_org_edit_get(
    role_id: str,
    request: Request,
    user: AdminUser = Depends(get_admin_user),
    db=Depends(get_db),
):
    """Return org typeahead form partial."""
    role = await _get_role(role_id, db)
    return templates.TemplateResponse(
        request, "admin/roles/partials/_org_form.html", {"role": role}
    )


@router.post("/inline/org/")
async def role_inline_org_post(
    role_id: str,
    request: Request,
    organization_id: str = Form(""),
    user: AdminUser = Depends(get_admin_user),
    db=Depends(get_db),
):
    """Save org change; return updated read partial."""
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
    await db.execute("UPDATE roles SET organization_id=$1 WHERE id=$2", resolved, role_id)
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
    user: AdminUser = Depends(get_admin_user),
    db=Depends(get_db),
):
    """Return title read partial."""
    role = await _get_role(role_id, db)
    return templates.TemplateResponse(
        request, "admin/roles/partials/_title_read.html", {"role": role}
    )


@router.get("/inline/title/edit/")
async def role_inline_title_edit_get(
    role_id: str,
    request: Request,
    user: AdminUser = Depends(get_admin_user),
    db=Depends(get_db),
):
    """Return title edit form partial."""
    role = await _get_role(role_id, db)
    return templates.TemplateResponse(
        request, "admin/roles/partials/_title_form.html", {"role": role}
    )


@router.post("/inline/title/")
async def role_inline_title_post(
    role_id: str,
    request: Request,
    title: str = Form(""),
    user: AdminUser = Depends(get_admin_user),
    db=Depends(get_db),
):
    """Save title; return updated read partial."""
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
            headers=flash_trigger(
                "error",
                f"A role named <strong>{escape(cleaned)}</strong>"
                " already exists for this organization.",
            ),
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
    user: AdminUser = Depends(get_admin_user),
    db=Depends(get_db),
):
    """Return notes read partial."""
    role = await _get_role(role_id, db)
    return templates.TemplateResponse(
        request, "admin/roles/partials/_notes_read.html", {"role": role}
    )


@router.get("/inline/notes/edit/")
async def role_inline_notes_edit_get(
    role_id: str,
    request: Request,
    user: AdminUser = Depends(get_admin_user),
    db=Depends(get_db),
):
    """Return notes edit form partial."""
    role = await _get_role(role_id, db)
    return templates.TemplateResponse(
        request, "admin/roles/partials/_notes_form.html", {"role": role}
    )


@router.post("/inline/notes/")
async def role_inline_notes_post(
    role_id: str,
    request: Request,
    notes: str = Form(""),
    user: AdminUser = Depends(get_admin_user),
    db=Depends(get_db),
):
    """Save notes; return updated read partial."""
    await _get_role(role_id, db)  # 404 check
    await db.execute("UPDATE roles SET notes=$1 WHERE id=$2", notes.strip() or None, role_id)
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
# Boundary dates inline
# ---------------------------------------------------------------------------


@router.get("/inline/dates/")
async def role_inline_dates_get(
    role_id: str,
    request: Request,
    user: AdminUser = Depends(get_admin_user),
    db=Depends(get_db),
):
    """Return boundary dates read partial."""
    role = await _get_role(role_id, db)
    return templates.TemplateResponse(
        request, "admin/roles/partials/_dates_read.html", {"role": role}
    )


@router.get("/inline/dates/edit/")
async def role_inline_dates_edit_get(
    role_id: str,
    request: Request,
    user: AdminUser = Depends(get_admin_user),
    db=Depends(get_db),
):
    """Return boundary dates edit form partial."""
    role = await _get_role(role_id, db)
    return templates.TemplateResponse(
        request,
        "admin/roles/partials/_dates_form.html",
        {
            "role": role,
            "established_on_input": (
                role["established_on"].isoformat() if role["established_on"] else ""
            ),
            "abolished_on_input": (
                role["abolished_on"].isoformat() if role["abolished_on"] else ""
            ),
        },
    )


@router.post("/inline/dates/")
async def role_inline_dates_post(
    role_id: str,
    request: Request,
    established_on: str = Form(""),
    abolished_on: str = Form(""),
    user: AdminUser = Depends(get_admin_user),
    db=Depends(get_db),
):
    """Save boundary dates; validate against existing assignments."""
    role = await _get_role(role_id, db)

    def _form_ctx(est_input: str, abol_input: str):
        return {
            "role": role,
            "established_on_input": est_input,
            "abolished_on_input": abol_input,
        }

    try:
        established_on_val = _parse_date(established_on)
        abolished_on_val = _parse_date(abolished_on)
    except ValueError:
        if not is_htmx(request):
            return RedirectResponse(f"/admin/roles/{role_id}/", status_code=303)
        return templates.TemplateResponse(
            request,
            "admin/roles/partials/_dates_form.html",
            _form_ctx(established_on, abolished_on),
            headers=flash_trigger("error", "Invalid date format. Use YYYY-MM-DD."),
        )

    if established_on_val and abolished_on_val and established_on_val > abolished_on_val:
        if not is_htmx(request):
            return RedirectResponse(f"/admin/roles/{role_id}/", status_code=303)
        return templates.TemplateResponse(
            request,
            "admin/roles/partials/_dates_form.html",
            _form_ctx(established_on, abolished_on),
            headers=flash_trigger("error", "Established date must be on or before abolished date."),
        )

    # Check existing active assignments
    assignments = await db.fetch(
        """SELECT start_date, end_date FROM role_assignments
           WHERE role_id = $1 AND archived_at IS NULL""",
        role_id,
    )
    violations = [
        ra
        for ra in assignments
        if _check_assignment_within_bounds(
            ra["start_date"], ra["end_date"], established_on_val, abolished_on_val
        )
    ]
    if violations:
        count = len(violations)
        msg = (
            f"{count} existing assignment{'s' if count > 1 else ''} "
            f"fall{'s' if count == 1 else ''} outside these boundaries."
        )
        if not is_htmx(request):
            return RedirectResponse(f"/admin/roles/{role_id}/", status_code=303)
        return templates.TemplateResponse(
            request,
            "admin/roles/partials/_dates_form.html",
            _form_ctx(established_on, abolished_on),
            headers=flash_trigger("error", msg),
        )

    await db.execute(
        "UPDATE roles SET established_on=$1, abolished_on=$2 WHERE id=$3",
        established_on_val,
        abolished_on_val,
        role_id,
    )
    role = await _get_role(role_id, db)
    if not is_htmx(request):
        return RedirectResponse(f"/admin/roles/{role_id}/", status_code=303)
    return templates.TemplateResponse(
        request,
        "admin/roles/partials/_dates_read.html",
        {"role": role},
        headers=flash_trigger("success", "Boundary dates saved."),
    )
