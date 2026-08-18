"""Inline editing routes for the role detail page (org, title, role type, notes, dates)."""

import asyncpg
from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from markupsafe import escape

from src.api.admin.deps import AdminUser, flash_trigger, get_admin_user, get_db, is_htmx, with_flash
from src.api.admin.roles_shared import (
    _check_assignment_within_bounds,
    _get_role,
    _parse_date,
    fetch_role_types,
    positioned_at_large_error,
    positionless_seat_error,
)
from src.core.role_title import synthesize_role_title

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
            return RedirectResponse(
                with_flash(f"/admin/roles/{role_id}/", "invalid"), status_code=303
            )
        return templates.TemplateResponse(
            request,
            "admin/roles/partials/_org_form.html",
            {"role": role},
            headers=flash_trigger("warning", "Organization is required."),
        )
    exists = await db.fetchval("SELECT id FROM organizations WHERE id=$1", resolved)
    if not exists:
        if not is_htmx(request):
            return RedirectResponse(
                with_flash(f"/admin/roles/{role_id}/", "invalid"), status_code=303
            )
        return templates.TemplateResponse(
            request,
            "admin/roles/partials/_org_form.html",
            {"role": role},
            headers=flash_trigger("warning", "Organization not found."),
        )
    await db.execute("UPDATE roles SET organization_id=$1 WHERE id=$2", resolved, role_id)
    role = await _get_role(role_id, db)
    if not is_htmx(request):
        return RedirectResponse(with_flash(f"/admin/roles/{role_id}/", "saved"), status_code=303)
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
    # The title of a role with a role type is PM-curated from its
    # (role type, jurisdiction, qualifier) tuple (#267) — refuse manual edits so
    # the admin can't become a drift vector.
    if role["role_type_id"]:
        if not is_htmx(request):
            return RedirectResponse(
                with_flash(f"/admin/roles/{role_id}/", "invalid"), status_code=303
            )
        return templates.TemplateResponse(
            request,
            "admin/roles/partials/_title_read.html",
            {"role": role},
            headers=flash_trigger(
                "warning",
                "This title is generated from the role type, jurisdiction, and qualifier.",
            ),
        )
    cleaned = title.strip()
    if not cleaned:
        if not is_htmx(request):
            return RedirectResponse(
                with_flash(f"/admin/roles/{role_id}/", "invalid"), status_code=303
            )
        return templates.TemplateResponse(
            request,
            "admin/roles/partials/_title_form.html",
            {"role": role},
            headers=flash_trigger("warning", "Title cannot be empty."),
        )
    try:
        await db.execute("UPDATE roles SET title=$1 WHERE id=$2", cleaned, role_id)
    except asyncpg.UniqueViolationError:
        if not is_htmx(request):
            return RedirectResponse(
                with_flash(f"/admin/roles/{role_id}/", "exists"), status_code=303
            )
        return templates.TemplateResponse(
            request,
            "admin/roles/partials/_title_form.html",
            {"role": role},
            headers=flash_trigger(
                "warning",
                f"A role named <strong>{escape(cleaned)}</strong>"
                " already exists for this organization.",
            ),
        )
    role = await _get_role(role_id, db)
    if not is_htmx(request):
        return RedirectResponse(with_flash(f"/admin/roles/{role_id}/", "saved"), status_code=303)
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
        return RedirectResponse(with_flash(f"/admin/roles/{role_id}/", "saved"), status_code=303)
    return templates.TemplateResponse(
        request,
        "admin/roles/partials/_notes_read.html",
        {"role": role},
        headers=flash_trigger("success", "Notes saved."),
    )


# ---------------------------------------------------------------------------
# Structural inline (role type / jurisdiction / qualifier)
# ---------------------------------------------------------------------------


def _structural_form_ctx(
    role, role_types, *, sel_role_type_id, sel_jurisdiction_id, sel_jurisdiction_name, sel_qualifier
):
    """Context for the structural-fields edit form.

    Callers pass the selected values explicitly: the GET-edit route passes the
    role's current tuple; the POST-error path passes the *submitted* values so a
    cleared field re-renders empty rather than being restored from the DB (#264).
    """
    return {
        "role": role,
        "role_types": role_types,
        "sel_role_type_id": sel_role_type_id,
        "sel_jurisdiction_id": sel_jurisdiction_id,
        "sel_jurisdiction_name": sel_jurisdiction_name,
        "sel_qualifier": sel_qualifier,
    }


@router.get("/inline/structural/")
async def role_inline_structural_get(
    role_id: str,
    request: Request,
    user: AdminUser = Depends(get_admin_user),
    db=Depends(get_db),
):
    """Return structural-fields read partial."""
    role = await _get_role(role_id, db)
    return templates.TemplateResponse(
        request, "admin/roles/partials/_structural_read.html", {"role": role}
    )


@router.get("/inline/structural/edit/")
async def role_inline_structural_edit_get(
    role_id: str,
    request: Request,
    user: AdminUser = Depends(get_admin_user),
    db=Depends(get_db),
):
    """Return structural-fields edit form partial."""
    role = await _get_role(role_id, db)
    role_types = await fetch_role_types(db)
    return templates.TemplateResponse(
        request,
        "admin/roles/partials/_structural_form.html",
        _structural_form_ctx(
            role,
            role_types,
            sel_role_type_id=role["role_type_id"],
            sel_jurisdiction_id=role["jurisdiction_id"],
            sel_jurisdiction_name=role["jurisdiction_name"],
            sel_qualifier=role["qualifier"],
        ),
    )


