"""Admin CRUD for organization names."""

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from markupsafe import escape

from src.api.admin.deps import (
    AdminUser,
    check_auth,
    flash_trigger,
    get_admin_user,
    get_db,
    is_htmx,
    org_header_extra,
)
from src.core.db import generate_id

templates = Jinja2Templates(directory="src/templates")
router = APIRouter(prefix="/orgs/{org_id}/names", tags=["admin-org-names"])


async def _get_org_or_404(org_id: str, db):
    org = await db.fetchrow("SELECT id FROM organizations WHERE id=$1", org_id)
    if not org:
        raise HTTPException(status_code=404, detail="Organization not found")
    return org


async def _maybe_promote_sole_name(org_id: str, db) -> None:
    """If the org has exactly one name and it is not canonical, promote it."""
    rows = await db.fetch(
        "SELECT id, is_canonical FROM organization_names WHERE organization_id=$1",
        org_id,
    )
    if len(rows) == 1 and not rows[0]["is_canonical"]:
        await db.execute(
            "UPDATE organization_names SET is_canonical=TRUE WHERE id=$1",
            rows[0]["id"],
        )


@router.get("/new-row/")
async def name_new_row(
    org_id: str,
    request: Request,
    user: AdminUser | RedirectResponse = Depends(get_admin_user),
    db=Depends(get_db),
):
    """Return empty name form row."""
    redirect, user = check_auth(user)
    if redirect:
        return redirect
    await _get_org_or_404(org_id, db)
    return templates.TemplateResponse(
        request,
        "admin/orgs/partials/_name_form_row.html",
        {"org_id": org_id, "n": None},
    )


@router.post("/")
async def name_create(
    org_id: str,
    request: Request,
    name: str = Form(...),
    name_type: str = Form("legal"),
    is_canonical: str = Form(""),
    user: AdminUser | RedirectResponse = Depends(get_admin_user),
    db=Depends(get_db),
):
    """Create a new organization name."""
    redirect, user = check_auth(user)
    if redirect:
        return redirect
    await _get_org_or_404(org_id, db)
    nid = generate_id()
    async with db.transaction():
        if is_canonical == "true":
            await db.execute(
                "UPDATE organization_names SET is_canonical=FALSE"
                " WHERE organization_id=$1 AND is_canonical=TRUE",
                org_id,
            )
        await db.execute(
            "INSERT INTO organization_names (id, organization_id, name, name_type, is_canonical)"
            " VALUES ($1, $2, $3, $4, $5)",
            nid,
            org_id,
            name.strip(),
            name_type,
            is_canonical == "true",
        )
    if not is_htmx(request):
        return RedirectResponse(f"/admin/orgs/{org_id}/", status_code=303)
    names = await db.fetch(
        "SELECT * FROM organization_names WHERE organization_id=$1"
        " ORDER BY is_canonical DESC, name_type, name",
        org_id,
    )
    return templates.TemplateResponse(
        request,
        "admin/orgs/partials/_name_rows.html",
        {"org_id": org_id, "names": names},
        headers=flash_trigger(
            "success",
            f"Name <strong>{escape(name.strip())}</strong> added.",
            extra=await org_header_extra(org_id, db),
        ),
    )


@router.get("/{name_id}/read-row/")
async def name_read_row(
    org_id: str,
    name_id: str,
    request: Request,
    user: AdminUser | RedirectResponse = Depends(get_admin_user),
    db=Depends(get_db),
):
    """Return read-only name row (used by Cancel on edit form)."""
    redirect, user = check_auth(user)
    if redirect:
        return redirect
    name_row = await db.fetchrow(
        "SELECT * FROM organization_names WHERE id=$1 AND organization_id=$2",
        name_id,
        org_id,
    )
    if not name_row:
        raise HTTPException(status_code=404)
    return templates.TemplateResponse(
        request, "admin/orgs/partials/_name_row.html", {"org_id": org_id, "n": name_row}
    )


@router.get("/{name_id}/edit-row/")
async def name_edit_row_get(
    org_id: str,
    name_id: str,
    request: Request,
    user: AdminUser | RedirectResponse = Depends(get_admin_user),
    db=Depends(get_db),
):
    """Return name edit form row."""
    redirect, user = check_auth(user)
    if redirect:
        return redirect
    name_row = await db.fetchrow(
        "SELECT * FROM organization_names WHERE id=$1 AND organization_id=$2",
        name_id,
        org_id,
    )
    if not name_row:
        raise HTTPException(status_code=404)
    return templates.TemplateResponse(
        request,
        "admin/orgs/partials/_name_form_row.html",
        {"org_id": org_id, "n": name_row},
    )


