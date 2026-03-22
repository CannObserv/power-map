"""Admin views for organizations."""

import asyncpg
from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from markupsafe import escape

from src.api.admin.deps import AdminUser, check_auth, get_admin_user, get_db
from src.api.admin.org_dups import (
    CANDIDATE_WHERE,
    get_org_dup_count,
    invalidate_dup_count_cache,
)
from src.api.admin.pagination import pagination_context
from src.core.db import generate_id
from src.core.logging import get_logger

logger = get_logger(__name__)

templates = Jinja2Templates(directory="src/templates")
router = APIRouter(prefix="/orgs", tags=["admin-orgs"])


@router.get("/")
async def orgs_list(
    request: Request,
    q: str = "",
    status: str = "active",
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=10, le=500),
    user: AdminUser | RedirectResponse = Depends(get_admin_user),
    db=Depends(get_db),
    org_dup_count: int = Depends(get_org_dup_count),
):
    """List organizations with search and status filter."""
    redirect, user = check_auth(user)
    if redirect:
        return redirect

    conditions = []
    params: list = []

    if status == "active":
        conditions.append("o.archived_at IS NULL AND o.active = TRUE")
    elif status == "inactive":
        conditions.append("o.archived_at IS NULL AND o.active = FALSE")
    elif status == "archived":
        conditions.append("o.archived_at IS NOT NULL")

    if q:
        params.append(f"%{q}%")
        conditions.append(f"dn.display_name ILIKE ${len(params)}")

    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
    count_params = params[:]

    count = await db.fetchval(
        f"""SELECT count(DISTINCT o.id)
            FROM organizations o
            LEFT JOIN v_org_display_names dn ON dn.organization_id = o.id
            {where}""",
        *count_params,
    )

    pctx = pagination_context(page, count, page_size)
    offset = (pctx["page"] - 1) * page_size
    list_params = params + [page_size, offset]

    rows = await db.fetch(
        f"""SELECT o.id, o.active, o.archived_at, o.created_at,
                   dn.display_name AS canonical_name
            FROM organizations o
            LEFT JOIN v_org_display_names dn ON dn.organization_id = o.id
            {where}
            ORDER BY dn.display_name NULLS LAST
            LIMIT ${len(list_params) - 1} OFFSET ${len(list_params)}""",
        *list_params,
    )

    ctx = {
        "user": user,
        "active_section": "orgs",
        "orgs": rows,
        "q": q,
        "status": status,
        "page_size": page_size,
        "total": count,
        "org_dup_count": org_dup_count,
        **pctx,
    }
    template = (
        "admin/orgs/_region.html"
        if request.headers.get("HX-Request") and not request.headers.get("HX-Boosted")
        else "admin/orgs/list.html"
    )
    return templates.TemplateResponse(request, template, ctx)


@router.get("/new/")
async def org_new_form(
    request: Request,
    user: AdminUser | RedirectResponse = Depends(get_admin_user),
    db=Depends(get_db),
    org_dup_count: int = Depends(get_org_dup_count),
):
    """New organization form."""
    redirect, user = check_auth(user)
    if redirect:
        return redirect
    parents = await db.fetch(
        """SELECT o.id, dn.display_name AS canonical_name
           FROM organizations o
           LEFT JOIN v_org_display_names dn ON dn.organization_id = o.id
           WHERE o.archived_at IS NULL ORDER BY dn.display_name NULLS LAST"""
    )
    return templates.TemplateResponse(
        request,
        "admin/orgs/form.html",
        {
            "user": user,
            "active_section": "orgs",
            "org": None,
            "parents": parents,
            "canonical_name": "",
            "canonical_acronym": "",
            "org_dup_count": org_dup_count,
        },
    )


