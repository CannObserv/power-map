"""Admin CRUD for organization acronyms."""

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from markupsafe import escape

from src.api.admin.deps import (
    AdminUser,
    flash_trigger,
    get_admin_user,
    get_db,
    is_htmx,
    org_header_extra,
    with_flash,
)
from src.core.db import generate_id

templates = Jinja2Templates(directory="src/templates")
router = APIRouter(prefix="/orgs/{org_id}/acronyms", tags=["admin-org-acronyms"])


async def _get_org_or_404(org_id: str, db):
    org = await db.fetchrow("SELECT id FROM organizations WHERE id=$1", org_id)
    if not org:
        raise HTTPException(status_code=404, detail="Organization not found")
    return org


async def _maybe_promote_sole_acronym(org_id: str, db) -> None:
    """If the org has exactly one acronym and it is not canonical, promote it."""
    rows = await db.fetch(
        "SELECT id, is_canonical FROM organization_acronyms WHERE organization_id=$1",
        org_id,
    )
    if len(rows) == 1 and not rows[0]["is_canonical"]:
        await db.execute(
            "UPDATE organization_acronyms SET is_canonical=TRUE WHERE id=$1",
            rows[0]["id"],
        )


@router.get("/new-row/")
async def acronym_new_row(
    org_id: str,
    request: Request,
    user: AdminUser = Depends(get_admin_user),
    db=Depends(get_db),
):
    """Return empty acronym form row."""
    await _get_org_or_404(org_id, db)
    return templates.TemplateResponse(
        request,
        "admin/orgs/partials/_acronym_form_row.html",
        {"org_id": org_id, "a": None},
    )


@router.post("/")
async def acronym_create(
    org_id: str,
    request: Request,
    acronym: str = Form(...),
    is_canonical: str = Form(""),
    user: AdminUser = Depends(get_admin_user),
    db=Depends(get_db),
):
    """Create a new organization acronym."""
    await _get_org_or_404(org_id, db)
    aid = generate_id()
    async with db.transaction():
        if is_canonical == "true":
            await db.execute(
                "UPDATE organization_acronyms SET is_canonical=FALSE"
                " WHERE organization_id=$1 AND is_canonical=TRUE",
                org_id,
            )
        await db.execute(
            "INSERT INTO organization_acronyms (id, organization_id, acronym, is_canonical)"
            " VALUES ($1, $2, $3, $4)",
            aid,
            org_id,
            acronym.strip(),
            is_canonical == "true",
        )
    if not is_htmx(request):
        return RedirectResponse(with_flash(f"/admin/orgs/{org_id}/", "saved"), status_code=303)
    acronyms = await db.fetch(
        "SELECT * FROM organization_acronyms WHERE organization_id=$1"
        " ORDER BY is_canonical DESC, acronym",
        org_id,
    )
    return templates.TemplateResponse(
        request,
        "admin/orgs/partials/_acronym_rows.html",
        {"org_id": org_id, "acronyms": acronyms},
        headers=flash_trigger(
            "success",
            f"Acronym <strong>{escape(acronym.strip())}</strong> added.",
            extra=await org_header_extra(org_id, db),
        ),
    )


@router.get("/{acronym_id}/read-row/")
async def acronym_read_row(
    org_id: str,
    acronym_id: str,
    request: Request,
    user: AdminUser = Depends(get_admin_user),
    db=Depends(get_db),
):
    """Return read-only acronym row (used by Cancel on edit form)."""
    row = await db.fetchrow(
        "SELECT * FROM organization_acronyms WHERE id=$1 AND organization_id=$2",
        acronym_id,
        org_id,
    )
    if not row:
        raise HTTPException(status_code=404)
    return templates.TemplateResponse(
        request,
        "admin/orgs/partials/_acronym_row.html",
        {"org_id": org_id, "a": row},
    )


