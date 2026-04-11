"""Inline role create and merge on the org detail page."""

from datetime import UTC, datetime

import asyncpg
from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from markupsafe import escape

from src.api.admin.deps import AdminUser, flash_trigger, get_admin_user, get_db, is_htmx
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
    user: AdminUser = Depends(get_admin_user),
    db=Depends(get_db),
):
    """Return blank inline role form row."""
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
    user: AdminUser = Depends(get_admin_user),
    db=Depends(get_db),
):
    """Create a new role for this org (inline HTMX path)."""
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


@router.post("/{winner_id}/merge/{loser_id}/")
async def role_merge(
    org_id: str,
    winner_id: str,
    loser_id: str,
    request: Request,
    user: AdminUser = Depends(get_admin_user),
    db=Depends(get_db),
):
    """Merge loser role into winner: reassign assignments, hard-delete loser."""

    async with db.transaction():
        winner = await db.fetchrow(
            "SELECT id, organization_id, title, notes, archived_at"
            " FROM roles WHERE id=$1 FOR UPDATE",
            winner_id,
        )
        loser = await db.fetchrow(
            "SELECT id, organization_id, title, notes, archived_at"
            " FROM roles WHERE id=$1 FOR UPDATE",
            loser_id,
        )
        if not winner or not loser:
            raise HTTPException(status_code=404, detail="Role not found")

        if winner["archived_at"] or loser["archived_at"]:
            raise HTTPException(status_code=409, detail="Cannot merge archived roles")

        if (
            winner["organization_id"] != org_id
            or loser["organization_id"] != org_id
        ):
            raise HTTPException(
                status_code=409,
                detail="Roles must belong to the same organization",
            )

        # Notes: prefix loser's notes with merge metadata, append to winner
        if loser["notes"]:
            merge_date = datetime.now(UTC).strftime("%Y-%m-%d")
            prefix = (
                f"Merged from {loser['title']} on {merge_date}"
                f" by {user.email}"
            )
            appended = f"{prefix}\n{loser['notes']}"
            new_notes = (
                f"{winner['notes']}\n\n{appended}"
                if winner["notes"]
                else appended
            )
            await db.execute(
                "UPDATE roles SET notes=$1 WHERE id=$2", new_notes, winner_id,
            )

        # role_assignments: delete conflicts (same person+start_date), reassign rest
        await db.execute(
            """DELETE FROM role_assignments ra
               WHERE ra.role_id=$1 AND ra.archived_at IS NULL
                 AND EXISTS (
                     SELECT 1 FROM role_assignments w
                     WHERE w.role_id=$2 AND w.archived_at IS NULL
                       AND w.person_id = ra.person_id
                       AND w.start_date IS NOT DISTINCT FROM ra.start_date
                 )""",
            loser_id, winner_id,
        )
        await db.execute(
            "UPDATE role_assignments SET role_id=$1 WHERE role_id=$2",
            winner_id, loser_id,
        )

        await db.execute("DELETE FROM roles WHERE id=$1", loser_id)

    loser_title = loser["title"]
    winner_title = winner["title"]

    if is_htmx(request):
        roles = await _fetch_roles(org_id, db)
        body = (
            f"Merged <strong>{escape(loser_title)}</strong>"
            f" into <strong>{escape(winner_title)}</strong>."
        )
        return templates.TemplateResponse(
            request,
            "admin/orgs/partials/_role_rows.html",
            {"roles": roles},
            headers=flash_trigger("success", body),
        )
    return RedirectResponse(f"/admin/orgs/{org_id}/", status_code=303)