@router.post("/new/")
async def org_create(
    request: Request,
    name: str = Form(...),
    acronym: str = Form(""),
    active: str = Form(""),
    parent_id: str = Form(""),
    notes: str = Form(""),
    user: AdminUser | RedirectResponse = Depends(get_admin_user),
    db=Depends(get_db),
):
    """Create a new organization."""
    redirect, user = check_auth(user)
    if redirect:
        return redirect
    org_id = generate_id()
    async with db.transaction():
        await db.execute(
            "INSERT INTO organizations (id, active, parent_id, notes) VALUES ($1, $2, $3, $4)",
            org_id, active == "true", parent_id or None, notes or None,
        )
        await db.execute(
            "INSERT INTO organization_names"
            " (id, organization_id, name, is_canonical) VALUES ($1, $2, $3, TRUE)",
            generate_id(), org_id, name,
        )
        if acronym.strip():
            await db.execute(
                "INSERT INTO organization_acronyms"
                " (id, organization_id, acronym, is_canonical) VALUES ($1, $2, $3, TRUE)",
                generate_id(), org_id, acronym.strip(),
            )
    return RedirectResponse(f"/admin/orgs/{org_id}/", status_code=303)


async def _fetch_duplicate_pairs(db) -> list:
    """Return near-duplicate org pairs; empty list if pg_trgm not installed."""
    try:
        return await db.fetch(
            f"""SELECT
                a.id AS a_id, dn_a.display_name AS a_name, a.created_at AS a_created,
                b.id AS b_id, dn_b.display_name AS b_name, b.created_at AS b_created,
                similarity(dn_a.display_name, dn_b.display_name) AS score,
                (SELECT count(*) FROM roles
                 WHERE organization_id = a.id AND archived_at IS NULL) AS a_roles,
                (SELECT count(*) FROM roles
                 WHERE organization_id = b.id AND archived_at IS NULL) AS b_roles
            {CANDIDATE_WHERE}
            ORDER BY score DESC"""
        )
    except asyncpg.exceptions.UndefinedFunctionError:
        return []


def _is_htmx(request: Request) -> bool:
    return bool(
        request.headers.get("HX-Request") and not request.headers.get("HX-Boosted")
    )


@router.get("/duplicates/")
async def orgs_duplicates(
    request: Request,
    user: AdminUser | RedirectResponse = Depends(get_admin_user),
    db=Depends(get_db),
    org_dup_count: int = Depends(get_org_dup_count),
):
    """List near-duplicate organization pairs for review."""
    redirect, user = check_auth(user)
    if redirect:
        return redirect
    pairs = await _fetch_duplicate_pairs(db)
    ctx = {
        "user": user,
        "active_section": "orgs_duplicates",
        "pairs": pairs,
        "org_dup_count": org_dup_count,
    }
    template = (
        "admin/orgs/_duplicates_region.html"
        if _is_htmx(request)
        else "admin/orgs/duplicates.html"
    )
    return templates.TemplateResponse(request, template, ctx)


