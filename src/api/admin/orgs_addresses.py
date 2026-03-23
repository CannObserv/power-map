"""Admin CRUD for organization addresses."""

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from src.api.admin.deps import AdminUser, check_auth, get_admin_user, get_db
from src.core.db import generate_id

templates = Jinja2Templates(directory="src/templates")
router = APIRouter(prefix="/orgs/{org_id}/addresses", tags=["admin-org-addresses"])


def _is_htmx(request: Request) -> bool:
    return bool(request.headers.get("HX-Request") and not request.headers.get("HX-Boosted"))


async def _get_org_or_404(org_id: str, db):
    org = await db.fetchrow("SELECT id FROM organizations WHERE id=$1", org_id)
    if not org:
        raise HTTPException(status_code=404, detail="Organization not found")
    return org


async def _get_entity_address_or_404(addr_id: str, org_id: str, db):
    row = await db.fetchrow(
        """SELECT ea.id, ea.address_type, ea.display_name,
                  a.id AS address_id, a.standardized, a.address_line_1, a.address_line_2,
                  a.city, a.region, a.postal_code
           FROM entity_addresses ea JOIN addresses a ON a.id = ea.address_id
           WHERE ea.id=$1 AND ea.entity_type='organization' AND ea.entity_id=$2""",
        addr_id,
        org_id,
    )
    if not row:
        raise HTTPException(status_code=404)
    return row


@router.get("/new-row/")
async def address_new_row(
    org_id: str,
    request: Request,
    user: AdminUser | RedirectResponse = Depends(get_admin_user),
    db=Depends(get_db),
):
    """Return empty address form row."""
    redirect, user = check_auth(user)
    if redirect:
        return redirect
    await _get_org_or_404(org_id, db)
    return templates.TemplateResponse(
        request,
        "admin/orgs/partials/_address_form_row.html",
        {"org_id": org_id, "a": None},
    )


@router.post("/")
async def address_create(
    org_id: str,
    request: Request,
    address_line_1: str = Form(""),
    address_line_2: str = Form(""),
    city: str = Form(""),
    region: str = Form(""),
    postal_code: str = Form(""),
    address_type: str = Form("mailing"),
    display_name: str = Form(""),
    user: AdminUser | RedirectResponse = Depends(get_admin_user),
    db=Depends(get_db),
):
    """Create a new organization address."""
    redirect, user = check_auth(user)
    if redirect:
        return redirect
    await _get_org_or_404(org_id, db)
    aid = generate_id()
    eaid = generate_id()
    await db.execute(
        "INSERT INTO addresses (id, address_line_1, address_line_2, city, region, postal_code)"
        " VALUES ($1, $2, $3, $4, $5, $6)",
        aid,
        address_line_1.strip() or None,
        address_line_2.strip() or None,
        city.strip() or None,
        region.strip() or None,
        postal_code.strip() or None,
    )
    await db.execute(
        "INSERT INTO entity_addresses"
        " (id, entity_type, entity_id, address_id, address_type, display_name)"
        " VALUES ($1, 'organization', $2, $3, $4, $5)",
        eaid,
        org_id,
        aid,
        address_type,
        display_name.strip() or None,
    )
    row = await _get_entity_address_or_404(eaid, org_id, db)
    if not _is_htmx(request):
        return RedirectResponse(f"/admin/orgs/{org_id}/", status_code=303)
    return templates.TemplateResponse(
        request, "admin/orgs/partials/_address_row.html", {"org_id": org_id, "a": row}
    )


@router.get("/{addr_id}/edit-row/")
async def address_edit_row_get(
    org_id: str,
    addr_id: str,
    request: Request,
    user: AdminUser | RedirectResponse = Depends(get_admin_user),
    db=Depends(get_db),
):
    """Return address edit form row."""
    redirect, user = check_auth(user)
    if redirect:
        return redirect
    row = await _get_entity_address_or_404(addr_id, org_id, db)
    return templates.TemplateResponse(
        request,
        "admin/orgs/partials/_address_form_row.html",
        {"org_id": org_id, "a": row},
    )


@router.post("/{addr_id}/edit-row/")
async def address_edit_row_post(
    org_id: str,
    addr_id: str,
    request: Request,
    address_line_1: str = Form(""),
    address_line_2: str = Form(""),
    city: str = Form(""),
    region: str = Form(""),
    postal_code: str = Form(""),
    address_type: str = Form("mailing"),
    display_name: str = Form(""),
    user: AdminUser | RedirectResponse = Depends(get_admin_user),
    db=Depends(get_db),
):
    """Update an organization address."""
    redirect, user = check_auth(user)
    if redirect:
        return redirect
    existing = await _get_entity_address_or_404(addr_id, org_id, db)
    await db.execute(
        "UPDATE addresses SET address_line_1=$1, address_line_2=$2, city=$3, region=$4,"
        " postal_code=$5 WHERE id=$6",
        address_line_1.strip() or None,
        address_line_2.strip() or None,
        city.strip() or None,
        region.strip() or None,
        postal_code.strip() or None,
        existing["address_id"],
    )
    await db.execute(
        "UPDATE entity_addresses SET address_type=$1, display_name=$2 WHERE id=$3",
        address_type,
        display_name.strip() or None,
        addr_id,
    )
    row = await _get_entity_address_or_404(addr_id, org_id, db)
    if not _is_htmx(request):
        return RedirectResponse(f"/admin/orgs/{org_id}/", status_code=303)
    return templates.TemplateResponse(
        request, "admin/orgs/partials/_address_row.html", {"org_id": org_id, "a": row}
    )


@router.delete("/{addr_id}/")
async def address_delete(
    org_id: str,
    addr_id: str,
    request: Request,
    user: AdminUser | RedirectResponse = Depends(get_admin_user),
    db=Depends(get_db),
):
    """Delete an organization address link (entity_addresses only)."""
    redirect, user = check_auth(user)
    if redirect:
        return redirect
    existing = await db.fetchrow(
        "SELECT id FROM entity_addresses"
        " WHERE id=$1 AND entity_type='organization' AND entity_id=$2",
        addr_id,
        org_id,
    )
    if not existing:
        raise HTTPException(status_code=404)
    await db.execute("DELETE FROM entity_addresses WHERE id=$1", addr_id)
    return HTMLResponse(content="", status_code=200)
