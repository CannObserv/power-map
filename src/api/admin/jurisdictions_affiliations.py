"""Admin CRUD for organization-jurisdiction affiliations (#275 Phase 3).

Bidirectional. The **jurisdiction-scoped** router powers the "Affiliated
organizations" panel on jurisdiction detail (pick an org via typeahead + an
affiliation type); the **org-scoped** router powers the reciprocal "Affiliated
jurisdictions" panel on org detail (pick a jurisdiction). One table, one
uniqueness rule (``uq_org_jur_affiliation`` on org+jur+type) surfaced as a 409.
"""

import asyncpg
from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from src.api.admin.deps import AdminUser, flash_trigger, get_admin_user, get_db, is_htmx
from src.core.db import generate_id

templates = Jinja2Templates(directory="src/templates")

jurisdiction_router = APIRouter(
    prefix="/jurisdictions/{jurisdiction_id}/affiliations",
    tags=["admin-jurisdiction-affiliations"],
)
org_router = APIRouter(
    prefix="/orgs/{org_id}/jurisdiction-affiliations",
    tags=["admin-org-jurisdiction-affiliations"],
)

_JUR_AFF_ROW_SQL = """
    SELECT oja.id AS aff_id, o.id AS org_id, dn.display_name AS org_name,
           ojat.display_name AS affiliation_type
    FROM organization_jurisdiction_affiliations oja
    JOIN organizations o ON o.id = oja.organization_id
    LEFT JOIN v_org_display_names dn ON dn.organization_id = o.id
    JOIN organization_jurisdiction_affiliation_types ojat ON ojat.id = oja.affiliation_type_id
    WHERE oja.id = $1
"""

_ORG_AFF_ROW_SQL = """
    SELECT oja.id AS aff_id, j.id AS jurisdiction_id, j.name AS jurisdiction_name,
           ojat.display_name AS affiliation_type
    FROM organization_jurisdiction_affiliations oja
    JOIN jurisdictions j ON j.id = oja.jurisdiction_id
    JOIN organization_jurisdiction_affiliation_types ojat ON ojat.id = oja.affiliation_type_id
    WHERE oja.id = $1
"""


async def _affiliation_types(db):
    return await db.fetch(
        "SELECT id, display_name FROM organization_jurisdiction_affiliation_types"
        " ORDER BY display_name"
    )


async def _require(db, table: str, entity_id: str, msg: str):
    # table is a fixed literal ('jurisdictions' / 'organizations'), never user input.
    if not await db.fetchrow(f"SELECT id FROM {table} WHERE id=$1", entity_id):
        raise HTTPException(status_code=404, detail=msg)


# ---------------------------------------------------------------------------
# Jurisdiction side — "Affiliated organizations" panel
# ---------------------------------------------------------------------------


async def _render_jur_form(request, jurisdiction_id, db, *, values, errors, status_code=200):
    return templates.TemplateResponse(
        request,
        "admin/jurisdictions/partials/_affiliation_form_row.html",
        {
            "jurisdiction_id": jurisdiction_id,
            "affiliation_types": await _affiliation_types(db),
            "values": values,
            "errors": errors,
        },
        status_code=status_code,
    )


@jurisdiction_router.get("/new-row/")
async def jur_affiliation_new_row(
    jurisdiction_id: str,
    request: Request,
    user: AdminUser = Depends(get_admin_user),
    db=Depends(get_db),
):
    """Empty add-affiliation form row (jurisdiction side)."""
    await _require(db, "jurisdictions", jurisdiction_id, "Jurisdiction not found")
    return await _render_jur_form(request, jurisdiction_id, db, values={}, errors={})


@jurisdiction_router.post("/")
async def jur_affiliation_create(
    jurisdiction_id: str,
    request: Request,
    organization_id: str = Form(""),
    affiliation_type_id: str = Form(""),
    user: AdminUser = Depends(get_admin_user),
    db=Depends(get_db),
):
    """Affiliate an organization with this jurisdiction. Dup (org+jur+type) → 409."""
    await _require(db, "jurisdictions", jurisdiction_id, "Jurisdiction not found")
    values = {"organization_id": organization_id, "affiliation_type_id": affiliation_type_id}
    errors: dict[str, str] = {}
    if not organization_id.strip():
        errors["organization_id"] = "Select an organization"
    if not affiliation_type_id.strip():
        errors["affiliation_type_id"] = "Select an affiliation type"
    if errors:
        return await _render_jur_form(
            request, jurisdiction_id, db, values=values, errors=errors, status_code=422
        )
    aid = generate_id()
    try:
        await db.execute(
            "INSERT INTO organization_jurisdiction_affiliations"
            " (id, organization_id, jurisdiction_id, affiliation_type_id) VALUES ($1, $2, $3, $4)",
            aid,
            organization_id,
            jurisdiction_id,
            affiliation_type_id,
        )
    except asyncpg.UniqueViolationError:
        errors["organization_id"] = "This organization already has that affiliation here"
        return await _render_jur_form(
            request, jurisdiction_id, db, values=values, errors=errors, status_code=409
        )
    except asyncpg.ForeignKeyViolationError:
        errors["organization_id"] = "Unknown organization or affiliation type"
        return await _render_jur_form(
            request, jurisdiction_id, db, values=values, errors=errors, status_code=422
        )
    row = await db.fetchrow(_JUR_AFF_ROW_SQL, aid)
    if not is_htmx(request):
        return RedirectResponse(f"/admin/jurisdictions/{jurisdiction_id}/", status_code=303)
    return templates.TemplateResponse(
        request,
        "admin/jurisdictions/partials/_affiliation_row.html",
        {"jurisdiction_id": jurisdiction_id, "a": row},
        headers=flash_trigger("success", "Affiliation added."),
    )