@router.post("/{winner_id}/merge/{loser_id}/")
async def org_merge(
    winner_id: str,
    loser_id: str,
    request: Request,
    user: AdminUser | RedirectResponse = Depends(get_admin_user),
    db=Depends(get_db),
):
    """Merge loser into winner: reassign all references, hard-delete loser."""
    redirect, user = check_auth(user)
    if redirect:
        return redirect
    # Fetch display names before transaction for flash message
    winner_name = await db.fetchval(
        "SELECT display_name FROM v_org_display_names WHERE organization_id=$1", winner_id
    )
    loser_name = await db.fetchval(
        "SELECT display_name FROM v_org_display_names WHERE organization_id=$1", loser_id
    )
    async with db.transaction():
        winner = await db.fetchrow(
            "SELECT id FROM organizations WHERE id=$1 FOR UPDATE", winner_id
        )
        loser = await db.fetchrow(
            "SELECT id FROM organizations WHERE id=$1 FOR UPDATE", loser_id
        )
        if not winner or not loser:
            raise HTTPException(status_code=404, detail="Organization not found")
        # organizations.parent_id (FK — reassign before deleting loser)
        await db.execute(
            "UPDATE organizations SET parent_id=$1 WHERE parent_id=$2",
            winner_id, loser_id,
        )
        # organization_names
        await db.execute(
            "UPDATE organization_names SET organization_id=$1"
            " WHERE organization_id=$2 AND is_canonical=FALSE",
            winner_id, loser_id,
        )
        await db.execute(
            "DELETE FROM organization_names"
            " WHERE organization_id=$1 AND is_canonical=TRUE",
            loser_id,
        )
        # organization_acronyms
        await db.execute(
            "UPDATE organization_acronyms SET organization_id=$1"
            " WHERE organization_id=$2 AND is_canonical=FALSE",
            winner_id, loser_id,
        )
        await db.execute(
            "DELETE FROM organization_acronyms"
            " WHERE organization_id=$1 AND is_canonical=TRUE",
            loser_id,
        )
        # roles
        await db.execute(
            "UPDATE roles SET organization_id=$1 WHERE organization_id=$2",
            winner_id, loser_id,
        )
        # urls: demote loser's canonical url if winner already has one to avoid
        # uq_url_canonical violation before the bulk reassignment below
        await db.execute(
            "UPDATE urls SET is_canonical=FALSE"
            " WHERE entity_type='organization' AND entity_id=$1 AND is_canonical=TRUE"
            " AND EXISTS ("
            "   SELECT 1 FROM urls"
            "   WHERE entity_type='organization' AND entity_id=$2 AND is_canonical=TRUE"
            " )",
            loser_id, winner_id,
        )
        # Polymorphic entity tables (entity_type TEXT + entity_id TEXT, no FK)
        for table in ("entity_addresses", "contact_methods", "urls",
                      "social_links", "import_provenance", "field_confidence"):
            await db.execute(
                f"UPDATE {table} SET entity_id=$1"
                f" WHERE entity_type='organization' AND entity_id=$2",
                winner_id, loser_id,
            )
        # identifiers (entity_type encoded in entity_identifier_type_id, not a column)
        await db.execute(
            "UPDATE identifiers SET entity_id=$1 WHERE entity_id=$2",
            winner_id, loser_id,
        )
        await db.execute("DELETE FROM organizations WHERE id=$1", loser_id)
    invalidate_dup_count_cache()
    if _is_htmx(request):
        pairs = await _fetch_duplicate_pairs(db)
        flash_body = (
            f'Merged <strong>{escape(loser_name)}</strong> into '
            f'<a href="/admin/orgs/{winner_id}/"><strong>{escape(winner_name)}</strong></a>. '
            f'Review URLs, roles, and contact info for duplicates.'
        )
        ctx = {
            "user": user,
            "active_section": "orgs_duplicates",
            "pairs": pairs,
            "flash_level": "success",
            "flash_body": flash_body,
        }
        return templates.TemplateResponse(request, "admin/orgs/_duplicates_region.html", ctx)
    return RedirectResponse("/admin/orgs/duplicates/", status_code=303)


@router.post("/{id_a}/dismiss-duplicate/{id_b}/")
async def org_dismiss_duplicate(
    id_a: str,
    id_b: str,
    request: Request,
    user: AdminUser | RedirectResponse = Depends(get_admin_user),
    db=Depends(get_db),
):
    """Record that this pair is not a duplicate (suppress from future results)."""
    redirect, user = check_auth(user)
    if redirect:
        return redirect
    # Store with consistent ordering (a < b)
    a, b = (id_a, id_b) if id_a < id_b else (id_b, id_a)
    await db.execute(
        "INSERT INTO duplicate_dismissals"
        " (id, entity_type, entity_a_id, entity_b_id, dismissed_by)"
        " VALUES ($1, 'organization', $2, $3, $4)"
        " ON CONFLICT (entity_type, entity_a_id, entity_b_id) DO NOTHING",
        generate_id(), a, b, user.email,
    )
    invalidate_dup_count_cache()
    if _is_htmx(request):
        pairs = await _fetch_duplicate_pairs(db)
        ctx = {
            "user": user,
            "active_section": "orgs_duplicates",
            "pairs": pairs,
            "flash_level": "info",
            "flash_body": "Pair marked as not a duplicate.",
        }
        return templates.TemplateResponse(request, "admin/orgs/_duplicates_region.html", ctx)
    return RedirectResponse("/admin/orgs/duplicates/", status_code=303)


