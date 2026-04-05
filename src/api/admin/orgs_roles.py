"""Inline role create on the org detail page."""

import asyncpg
from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from markupsafe import escape

from src.api.admin.deps import AdminUser, check_auth, flash_trigger, get_admin_user, get_db, is_htmx
from src.core.db import generate_id

templates = Jinja2Templates(directory="src/templates")
router = APIRouter(prefix="/orgs/{org_id}/roles", tags=["admin-org-roles"])


async def _get_org_or_404(org_id: str, db) -> None:
    row = await db.fetchrow("SELECT id FROM organizations WHERE id = $1", org_id)
    if not row:
        raise HTTPException(status_code=404, detail="Organization not found")


async def _fetch_roles(org_id: str, db) -> list:
    return await db.fetch(
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


@router.get("/new-row/")
async def role_new_row(
    org_id: str,
    request: Request,
    user: AdminUser | RedirectResponse = Depends(get_admin_user),
    db=Depends(get_db),
):
    """Return blank inline role form row."""
    redirect, user = check_auth(user)
    if redirect:
        return redirect
    await _get_org_or_404(org_id, db)
    return templates.TemplateResponse(
        request,
        "admin/orgs/partials/_role_form_row.html",
        {"org_id": org_id, "title_input": ""},
    )


@router.post("/")
async def role_create(
    org_id: str,
    request: Request,
    title: str = Form(...),
    user: AdminUser | RedirectResponse = Depends(get_admin_user),
    db=Depends(get_db),
):
    """Create a new role for this org (inline HTMX path)."""
    redirect, user = check_auth(user)
    if redirect:
        return redirect
    await _get_org_or_404(org_id, db)
    title = title.strip()
    if not title:
        if not is_htmx(request):
            return RedirectResponse(f"/admin/orgs/{org_id}/", status_code=303)
        return templates.TemplateResponse(
            request,
            "admin/orgs/partials/_role_form_row.html",
            {"org_id": org_id, "title_input": ""},
            headers={
                **flash_trigger("error", "Role title cannot be empty."),
                "HX-Retarget": "#role-row-new",
                "HX-Reswap": "outerHTML",
            },
        )
    role_id = generate_id()
    try:
        await db.execute(
            "INSERT INTO roles (id, organization_id, title) VALUES ($1, $2, $3)",
            role_id, org_id, title,
        )
    except asyncpg.UniqueViolationError:
        if not is_htmx(request):
            return RedirectResponse(f"/admin/orgs/{org_id}/", status_code=303)
        return templates.TemplateResponse(
            request,
            "admin/orgs/partials/_role_form_row.html",
            {"org_id": org_id, "title_input": title},
            headers={
                **flash_trigger(
                    "error",
                    f"A role named <strong>{escape(title)}</strong>"
                    " already exists for this organization.",
                ),
                "HX-Retarget": "#role-row-new",
                "HX-Reswap": "outerHTML",
            },
        )
    if not is_htmx(request):
        return RedirectResponse(f"/admin/orgs/{org_id}/", status_code=303)
    roles = await _fetch_roles(org_id, db)
    return templates.TemplateResponse(
        request,
        "admin/orgs/partials/_role_rows.html",
        {"roles": roles},
        headers=flash_trigger("success", f"Role <strong>{escape(title)}</strong> added."),
    )