@router.post("/inline/structural/")
async def role_inline_structural_post(
    role_id: str,
    request: Request,
    role_type_id: str = Form(""),
    jurisdiction_id: str = Form(""),
    qualifier: str = Form(""),
    user: AdminUser = Depends(get_admin_user),
    db=Depends(get_db),
):
    """Save the structural tuple; re-synthesize the curated title when possible (#267)."""
    role = await _get_role(role_id, db)
    rt = role_type_id.strip() or None
    jur = jurisdiction_id.strip() or None
    qual = qualifier.strip() or None

    async def _form(error: str):
        jur_name = None
        if jur:
            jur_name = await db.fetchval("SELECT name FROM jurisdictions WHERE id=$1", jur)
        role_types = await fetch_role_types(db)
        return templates.TemplateResponse(
            request,
            "admin/roles/partials/_structural_form.html",
            _structural_form_ctx(
                role,
                role_types,
                sel_role_type_id=rt,
                sel_jurisdiction_id=jur,
                sel_jurisdiction_name=jur_name,
                sel_qualifier=qual,
            ),
            headers=flash_trigger("warning", error),
        )

    # Mirror the DB check-constraints with clear messages.
    if qual is not None and jur is None:
        return await _form("A qualifier requires a jurisdiction.")
    if jur is not None and rt is None:
        return await _form("A jurisdiction requires a role type.")

    # Demotion: a role that had a role_type is being cleared back to a plain role.
    # We keep the old (synthesized) title — the title editor un-gates for plain
    # roles, so the admin can retitle it — but flag the retention in the flash.
    demoted = role["role_type_id"] is not None and rt is None

    # The title is PM-curated: regenerate it from the new tuple when the
    # formatter can render one; otherwise leave the existing title untouched.
    new_title = role["title"]
    if jur is not None:
        rt_row = await db.fetchrow(
            "SELECT slug, requires_qualifier, forbids_qualifier FROM role_types WHERE id=$1", rt
        )
        rt_slug = rt_row["slug"] if rt_row else None
        jur_slug = await db.fetchval("SELECT slug FROM jurisdictions WHERE id=$1", jur)
        synthesized = synthesize_role_title(rt_slug, jur_slug, qual) if rt_slug else None
        if synthesized is not None:
            new_title = synthesized
        # Mirror the requires_qualifier/forbids_qualifier guards + DB triggers
        # (#273/#302). The `or` is safe because the two flags are mutually
        # exclusive — chk_role_type_qualifier_policy forbids both being TRUE.
        seat_error = positionless_seat_error(
            rt_row["requires_qualifier"] if rt_row else False, qual
        ) or positioned_at_large_error(rt_row["forbids_qualifier"] if rt_row else False, qual)
        if seat_error:
            return await _form(seat_error)

    try:
        await db.execute(
            "UPDATE roles SET role_type_id=$1, jurisdiction_id=$2, qualifier=$3, title=$4"
            " WHERE id=$5",
            rt,
            jur,
            qual,
            new_title,
            role_id,
        )
    except asyncpg.UniqueViolationError:
        if jur is not None:
            return await _form(
                "A role with this role type, jurisdiction, and qualifier already exists."
            )
        return await _form(
            f"A role titled “{escape(new_title)}” already exists for this organization."
        )
    except asyncpg.ForeignKeyViolationError:
        return await _form("The selected role type or jurisdiction no longer exists.")

    role = await _get_role(role_id, db)
    if not is_htmx(request):
        return RedirectResponse(with_flash(f"/admin/roles/{role_id}/", "saved"), status_code=303)
    flash_body = (
        "Role type cleared — the previous title was retained. Edit the title if needed."
        if demoted
        else "Role type saved."
    )
    return templates.TemplateResponse(
        request,
        "admin/roles/partials/_structural_read.html",
        {"role": role},
        headers=flash_trigger("success", flash_body),
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
            return RedirectResponse(
                with_flash(f"/admin/roles/{role_id}/", "invalid"), status_code=303
            )
        return templates.TemplateResponse(
            request,
            "admin/roles/partials/_dates_form.html",
            _form_ctx(established_on, abolished_on),
            headers=flash_trigger("warning", "Invalid date format. Use YYYY-MM-DD."),
        )

    if established_on_val and abolished_on_val and established_on_val > abolished_on_val:
        if not is_htmx(request):
            return RedirectResponse(
                with_flash(f"/admin/roles/{role_id}/", "invalid"), status_code=303
            )
        return templates.TemplateResponse(
            request,
            "admin/roles/partials/_dates_form.html",
            _form_ctx(established_on, abolished_on),
            headers=flash_trigger(
                "warning", "Established date must be on or before abolished date."
            ),
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
            return RedirectResponse(
                with_flash(f"/admin/roles/{role_id}/", "invalid"), status_code=303
            )
        return templates.TemplateResponse(
            request,
            "admin/roles/partials/_dates_form.html",
            _form_ctx(established_on, abolished_on),
            headers=flash_trigger("warning", msg),
        )

    await db.execute(
        "UPDATE roles SET established_on=$1, abolished_on=$2 WHERE id=$3",
        established_on_val,
        abolished_on_val,
        role_id,
    )
    role = await _get_role(role_id, db)
    if not is_htmx(request):
        return RedirectResponse(with_flash(f"/admin/roles/{role_id}/", "saved"), status_code=303)
    return templates.TemplateResponse(
        request,
        "admin/roles/partials/_dates_read.html",
        {"role": role},
        headers=flash_trigger("success", "Boundary dates saved."),
    )