@router.get("/{org_id}/")
async def org_detail(
    org_id: str,
    request: Request,
    user: AdminUser | RedirectResponse = Depends(get_admin_user),
    db=Depends(get_db),
    org_dup_count: int = Depends(get_org_dup_count),
):
    """Organization detail view."""
    redirect, user = check_auth(user)
    if redirect:
        return redirect

    org = await db.fetchrow("SELECT * FROM organizations WHERE id = $1", org_id)
    if not org:
        raise HTTPException(status_code=404, detail="Organization not found")

    names = await db.fetch(
        "SELECT * FROM organization_names WHERE organization_id = $1 ORDER BY is_canonical DESC",
        org_id,
    )
    acronyms = await db.fetch(
        "SELECT * FROM organization_acronyms WHERE organization_id = $1 ORDER BY is_canonical DESC",
        org_id,
    )
    addresses = await db.fetch(
        """SELECT ea.*, a.standardized, a.address_line_1, a.city, a.region, a.postal_code
           FROM entity_addresses ea JOIN addresses a ON a.id = ea.address_id
           WHERE ea.entity_type = 'organization' AND ea.entity_id = $1""",
        org_id,
    )
    contacts = await db.fetch(
        "SELECT * FROM contact_methods WHERE entity_type = 'organization' AND entity_id = $1",
        org_id,
    )
    urls = await db.fetch(
        """SELECT u.*, ut.display_name AS url_type_name
           FROM urls u JOIN url_types ut ON ut.id = u.url_type_id
           WHERE u.entity_type = 'organization' AND u.entity_id = $1""",
        org_id,
    )
    social = await db.fetch(
        """SELECT sl.*, p.display_name AS platform_name
           FROM social_links sl JOIN platforms p ON p.id = sl.platform_id
           WHERE sl.entity_type = 'organization' AND sl.entity_id = $1""",
        org_id,
    )
    identifiers = await db.fetch(
        """SELECT i.*, eit.display_name AS type_name, eit.full_name AS type_full_name
           FROM identifiers i
           JOIN entity_identifier_types eit ON eit.id = i.entity_identifier_type_id
           WHERE i.entity_id = $1""",
        org_id,
    )
    children = await db.fetch(
        """SELECT o.id, o.active, o.archived_at, dn.display_name AS canonical_name
           FROM organizations o
           LEFT JOIN v_org_display_names dn ON dn.organization_id = o.id
           WHERE o.parent_id = $1 ORDER BY dn.display_name""",
        org_id,
    )
    roles = await db.fetch(
        "SELECT * FROM roles WHERE organization_id = $1 AND archived_at IS NULL ORDER BY title",
        org_id,
    )

    return templates.TemplateResponse(
        request,
        "admin/orgs/detail.html",
        {
            "user": user,
            "active_section": "orgs",
            "org": org,
            "names": names,
            "acronyms": acronyms,
            "addresses": addresses,
            "contacts": contacts,
            "urls": urls,
            "social": social,
            "identifiers": identifiers,
            "children": children,
            "roles": roles,
            "org_dup_count": org_dup_count,
        },
    )


@router.get("/{org_id}/edit/")
async def org_edit_form(
    org_id: str,
    request: Request,
    user: AdminUser | RedirectResponse = Depends(get_admin_user),
    db=Depends(get_db),
    org_dup_count: int = Depends(get_org_dup_count),
):
    """Edit organization form."""
    redirect, user = check_auth(user)
    if redirect:
        return redirect
    org = await db.fetchrow("SELECT * FROM organizations WHERE id = $1", org_id)
    if not org:
        raise HTTPException(status_code=404, detail="Organization not found")
    canonical = await db.fetchrow(
        "SELECT name FROM organization_names"
        " WHERE organization_id = $1 AND is_canonical = TRUE",
        org_id,
    )
    parents = await db.fetch(
        """SELECT o.id, dn.display_name AS canonical_name
           FROM organizations o
           LEFT JOIN v_org_display_names dn ON dn.organization_id = o.id
           WHERE o.archived_at IS NULL AND o.id != $1 ORDER BY dn.display_name NULLS LAST""",
        org_id,
    )
    canonical_acronym_row = await db.fetchrow(
        "SELECT acronym FROM organization_acronyms"
        " WHERE organization_id = $1 AND is_canonical = TRUE",
        org_id,
    )
    return templates.TemplateResponse(
        request,
        "admin/orgs/form.html",
        {
            "user": user,
            "active_section": "orgs",
            "org": org,
            "canonical_name": canonical["name"] if canonical else "",
            "canonical_acronym": canonical_acronym_row["acronym"] if canonical_acronym_row else "",
            "parents": parents,
            "org_dup_count": org_dup_count,
        },
    )


