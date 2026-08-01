"""Inline role create and merge on the org detail page."""

from datetime import UTC, datetime

import asyncpg
from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from markupsafe import escape

from src.api.admin._citations_shared import citation_count_lateral
from src.api.admin.deps import AdminUser, flash_trigger, get_admin_user, get_db, is_htmx, with_flash
from src.api.admin.list_filters import parse_list_filters
from src.api.admin.roles_queries import VALID_STATUSES, query_roles_rows
from src.core.ancillary_migrate import (
    rehome_assignment_relationships,
    rehome_conflicting_assignment_ancillary,
    rehome_role_ancillary,
)
from src.core.db import generate_id

templates = Jinja2Templates(directory="src/templates")
router = APIRouter(prefix="/orgs/{org_id}/roles", tags=["admin-org-roles"])

# Roles list region id (#251). A merge initiated from /admin/roles/ targets this
# region; the org-detail roles table merge does not, so the response branches on it.
_LIST_TARGET = "roles-list-region"


def _parse_roles_list_filters(request: Request) -> dict:
    """Parse the roles list filters (incl. the roles-only `org_q`) from HX-Current-URL.

    Thin wrapper binding the roles two-valued status set and the second
    organization-name filter; parsing logic is shared with People / Orgs via
    `src.api.admin.list_filters`.
    """
    return parse_list_filters(request, valid_statuses=VALID_STATUSES, extra_text_params=("org_q",))


async def _get_org_or_404(org_id: str, db) -> None:
    row = await db.fetchrow("SELECT id FROM organizations WHERE id = $1", org_id)
    if not row:
        raise HTTPException(status_code=404, detail="Organization not found")