@router.post("/{name_id}/edit-row/")
async def name_edit_row_post(
    org_id: str,
    name_id: str,
    request: Request,
    name: str = Form(...),
    name_type: str = Form("legal"),
    is_canonical: str = Form(""),
    user: AdminUser | RedirectResponse = Depends(get_admin_user),
    db=Depends(get_db),
):
    """Update an organization name."""
    redirect, user = check_auth(user)
    if redirect:
        return redirect
    existing = await db.fetchrow(
        "SELECT * FROM organization_names WHERE id=$1 AND organization_id=$2",
        name_id,
        org_id,
    )
    if not existing:
        raise HTTPException(status_code=404)
    if is_canonical != "true" and existing["is_canonical"]:
        # Guard runs outside the transaction intentionally: a concurrent promotion
        # (another request canonicalizing a different name) would make this check
        # false — i.e., the save would be allowed — which is the safe direction.
        other_canonical = await db.fetchval(
            "SELECT id FROM organization_names"
            " WHERE organization_id=$1 AND is_canonical=TRUE AND id != $2",
            org_id,
            name_id,
        )
        if not other_canonical:
            if not is_htmx(request):
                return RedirectResponse(f"/admin/orgs/{org_id}/", status_code=303)
            return HTMLResponse(
                content="",
                status_code=200,
                headers=flash_trigger(
                    "error",
                    "Cannot remove canonical. Promote another name first.",
                ),
            )
    async with db.transaction():
        if is_canonical == "true":
            await db.execute(
                "UPDATE organization_names SET is_canonical=FALSE"
                " WHERE organization_id=$1 AND is_canonical=TRUE AND id != $2",
                org_id,
                name_id,
            )
        await db.execute(
            "UPDATE organization_names SET name=$1, name_type=$2, is_canonical=$3 WHERE id=$4",
            name.strip(),
            name_type,
            is_canonical == "true",
            name_id,
        )
    if not is_htmx(request):
        return RedirectResponse(f"/admin/orgs/{org_id}/", status_code=303)
    names = await db.fetch(
        "SELECT * FROM organization_names WHERE organization_id=$1"
        " ORDER BY is_canonical DESC, name_type, name",
        org_id,
    )
    return templates.TemplateResponse(
        request,
        "admin/orgs/partials/_name_rows.html",
        {"org_id": org_id, "names": names},
        headers=flash_trigger(
            "success",
            f"Name <strong>{escape(name.strip())}</strong> saved.",
            extra=await org_header_extra(org_id, db),
        ),
    )


@router.delete("/{name_id}/")
async def name_delete(
    org_id: str,
    name_id: str,
    request: Request,
    user: AdminUser | RedirectResponse = Depends(get_admin_user),
    db=Depends(get_db),
):
    """Delete an organization name."""
    redirect, user = check_auth(user)
    if redirect:
        return redirect
    existing = await db.fetchrow(
        "SELECT id FROM organization_names WHERE id=$1 AND organization_id=$2",
        name_id,
        org_id,
    )
    if not existing:
        raise HTTPException(status_code=404)
    async with db.transaction():
        name_count = await db.fetchval(
            "SELECT count(*) FROM organization_names WHERE organization_id=$1",
            org_id,
        )
        canonical_acronym_count = await db.fetchval(
            "SELECT count(*) FROM organization_acronyms"
            " WHERE organization_id=$1 AND is_canonical=TRUE",
            org_id,
        )
        if name_count == 1 and canonical_acronym_count == 0:
            if not is_htmx(request):
                raise HTTPException(
                    status_code=409,
                    detail="Cannot remove the only name: no canonical acronym exists.",
                )
            return HTMLResponse(
                content="",
                status_code=200,
                headers=flash_trigger(
                    "error",
                    "Cannot remove the only name when the organization has no canonical acronym.",
                ),
            )
        await db.execute("DELETE FROM organization_names WHERE id=$1", name_id)
        await _maybe_promote_sole_name(org_id, db)
    if not is_htmx(request):
        return RedirectResponse(f"/admin/orgs/{org_id}/", status_code=303)
    names = await db.fetch(
        "SELECT * FROM organization_names WHERE organization_id=$1"
        " ORDER BY is_canonical DESC, name_type, name",
        org_id,
    )
    return templates.TemplateResponse(
        request,
        "admin/orgs/partials/_name_rows.html",
        {"org_id": org_id, "names": names},
        headers=flash_trigger("info", "Name removed.", extra=await org_header_extra(org_id, db)),
    )