@router.get("/{acronym_id}/edit-row/")
async def acronym_edit_row_get(
    org_id: str,
    acronym_id: str,
    request: Request,
    user: AdminUser = Depends(get_admin_user),
    db=Depends(get_db),
):
    """Return acronym edit form row."""
    row = await db.fetchrow(
        "SELECT * FROM organization_acronyms WHERE id=$1 AND organization_id=$2",
        acronym_id,
        org_id,
    )
    if not row:
        raise HTTPException(status_code=404)
    return templates.TemplateResponse(
        request,
        "admin/orgs/partials/_acronym_form_row.html",
        {"org_id": org_id, "a": row},
    )


@router.post("/{acronym_id}/edit-row/")
async def acronym_edit_row_post(
    org_id: str,
    acronym_id: str,
    request: Request,
    acronym: str = Form(...),
    is_canonical: str = Form(""),
    user: AdminUser = Depends(get_admin_user),
    db=Depends(get_db),
):
    """Update an organization acronym."""
    existing = await db.fetchrow(
        "SELECT * FROM organization_acronyms WHERE id=$1 AND organization_id=$2",
        acronym_id,
        org_id,
    )
    if not existing:
        raise HTTPException(status_code=404)
    async with db.transaction():
        if is_canonical == "true":
            await db.execute(
                "UPDATE organization_acronyms SET is_canonical=FALSE"
                " WHERE organization_id=$1 AND is_canonical=TRUE AND id != $2",
                org_id,
                acronym_id,
            )
        await db.execute(
            "UPDATE organization_acronyms SET acronym=$1, is_canonical=$2 WHERE id=$3",
            acronym.strip(),
            is_canonical == "true",
            acronym_id,
        )
        await _maybe_promote_sole_acronym(org_id, db)
    if not is_htmx(request):
        return RedirectResponse(with_flash(f"/admin/orgs/{org_id}/", "saved"), status_code=303)
    acronyms = await db.fetch(
        "SELECT * FROM organization_acronyms WHERE organization_id=$1"
        " ORDER BY is_canonical DESC, acronym",
        org_id,
    )
    return templates.TemplateResponse(
        request,
        "admin/orgs/partials/_acronym_rows.html",
        {"org_id": org_id, "acronyms": acronyms},
        headers=flash_trigger(
            "success",
            f"Acronym <strong>{escape(acronym.strip())}</strong> saved.",
            extra=await org_header_extra(org_id, db),
        ),
    )


@router.delete("/{acronym_id}/")
async def acronym_delete(
    org_id: str,
    acronym_id: str,
    request: Request,
    user: AdminUser = Depends(get_admin_user),
    db=Depends(get_db),
):
    """Delete an organization acronym."""
    existing = await db.fetchrow(
        "SELECT id FROM organization_acronyms WHERE id=$1 AND organization_id=$2",
        acronym_id,
        org_id,
    )
    if not existing:
        raise HTTPException(status_code=404)
    async with db.transaction():
        acronym_count = await db.fetchval(
            "SELECT count(*) FROM organization_acronyms WHERE organization_id=$1",
            org_id,
        )
        canonical_name_count = await db.fetchval(
            "SELECT count(*) FROM organization_names"
            " WHERE organization_id=$1 AND is_canonical=TRUE",
            org_id,
        )
        if acronym_count == 1 and canonical_name_count == 0:
            if not is_htmx(request):
                raise HTTPException(
                    status_code=409,
                    detail="Cannot remove the only acronym: no canonical name exists.",
                )
            return HTMLResponse(
                content="",
                status_code=200,
                headers=flash_trigger(
                    "warning",
                    "Cannot remove the only acronym when the organization has no canonical name.",
                ),
            )
        await db.execute("DELETE FROM organization_acronyms WHERE id=$1", acronym_id)
        await _maybe_promote_sole_acronym(org_id, db)
    if not is_htmx(request):
        return RedirectResponse(with_flash(f"/admin/orgs/{org_id}/", "removed"), status_code=303)
    acronyms = await db.fetch(
        "SELECT * FROM organization_acronyms WHERE organization_id=$1"
        " ORDER BY is_canonical DESC, acronym",
        org_id,
    )
    return templates.TemplateResponse(
        request,
        "admin/orgs/partials/_acronym_rows.html",
        {"org_id": org_id, "acronyms": acronyms},
        headers=flash_trigger(
            "success", "Acronym removed.", extra=await org_header_extra(org_id, db)
        ),
    )