async def fetch_org_roles(org_id: str, db) -> list:
    """Fetch active roles for an org with assignment + citation counts (#341)."""
    return await db.fetch(
        f"""SELECT r.id, r.title, sub.assignment_count, sub.current_count,
                  cc_j.citation_count
           FROM roles r
           CROSS JOIN LATERAL (
               SELECT COUNT(*) AS assignment_count,
                      COUNT(*) FILTER (WHERE is_current) AS current_count
               FROM role_assignments
               WHERE role_id = r.id AND archived_at IS NULL
           ) sub
           {citation_count_lateral("role", "r.id")}
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
            return RedirectResponse(
                with_flash(f"/admin/orgs/{org_id}/", "invalid"), status_code=303
            )
        return templates.TemplateResponse(
            request,
            "admin/orgs/partials/_role_form_row.html",
            {"org_id": org_id, "title_input": ""},
            headers={
                **flash_trigger("warning", "Role title cannot be empty."),
                "HX-Retarget": "#role-row-new",
                "HX-Reswap": "outerHTML",
            },
        )
    role_id = generate_id()
    try:
        await db.execute(
            "INSERT INTO roles (id, organization_id, title) VALUES ($1, $2, $3)",
            role_id,
            org_id,
            title,
        )
    except asyncpg.UniqueViolationError:
        if not is_htmx(request):
            return RedirectResponse(with_flash(f"/admin/orgs/{org_id}/", "exists"), status_code=303)
        return templates.TemplateResponse(
            request,
            "admin/orgs/partials/_role_form_row.html",
            {"org_id": org_id, "title_input": title},
            headers={
                **flash_trigger(
                    "warning",
                    f"A role named <strong>{escape(title)}</strong>"
                    " already exists for this organization.",
                ),
                "HX-Retarget": "#role-row-new",
                "HX-Reswap": "outerHTML",
            },
        )
    if not is_htmx(request):
        return RedirectResponse(with_flash(f"/admin/orgs/{org_id}/", "saved"), status_code=303)
    roles = await fetch_org_roles(org_id, db)
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

        if winner["organization_id"] != org_id or loser["organization_id"] != org_id:
            raise HTTPException(
                status_code=409,
                detail="Roles must belong to the same organization",
            )

        # Notes: prefix loser's notes with merge metadata, append to winner
        if loser["notes"]:
            merge_date = datetime.now(UTC).strftime("%Y-%m-%d")
            prefix = f"Merged from {loser['title']} on {merge_date} by {user.email}"
            appended = f"{prefix}\n{loser['notes']}"
            new_notes = f"{winner['notes']}\n\n{appended}" if winner["notes"] else appended
            await db.execute(
                "UPDATE roles SET notes=$1 WHERE id=$2",
                new_notes,
                winner_id,
            )

        # role_assignments: delete conflicts (same person+start_date), reassign rest.
        # #324: re-home the conflict rows' polymorphic ancillary onto the surviving
        # winner assignment before the hard-delete, else it orphans.
        conflict_pairs = await db.fetch(
            """SELECT l.id AS loser_ra, w.id AS winner_ra
               FROM role_assignments l
               JOIN role_assignments w
                 ON w.role_id=$2 AND w.archived_at IS NULL
                AND w.person_id = l.person_id
                AND w.start_date IS NOT DISTINCT FROM l.start_date
               WHERE l.role_id=$1 AND l.archived_at IS NULL""",
            loser_id,
            winner_id,
        )
        _conflict_pairs = [(r["loser_ra"], r["winner_ra"]) for r in conflict_pairs]
        await rehome_conflicting_assignment_ancillary(db, _conflict_pairs)
        # #301: re-point active relationship edges onto the winner before the
        # hard-delete (FK ON DELETE CASCADE would otherwise drop them silently).
        await rehome_assignment_relationships(db, _conflict_pairs)
        await db.execute(
            """DELETE FROM role_assignments ra
               WHERE ra.role_id=$1 AND ra.archived_at IS NULL
                 AND EXISTS (
                     SELECT 1 FROM role_assignments w
                     WHERE w.role_id=$2 AND w.archived_at IS NULL
                       AND w.person_id = ra.person_id
                       AND w.start_date IS NOT DISTINCT FROM ra.start_date
                 )""",
            loser_id,
            winner_id,
        )
        await db.execute(
            "UPDATE role_assignments SET role_id=$1 WHERE role_id=$2",
            winner_id,
            loser_id,
        )

        # #326: re-home the loser role's own contacts/links onto the winner before
        # the hard-delete, else they orphan (entity_type='role', no FK).
        await rehome_role_ancillary(db, loser_id, winner_id)
        await db.execute("DELETE FROM roles WHERE id=$1", loser_id)

    loser_title = loser["title"]
    winner_title = winner["title"]

    if is_htmx(request):
        body = (
            f"Merged <strong>{escape(loser_title)}</strong>"
            f" into <strong>{escape(winner_title)}</strong>."
        )
        # List-flow branch (#251): merge initiated from /admin/roles/. HX-Target
        # identifies the swap region; re-render the full roles `_region.html`
        # (rows + caption total + sticky pagination) so post-merge counts stay
        # consistent. Filter state (incl. org_q) preserved via HX-Current-URL.
        if request.headers.get("HX-Target") == _LIST_TARGET:
            filters = _parse_roles_list_filters(request)
            rows, count, pctx, hidden_matches = await query_roles_rows(db, **filters)
            ctx = {
                "user": user,
                "active_section": "roles",
                "roles": rows,
                "total": count,
                "q": filters["q"],
                "org_q": filters["org_q"],
                "status": filters["status"],
                "page_size": filters["page_size"],
                "hidden_matches": hidden_matches,
                **pctx,
            }
            return templates.TemplateResponse(
                request,
                "admin/roles/_region.html",
                ctx,
                headers=flash_trigger("success", body),
            )
        # Org-detail roles-table branch (existing) — keep working unchanged.
        roles = await fetch_org_roles(org_id, db)
        return templates.TemplateResponse(
            request,
            "admin/orgs/partials/_role_rows.html",
            {"roles": roles},
            headers=flash_trigger("success", body),
        )
    return RedirectResponse(with_flash(f"/admin/orgs/{org_id}/", "saved"), status_code=303)


@router.get("/{winner_id}/merge-preview/{loser_id}/")
async def role_merge_preview(
    org_id: str,
    winner_id: str,
    loser_id: str,
    request: Request,
    ctx: str = "",
    user: AdminUser = Depends(get_admin_user),
    db=Depends(get_db),
):
    """Return the role merge-preview modal (#255).

    Roles have no names/acronyms, so this is confirmation-style: it surfaces how many
    assignments reassign vs. drop as (person, start_date) conflicts, and whether the
    loser's notes will be appended. Unlike Orgs/People — which need a curated
    `merge-with` endpoint to honour keep/drop name selections — there is nothing to
    curate here, so the modal simply posts to the existing `role_merge` (`/merge/`)
    route, targeting the roles list region. `ctx` is accepted for symmetry with the
    other entity previews; the role merge is only ever opened from the list.
    """
    if winner_id == loser_id:
        raise HTTPException(status_code=400, detail="Cannot merge a role with itself")
    winner = await db.fetchrow(
        "SELECT id, title, organization_id, archived_at FROM roles WHERE id=$1", winner_id
    )
    loser = await db.fetchrow(
        "SELECT id, title, organization_id, notes, archived_at FROM roles WHERE id=$1", loser_id
    )
    if not winner or not loser:
        raise HTTPException(status_code=404, detail="Role not found")
    if winner["organization_id"] != org_id or loser["organization_id"] != org_id:
        raise HTTPException(status_code=409, detail="Roles must belong to the same organization")
    if winner["archived_at"] or loser["archived_at"]:
        raise HTTPException(status_code=409, detail="Cannot merge archived roles")

    total_assignments = await db.fetchval(
        "SELECT count(*) FROM role_assignments WHERE role_id=$1 AND archived_at IS NULL",
        loser_id,
    )
    conflict_count = await db.fetchval(
        """SELECT count(*) FROM role_assignments l
           WHERE l.role_id=$1 AND l.archived_at IS NULL
             AND EXISTS (
                 SELECT 1 FROM role_assignments w
                 WHERE w.role_id=$2 AND w.archived_at IS NULL
                   AND w.person_id = l.person_id
                   AND w.start_date IS NOT DISTINCT FROM l.start_date
             )""",
        loser_id,
        winner_id,
    )
    reassigned_count = total_assignments - conflict_count

    return templates.TemplateResponse(
        request,
        "admin/roles/_merge_preview_modal.html",
        {
            "org_id": org_id,
            "winner_id": winner_id,
            "loser_id": loser_id,
            "winner_title": winner["title"],
            "loser_title": loser["title"],
            "reassigned_count": reassigned_count,
            "conflict_count": conflict_count,
            "loser_has_notes": bool(loser["notes"]),
            "ctx": ctx,
        },
    )