@router.post("/{org_id}/edit/")
async def org_update(
    org_id: str,
    request: Request,
    name: str = Form(...),
    acronym: str = Form(""),
    active: str = Form(""),
    parent_id: str = Form(""),
    notes: str = Form(""),
    user: AdminUser | RedirectResponse = Depends(get_admin_user),
    db=Depends(get_db),
):
    """Update an organization."""
    redirect, user = check_auth(user)
    if redirect:
        return redirect
    org = await db.fetchrow("SELECT id FROM organizations WHERE id = $1", org_id)
    if not org:
        raise HTTPException(status_code=404, detail="Organization not found")
    async with db.transaction():
        await db.execute(
            "UPDATE organizations SET active = $1, parent_id = $2, notes = $3 WHERE id = $4",
            active == "true", parent_id or None, notes or None, org_id,
        )
        existing = await db.fetchrow(
            "SELECT id FROM organization_names"
            " WHERE organization_id = $1 AND is_canonical = TRUE",
            org_id,
        )
        if existing:
            await db.execute(
                "UPDATE organization_names SET name = $1 WHERE id = $2", name, existing["id"]
            )
        else:
            await db.execute(
                "INSERT INTO organization_names"
                " (id, organization_id, name, is_canonical) VALUES ($1, $2, $3, TRUE)",
                generate_id(), org_id, name,
            )
        acronym_stripped = acronym.strip()
        existing_acronym = await db.fetchrow(
            "SELECT id FROM organization_acronyms"
            " WHERE organization_id = $1 AND is_canonical = TRUE",
            org_id,
        )
        if acronym_stripped:
            if existing_acronym:
                await db.execute(
                    "UPDATE organization_acronyms SET acronym = $1 WHERE id = $2",
                    acronym_stripped, existing_acronym["id"],
                )
            else:
                await db.execute(
                    "INSERT INTO organization_acronyms"
                    " (id, organization_id, acronym, is_canonical) VALUES ($1, $2, $3, TRUE)",
                    generate_id(), org_id, acronym_stripped,
                )
        elif existing_acronym:
            await db.execute(
                "DELETE FROM organization_acronyms WHERE id = $1", existing_acronym["id"]
            )
    return RedirectResponse(f"/admin/orgs/{org_id}/", status_code=303)


@router.post("/{org_id}/archive/")
async def org_archive(
    org_id: str,
    request: Request,
    user: AdminUser | RedirectResponse = Depends(get_admin_user),
    db=Depends(get_db),
):
    """Archive an organization (soft delete)."""
    redirect, user = check_auth(user)
    if redirect:
        return redirect
    org = await db.fetchrow("SELECT id FROM organizations WHERE id = $1", org_id)
    if not org:
        raise HTTPException(status_code=404, detail="Organization not found")
    await db.execute("UPDATE organizations SET archived_at = NOW() WHERE id = $1", org_id)
    return RedirectResponse(f"/admin/orgs/{org_id}/", status_code=303)


@router.delete("/{org_id}/")
async def org_delete(
    org_id: str,
    request: Request,
    user: AdminUser | RedirectResponse = Depends(get_admin_user),
    db=Depends(get_db),
):
    """Hard delete an archived organization."""
    redirect, user = check_auth(user)
    if redirect:
        return redirect
    org = await db.fetchrow("SELECT id, archived_at FROM organizations WHERE id = $1", org_id)
    if not org:
        raise HTTPException(status_code=404, detail="Organization not found")
    if not org["archived_at"]:
        raise HTTPException(status_code=409, detail="Organization must be archived before deletion")
    try:
        await db.execute("DELETE FROM organization_acronyms WHERE organization_id = $1", org_id)
        await db.execute("DELETE FROM organization_names WHERE organization_id = $1", org_id)
        await db.execute("DELETE FROM organizations WHERE id = $1", org_id)
    except asyncpg.ForeignKeyViolationError:
        raise HTTPException(
            status_code=409,
            detail="Cannot delete: organization has related records (roles, etc.)",
        )
    return HTMLResponse(content="", status_code=200)
