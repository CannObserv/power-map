"""Admin CRUD for organization addresses."""

import json

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from src.api.admin.deps import AdminUser, flash_trigger, get_admin_user, get_db, is_htmx
from src.core.db import generate_id
from src.core.normalizers.address import get_address_normalizer
from src.core.normalizers.address_meta import get_country_format

templates = Jinja2Templates(directory="src/templates")
router = APIRouter(prefix="/orgs/{org_id}/addresses", tags=["admin-org-addresses"])


def _is_all_blank(*fields: str) -> bool:
    return not any(f.strip() for f in fields)


def _parse_normalizer_fields(
    standardized: str,
    latitude: str,
    longitude: str,
    components: str,
) -> tuple:
    """Parse mode=save normalizer form fields into DB-ready values.

    Raises ValueError if latitude/longitude are non-numeric or components is not valid JSON.
    """
    _standardized = standardized.strip() or None
    lat_str = latitude.strip()
    lon_str = longitude.strip()
    comp_str = components.strip()
    try:
        _latitude = float(lat_str) if lat_str else None
    except ValueError:
        raise ValueError(f"latitude must be a number, got {lat_str!r}")
    try:
        _longitude = float(lon_str) if lon_str else None
    except ValueError:
        raise ValueError(f"longitude must be a number, got {lon_str!r}")
    if comp_str:
        try:
            json.loads(comp_str)
        except ValueError:
            raise ValueError("components must be valid JSON")
    _components = comp_str or None
    return _standardized, _latitude, _longitude, _components


_NORMALIZER = get_address_normalizer()


async def _field_context(country: str) -> dict:
    """Return field_labels and field_visible for the given country code."""
    fmt = await get_country_format(country.upper() if country else "US")
    return {
        "field_labels": {f["key"]: f["label"] for f in fmt.get("fields", [])},
        "field_visible": {f["key"] for f in fmt.get("fields", [])},
    }


async def _maybe_confirm(
    request,
    org_id: str,
    addr_id: str | None,
    address_line_1: str,
    address_line_2: str,
    city: str,
    region: str,
    postal_code: str,
    address_type: str,
    display_name: str,
    country: str = "US",
):
    """Call normalizer and return confirm partial if standardized result; else None."""
    raw = " ".join(
        filter(
            None,
            [
                address_line_1.strip(),
                address_line_2.strip(),
                city.strip(),
                region.strip(),
                postal_code.strip(),
            ],
        )
    )
    result = await _NORMALIZER.normalize(raw, country=country)
    if not (result.value and result.value.get("standardized")):
        return None
    validation_status = None
    validation_provider = None
    if result.validation_detail:
        validation_status = result.validation_detail.get("status")
        validation_provider = result.validation_detail.get("provider")
    components_val = result.value.get("components")
    normalized_ctx = {
        "address_line_1": result.value.get("address_line_1") or address_line_1.strip(),
        "address_line_2": result.value.get("address_line_2") or address_line_2.strip(),
        "city": result.value.get("city") or city.strip(),
        "region": result.value.get("region") or region.strip(),
        "postal_code": result.value.get("postal_code") or postal_code.strip(),
        "country": result.value.get("country", country),
        "standardized": result.value.get("standardized"),
        "latitude": result.value.get("latitude"),
        "longitude": result.value.get("longitude"),
        "components_json": json.dumps(components_val) if components_val else "",
    }
    original_ctx = {
        "address_line_1": address_line_1,
        "address_line_2": address_line_2,
        "city": city,
        "region": region,
        "postal_code": postal_code,
        "address_type": address_type,
        "display_name": display_name,
        "country": country,
    }
    if not is_htmx(request):
        return RedirectResponse(f"/admin/orgs/{org_id}/", status_code=303)
    return templates.TemplateResponse(
        request,
        "admin/orgs/partials/_address_confirm_modal.html",
        {
            "org_id": org_id,
            "addr_id": addr_id,
            "normalized": normalized_ctx,
            "original": original_ctx,
            "validation_status": validation_status,
            "validation_provider": validation_provider,
        },
        headers={"HX-Retarget": "#address-confirm-portal", "HX-Reswap": "innerHTML"},
    )


async def _get_org_or_404(org_id: str, db):
    org = await db.fetchrow("SELECT id FROM organizations WHERE id=$1", org_id)
    if not org:
        raise HTTPException(status_code=404, detail="Organization not found")
    return org