@jurisdiction_router.delete("/{aff_id}/")
async def jur_affiliation_delete(
    jurisdiction_id: str,
    aff_id: str,
    request: Request,
    user: AdminUser = Depends(get_admin_user),
    db=Depends(get_db),
):
    """Remove an affiliation from the jurisdiction side."""
    existing = await db.fetchrow(
        "SELECT id FROM organization_jurisdiction_affiliations WHERE id=$1 AND jurisdiction_id=$2",
        aff_id,
        jurisdiction_id,
    )
    if not existing:
        raise HTTPException(status_code=404, detail="Affiliation not found")
    await db.execute("DELETE FROM organization_jurisdiction_affiliations WHERE id=$1", aff_id)
    return HTMLResponse(
        content="", status_code=200, headers=flash_trigger("info", "Affiliation removed.")
    )


# ---------------------------------------------------------------------------
# Org side — reciprocal "Affiliated jurisdictions" panel
# ---------------------------------------------------------------------------


async def _render_org_form(request, org_id, db, *, values, errors, status_code=200):
    return templates.TemplateResponse(
        request,
        "admin/orgs/partials/_jurisdiction_affiliation_form_row.html",
        {
            "org_id": org_id,
            "affiliation_types": await _affiliation_types(db),
            "values": values,
            "errors": errors,
        },
        status_code=status_code,
    )


@org_router.get("/new-row/")
async def org_affiliation_new_row(
    org_id: str,
    request: Request,
    user: AdminUser = Depends(get_admin_user),
    db=Depends(get_db),
):
    """Empty add-affiliation form row (org side)."""
    await _require(db, "organizations", org_id, "Organization not found")
    return await _render_org_form(request, org_id, db, values={}, errors={})


@org_router.post("/")
async def org_affiliation_create(
    org_id: str,
    request: Request,
    jurisdiction_id: str = Form(""),
    affiliation_type_id: str = Form(""),
    user: AdminUser = Depends(get_admin_user),
    db=Depends(get_db),
):
    """Affiliate a jurisdiction with this org. Dup (org+jur+type) → 409."""
    await _require(db, "organizations", org_id, "Organization not found")
    values = {"jurisdiction_id": jurisdiction_id, "affiliation_type_id": affiliation_type_id}
    errors: dict[str, str] = {}
    if not jurisdiction_id.strip():
        errors["jurisdiction_id"] = "Select a jurisdiction"
    if not affiliation_type_id.strip():
        errors["affiliation_type_id"] = "Select an affiliation type"
    if errors:
        return await _render_org_form(
            request, org_id, db, values=values, errors=errors, status_code=422
        )
    aid = generate_id()
    try:
        await db.execute(
            "INSERT INTO organization_jurisdiction_affiliations"
            " (id, organization_id, jurisdiction_id, affiliation_type_id) VALUES ($1, $2, $3, $4)",
            aid,
            org_id,
            jurisdiction_id,
            affiliation_type_id,
        )
    except asyncpg.UniqueViolationError:
        errors["jurisdiction_id"] = "This jurisdiction already has that affiliation here"
        return await _render_org_form(
            request, org_id, db, values=values, errors=errors, status_code=409
        )
    except asyncpg.ForeignKeyViolationError:
        errors["jurisdiction_id"] = "Unknown jurisdiction or affiliation type"
        return await _render_org_form(
            request, org_id, db, values=values, errors=errors, status_code=422
        )
    row = await db.fetchrow(_ORG_AFF_ROW_SQL, aid)
    if not is_htmx(request):
        return RedirectResponse(f"/admin/orgs/{org_id}/", status_code=303)
    return templates.TemplateResponse(
        request,
        "admin/orgs/partials/_jurisdiction_affiliation_row.html",
        {"org_id": org_id, "a": row},
        headers=flash_trigger("success", "Affiliation added."),
    )


@org_router.delete("/{aff_id}/")
async def org_affiliation_delete(
    org_id: str,
    aff_id: str,
    request: Request,
    user: AdminUser = Depends(get_admin_user),
    db=Depends(get_db),
):
    """Remove an affiliation from the org side."""
    existing = await db.fetchrow(
        "SELECT id FROM organization_jurisdiction_affiliations WHERE id=$1 AND organization_id=$2",
        aff_id,
        org_id,
    )
    if not existing:
        raise HTTPException(status_code=404, detail="Affiliation not found")
    await db.execute("DELETE FROM organization_jurisdiction_affiliations WHERE id=$1", aff_id)
    return HTMLResponse(
        content="", status_code=200, headers=flash_trigger("info", "Affiliation removed.")
    )