async def _get_entity_address_or_404(addr_id: str, org_id: str, db):
    row = await db.fetchrow(
        """SELECT ea.id, ea.address_type, ea.display_name,
                  a.id AS address_id, a.standardized, a.address_line_1, a.address_line_2,
                  a.city, a.region, a.postal_code, a.country
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
    user: AdminUser = Depends(get_admin_user),
    db=Depends(get_db),
):
    """Return empty address form row."""
    await _get_org_or_404(org_id, db)
    ctx = await _field_context("US")
    return templates.TemplateResponse(
        request,
        "admin/orgs/partials/_address_form_row.html",
        {"org_id": org_id, "a": None, **ctx},
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
    mode: str = Form("confirm"),
    standardized: str = Form(""),
    latitude: str = Form(""),
    longitude: str = Form(""),
    components: str = Form(""),
    country: str = Form("US"),
    user: AdminUser = Depends(get_admin_user),
    db=Depends(get_db),
):
    """Create a new organization address."""
    await _get_org_or_404(org_id, db)
    if _is_all_blank(address_line_1, city, region, postal_code):
        if not is_htmx(request):
            return RedirectResponse(f"/admin/orgs/{org_id}/", status_code=303)
        return templates.TemplateResponse(
            request,
            "admin/orgs/partials/_address_form_row.html",
            {
                "org_id": org_id,
                "a": {
                    "id": None,
                    "address_line_1": address_line_1,
                    "address_line_2": address_line_2,
                    "city": city,
                    "region": region,
                    "postal_code": postal_code,
                    "address_type": address_type,
                    "display_name": display_name,
                    "country": country,
                },
                "error": "At least one address field is required.",
                **(await _field_context(country)),
            },
        )
    if mode == "edit":
        if not is_htmx(request):
            return RedirectResponse(f"/admin/orgs/{org_id}/", status_code=303)
        return templates.TemplateResponse(
            request,
            "admin/orgs/partials/_address_form_row.html",
            {
                "org_id": org_id,
                "a": {
                    "id": None,
                    "address_line_1": address_line_1,
                    "address_line_2": address_line_2,
                    "city": city,
                    "region": region,
                    "postal_code": postal_code,
                    "address_type": address_type,
                    "display_name": display_name,
                    "country": country,
                },
                **(await _field_context(country)),
            },
        )
    if mode == "confirm":
        confirm = await _maybe_confirm(
            request,
            org_id,
            None,
            address_line_1,
            address_line_2,
            city,
            region,
            postal_code,
            address_type,
            display_name,
            country,
        )
        if confirm is not None:
            return confirm
    aid = generate_id()
    eaid = generate_id()
    try:
        _standardized, _latitude, _longitude, _components = _parse_normalizer_fields(
            standardized, latitude, longitude, components
        )
    except ValueError:
        if not is_htmx(request):
            return RedirectResponse(f"/admin/orgs/{org_id}/", status_code=303)
        return templates.TemplateResponse(
            request,
            "admin/orgs/partials/_address_form_row.html",
            {
                "org_id": org_id,
                "a": {
                    "id": None,
                    "address_line_1": address_line_1,
                    "address_line_2": address_line_2,
                    "city": city,
                    "region": region,
                    "postal_code": postal_code,
                    "address_type": address_type,
                    "display_name": display_name,
                    "country": country,
                },
                "error": "Invalid address data submitted. Please re-submit the form.",
                **(await _field_context(country)),
            },
        )
    await db.execute(
        "INSERT INTO addresses"
        " (id, address_line_1, address_line_2, city, region, postal_code,"
        "  country, standardized, latitude, longitude, components)"
        " VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)",
        aid,
        address_line_1.strip() or None,
        address_line_2.strip() or None,
        city.strip() or None,
        region.strip() or None,
        postal_code.strip() or None,
        country.strip() or "US",
        _standardized,
        _latitude,
        _longitude,
        _components,
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
    if not is_htmx(request):
        return RedirectResponse(f"/admin/orgs/{org_id}/", status_code=303)
    return templates.TemplateResponse(
        request,
        "admin/orgs/partials/_address_row.html",
        {"org_id": org_id, "a": row},
        headers=flash_trigger("success", "Address added."),
    )


@router.get("/{addr_id}/read-row/")
async def address_read_row(
    org_id: str,
    addr_id: str,
    request: Request,
    user: AdminUser = Depends(get_admin_user),
    db=Depends(get_db),
):
    """Return read-only address row (used by Cancel on edit form)."""
    row = await _get_entity_address_or_404(addr_id, org_id, db)
    return templates.TemplateResponse(
        request, "admin/orgs/partials/_address_row.html", {"org_id": org_id, "a": row}
    )


@router.get("/{addr_id}/edit-row/")
async def address_edit_row_get(
    org_id: str,
    addr_id: str,
    request: Request,
    user: AdminUser = Depends(get_admin_user),
    db=Depends(get_db),
):
    """Return address edit form row."""
    row = await _get_entity_address_or_404(addr_id, org_id, db)
    ctx = await _field_context(row["country"] or "US")
    return templates.TemplateResponse(
        request,
        "admin/orgs/partials/_address_form_row.html",
        {"org_id": org_id, "a": row, **ctx},
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
    mode: str = Form("confirm"),
    standardized: str = Form(""),
    latitude: str = Form(""),
    longitude: str = Form(""),
    components: str = Form(""),
    country: str = Form("US"),
    user: AdminUser = Depends(get_admin_user),
    db=Depends(get_db),
):
    """Update an organization address."""
    existing = await _get_entity_address_or_404(addr_id, org_id, db)
    if _is_all_blank(address_line_1, city, region, postal_code):
        if not is_htmx(request):
            return RedirectResponse(f"/admin/orgs/{org_id}/", status_code=303)
        return templates.TemplateResponse(
            request,
            "admin/orgs/partials/_address_form_row.html",
            {
                "org_id": org_id,
                "a": {
                    "id": addr_id,
                    "address_line_1": address_line_1,
                    "address_line_2": address_line_2,
                    "city": city,
                    "region": region,
                    "postal_code": postal_code,
                    "address_type": address_type,
                    "display_name": display_name,
                    "country": country,
                },
                "error": "At least one address field is required.",
                **(await _field_context(country)),
            },
        )
    if mode == "edit":
        if not is_htmx(request):
            return RedirectResponse(f"/admin/orgs/{org_id}/", status_code=303)
        return templates.TemplateResponse(
            request,
            "admin/orgs/partials/_address_form_row.html",
            {
                "org_id": org_id,
                "a": {
                    "id": addr_id,
                    "address_line_1": address_line_1,
                    "address_line_2": address_line_2,
                    "city": city,
                    "region": region,
                    "postal_code": postal_code,
                    "address_type": address_type,
                    "display_name": display_name,
                    "country": country,
                },
                **(await _field_context(country)),
            },
        )
    if mode == "confirm":
        confirm = await _maybe_confirm(
            request,
            org_id,
            addr_id,
            address_line_1,
            address_line_2,
            city,
            region,
            postal_code,
            address_type,
            display_name,
            country,
        )
        if confirm is not None:
            return confirm
    try:
        _standardized, _latitude, _longitude, _components = _parse_normalizer_fields(
            standardized, latitude, longitude, components
        )
    except ValueError:
        if not is_htmx(request):
            return RedirectResponse(f"/admin/orgs/{org_id}/", status_code=303)
        return templates.TemplateResponse(
            request,
            "admin/orgs/partials/_address_form_row.html",
            {
                "org_id": org_id,
                "a": {
                    "id": addr_id,
                    "address_line_1": address_line_1,
                    "address_line_2": address_line_2,
                    "city": city,
                    "region": region,
                    "postal_code": postal_code,
                    "address_type": address_type,
                    "display_name": display_name,
                    "country": country,
                },
                "error": "Invalid address data submitted. Please re-submit the form.",
                **(await _field_context(country)),
            },
        )
    await db.execute(
        "UPDATE addresses"
        " SET address_line_1=$1, address_line_2=$2, city=$3, region=$4, postal_code=$5,"
        "     country=$6, standardized=$7, latitude=$8, longitude=$9, components=$10"
        " WHERE id=$11",
        address_line_1.strip() or None,
        address_line_2.strip() or None,
        city.strip() or None,
        region.strip() or None,
        postal_code.strip() or None,
        country.strip() or "US",
        _standardized,
        _latitude,
        _longitude,
        _components,
        existing["address_id"],
    )
    await db.execute(
        "UPDATE entity_addresses SET address_type=$1, display_name=$2 WHERE id=$3",
        address_type,
        display_name.strip() or None,
        addr_id,
    )
    row = await _get_entity_address_or_404(addr_id, org_id, db)
    if not is_htmx(request):
        return RedirectResponse(f"/admin/orgs/{org_id}/", status_code=303)
    return templates.TemplateResponse(
        request,
        "admin/orgs/partials/_address_row.html",
        {"org_id": org_id, "a": row},
        headers=flash_trigger("success", "Address saved."),
    )


@router.get("/country-format/")
async def address_country_format(
    org_id: str,
    request: Request,
    country: str = "US",
    user: AdminUser = Depends(get_admin_user),
    db=Depends(get_db),
):
    """Return HTMX partial of structured address fields for the given country code."""
    await _get_org_or_404(org_id, db)
    ctx = await _field_context(country)
    return templates.TemplateResponse(
        request,
        "admin/orgs/partials/_address_fields_partial.html",
        {"org_id": org_id, "a": None, **ctx},
    )


@router.delete("/{addr_id}/")
async def address_delete(
    org_id: str,
    addr_id: str,
    request: Request,
    user: AdminUser = Depends(get_admin_user),
    db=Depends(get_db),
):
    """Delete an org address and cascade-delete its underlying addresses row."""
    existing = await db.fetchrow(
        "SELECT ea.id, ea.address_id FROM entity_addresses ea"
        " WHERE ea.id=$1 AND ea.entity_type='organization' AND ea.entity_id=$2",
        addr_id,
        org_id,
    )
    if not existing:
        raise HTTPException(status_code=404)
    address_id = existing["address_id"]
    async with db.transaction():
        await db.execute("DELETE FROM entity_addresses WHERE id=$1", addr_id)
        await db.execute("DELETE FROM addresses WHERE id=$1", address_id)
    return HTMLResponse(
        content="",
        status_code=200,
        headers=flash_trigger("info", "Address removed."),
    )
