"""Admin views for organizations."""

import asyncpg
from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from markupsafe import escape

from src.api.admin.deps import (
    AdminUser,
    check_auth,
    flash_trigger,
    get_admin_user,
    get_db,
    is_htmx,
)
from src.api.admin.org_dups import (
    CANDIDATE_WHERE,
    get_org_dup_count,
    invalidate_dup_count_cache,
)
from src.api.admin.pagination import pagination_context
from src.api.admin.people_dups import get_person_dup_count
from src.core.db import generate_id
from src.core.logging import get_logger

logger = get_logger(__name__)

templates = Jinja2Templates(directory="src/templates")
router = APIRouter(prefix="/orgs", tags=["admin-orgs"])


_FLASH_MESSAGES: dict[str, tuple[str, str]] = {
    "deleted": ("success", "Organization deleted."),
}


@router.get("/")
async def orgs_list(
    request: Request,
    q: str = "",
    status: str = "active",
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=10, le=500),
    flash: str | None = Query(None),
    user: AdminUser | RedirectResponse = Depends(get_admin_user),
    db=Depends(get_db),
    org_dup_count: int = Depends(get_org_dup_count),
    person_dup_count: int = Depends(get_person_dup_count),
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
        escaped = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        params.append(f"%{escaped}%")
        conditions.append(f"dn.display_name ILIKE ${len(params)} ESCAPE '\\'")

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

    flash_pair = _FLASH_MESSAGES.get(flash)
    flash_msg = {"level": flash_pair[0], "body": flash_pair[1]} if flash_pair else None

    ctx = {
        "user": user,
        "active_section": "orgs",
        "orgs": rows,
        "q": q,
        "status": status,
        "page_size": page_size,
        "total": count,
        "org_dup_count": org_dup_count,
        "person_dup_count": person_dup_count,
        "flash_msg": flash_msg,
        **pctx,
    }
    htmx = is_htmx(request)
    template = "admin/orgs/_region.html" if htmx else "admin/orgs/list.html"
    resp_headers = {}
    if flash_msg and not htmx:
        resp_headers["HX-Replace-Url"] = str(request.url.remove_query_params("flash"))
    return templates.TemplateResponse(request, template, ctx, headers=resp_headers)


async def _fetch_parents(db) -> list:
    """Fetch all non-archived orgs for the parent dropdown."""
    return await db.fetch(
        """SELECT o.id, dn.display_name AS canonical_name
           FROM organizations o
           LEFT JOIN v_org_display_names dn ON dn.organization_id = o.id
           WHERE o.archived_at IS NULL ORDER BY dn.display_name NULLS LAST"""
    )


@router.get("/new/")
async def org_new_form(
    request: Request,
    user: AdminUser | RedirectResponse = Depends(get_admin_user),
    db=Depends(get_db),
    org_dup_count: int = Depends(get_org_dup_count),
    person_dup_count: int = Depends(get_person_dup_count),
):
    """New organization form."""
    redirect, user = check_auth(user)
    if redirect:
        return redirect
    parents = await _fetch_parents(db)
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
            "org_notes": "",
            "org_dup_count": org_dup_count,
            "person_dup_count": person_dup_count,
            "errors": {},
        },
    )


@router.post("/new/")
async def org_create(
    request: Request,
    name: str = Form(""),
    acronym: str = Form(""),
    active: str = Form(""),
    parent_id: str = Form(""),
    notes: str = Form(""),
    user: AdminUser | RedirectResponse = Depends(get_admin_user),
    db=Depends(get_db),
    org_dup_count: int = Depends(get_org_dup_count),
    person_dup_count: int = Depends(get_person_dup_count),
):
    """Create a new organization."""
    redirect, user = check_auth(user)
    if redirect:
        return redirect
    if not name.strip():
        parents = await _fetch_parents(db)
        return templates.TemplateResponse(
            request,
            "admin/orgs/form.html",
            {
                "user": user,
                "active_section": "orgs",
                "org": None,
                "parents": parents,
                "canonical_name": name,
                "canonical_acronym": acronym,
                "org_notes": notes,
                "org_dup_count": org_dup_count,
                "person_dup_count": person_dup_count,
                "errors": {"name": "Name is required"},
            },
            status_code=422,
        )
    org_id = generate_id()
    async with db.transaction():
        await db.execute(
            "INSERT INTO organizations (id, active, parent_id, notes) VALUES ($1, $2, $3, $4)",
            org_id, active == "true", parent_id or None, notes or None,
        )
        await db.execute(
            "INSERT INTO organization_names"
            " (id, organization_id, name, is_canonical) VALUES ($1, $2, $3, TRUE)",
            generate_id(), org_id, name.strip(),
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


@router.get("/search/")
async def orgs_search(
    request: Request,
    q: str = "",
    user: AdminUser | RedirectResponse = Depends(get_admin_user),
    db=Depends(get_db),
):
    """Typeahead search — returns an HTML fragment of matching org options."""
    redirect, user = check_auth(user)
    if redirect:
        return redirect
    results = []
    if q.strip():
        escaped = q.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        results = await db.fetch(
            """SELECT o.id, dn.display_name
               FROM organizations o
               LEFT JOIN v_org_display_names dn ON dn.organization_id = o.id
               WHERE o.archived_at IS NULL
                 AND dn.display_name ILIKE $1 ESCAPE '\\'
               ORDER BY dn.display_name NULLS LAST
               LIMIT 20""",
            f"%{escaped}%",
        )
    return templates.TemplateResponse(
        request,
        "admin/orgs/partials/_search_results.html",
        {"results": results},
    )


@router.get("/duplicates/")
async def orgs_duplicates(
    request: Request,
    user: AdminUser | RedirectResponse = Depends(get_admin_user),
    db=Depends(get_db),
    org_dup_count: int = Depends(get_org_dup_count),
    person_dup_count: int = Depends(get_person_dup_count),
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
        "person_dup_count": person_dup_count,
    }
    template = (
        "admin/orgs/_duplicates_region.html"
        if is_htmx(request)
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
        # links: demote loser's canonical link if winner already has one to avoid
        # uq_link_canonical violation before the bulk reassignment below
        await db.execute(
            "UPDATE links SET is_canonical=FALSE"
            " WHERE entity_type='organization' AND entity_id=$1 AND is_canonical=TRUE"
            " AND EXISTS ("
            "   SELECT 1 FROM links"
            "   WHERE entity_type='organization' AND entity_id=$2 AND is_canonical=TRUE"
            " )",
            loser_id, winner_id,
        )
        # Polymorphic entity tables (entity_type TEXT + entity_id TEXT, no FK)
        for table in ("entity_addresses", "contact_methods", "links",
                      "import_provenance", "field_confidence"):
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
        # duplicate_dismissals: delete the merged pair, reassign any others referencing loser
        await db.execute(
            "DELETE FROM duplicate_dismissals"
            " WHERE entity_type='organization'"
            "   AND ((entity_a_id=$1 AND entity_b_id=$2)"
            "    OR  (entity_a_id=$2 AND entity_b_id=$1))",
            winner_id, loser_id,
        )
        # Delete loser dismissals that would conflict with existing winner dismissals
        await db.execute(
            """DELETE FROM duplicate_dismissals dd
               USING duplicate_dismissals dw
               WHERE dd.entity_type = 'organization'
                 AND dw.entity_type = 'organization'
                 AND dw.entity_a_id = $2
                 AND (
                   (dd.entity_a_id = $1 AND dd.entity_b_id = dw.entity_b_id)
                   OR (dd.entity_b_id = $1 AND dd.entity_a_id = dw.entity_b_id)
                 )""",
            loser_id, winner_id,
        )
        await db.execute(
            """DELETE FROM duplicate_dismissals dd
               USING duplicate_dismissals dw
               WHERE dd.entity_type = 'organization'
                 AND dw.entity_type = 'organization'
                 AND dw.entity_b_id = $2
                 AND (
                   (dd.entity_a_id = $1 AND dd.entity_b_id = dw.entity_a_id)
                   OR (dd.entity_b_id = $1 AND dd.entity_a_id = dw.entity_a_id)
                 )""",
            loser_id, winner_id,
        )
        await db.execute(
            """UPDATE duplicate_dismissals
               SET entity_a_id = LEAST($1, entity_b_id),
                   entity_b_id = GREATEST($1, entity_b_id)
               WHERE entity_type='organization' AND entity_a_id=$2""",
            winner_id, loser_id,
        )
        await db.execute(
            """UPDATE duplicate_dismissals
               SET entity_a_id = LEAST(entity_a_id, $1),
                   entity_b_id = GREATEST(entity_a_id, $1)
               WHERE entity_type='organization' AND entity_b_id=$2""",
            winner_id, loser_id,
        )
        await db.execute("DELETE FROM organizations WHERE id=$1", loser_id)
    invalidate_dup_count_cache()
    if is_htmx(request):
        pairs = await _fetch_duplicate_pairs(db)
        body = (
            f'Merged <strong>{escape(loser_name)}</strong> into '
            f'<a href="/admin/orgs/{winner_id}/"><strong>{escape(winner_name)}</strong></a>. '
            f'Review URLs, roles, and contact info for duplicates.'
        )
        ctx = {
            "user": user,
            "active_section": "orgs_duplicates",
            "pairs": pairs,
        }
        return templates.TemplateResponse(
            request,
            "admin/orgs/_duplicates_region.html",
            ctx,
            headers=flash_trigger("success", body),
        )
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
    if is_htmx(request):
        pairs = await _fetch_duplicate_pairs(db)
        ctx = {
            "user": user,
            "active_section": "orgs_duplicates",
            "pairs": pairs,
        }
        return templates.TemplateResponse(
            request,
            "admin/orgs/_duplicates_region.html",
            ctx,
            headers=flash_trigger("info", "Pair marked as not a duplicate."),
        )
    return RedirectResponse("/admin/orgs/duplicates/", status_code=303)


@router.post("/{org_id}/inline/active/")
async def org_inline_active_post(
    org_id: str,
    request: Request,
    active: str = Form(""),
    user: AdminUser | RedirectResponse = Depends(get_admin_user),
    db=Depends(get_db),
):
    """Toggle org active flag; return updated active-toggle partial."""
    redirect, user = check_auth(user)
    if redirect:
        return redirect
    org = await db.fetchrow("SELECT * FROM organizations WHERE id=$1", org_id)
    if not org:
        raise HTTPException(status_code=404)
    new_active = active == "true"
    await db.execute(
        "UPDATE organizations SET active=$1 WHERE id=$2", new_active, org_id
    )
    org = await db.fetchrow("SELECT * FROM organizations WHERE id=$1", org_id)
    if not is_htmx(request):
        return RedirectResponse(f"/admin/orgs/{org_id}/", status_code=303)
    label = "Marked active." if new_active else "Marked inactive."
    level = "success" if new_active else "info"
    return templates.TemplateResponse(
        request,
        "admin/orgs/partials/_active_toggle.html",
        {"org": org},
        headers=flash_trigger(level, label),
    )


@router.get("/{org_id}/inline/notes/")
async def org_inline_notes_get(
    org_id: str,
    request: Request,
    user: AdminUser | RedirectResponse = Depends(get_admin_user),
    db=Depends(get_db),
):
    """Return notes read partial."""
    redirect, user = check_auth(user)
    if redirect:
        return redirect
    org = await db.fetchrow("SELECT * FROM organizations WHERE id=$1", org_id)
    if not org:
        raise HTTPException(status_code=404)
    return templates.TemplateResponse(
        request, "admin/orgs/partials/_notes_read.html", {"org": org}
    )


@router.get("/{org_id}/inline/notes/edit/")
async def org_inline_notes_edit_get(
    org_id: str,
    request: Request,
    user: AdminUser | RedirectResponse = Depends(get_admin_user),
    db=Depends(get_db),
):
    """Return notes edit form partial."""
    redirect, user = check_auth(user)
    if redirect:
        return redirect
    org = await db.fetchrow("SELECT * FROM organizations WHERE id=$1", org_id)
    if not org:
        raise HTTPException(status_code=404)
    return templates.TemplateResponse(
        request, "admin/orgs/partials/_notes_form.html", {"org": org}
    )


@router.post("/{org_id}/inline/notes/")
async def org_inline_notes_post(
    org_id: str,
    request: Request,
    notes: str = Form(""),
    user: AdminUser | RedirectResponse = Depends(get_admin_user),
    db=Depends(get_db),
):
    """Save notes; return updated notes read partial."""
    redirect, user = check_auth(user)
    if redirect:
        return redirect
    org = await db.fetchrow("SELECT * FROM organizations WHERE id=$1", org_id)
    if not org:
        raise HTTPException(status_code=404)
    await db.execute(
        "UPDATE organizations SET notes=$1 WHERE id=$2",
        notes.strip() or None,
        org_id,
    )
    org = await db.fetchrow("SELECT * FROM organizations WHERE id=$1", org_id)
    if not is_htmx(request):
        return RedirectResponse(f"/admin/orgs/{org_id}/", status_code=303)
    return templates.TemplateResponse(
        request,
        "admin/orgs/partials/_notes_read.html",
        {"org": org},
        headers=flash_trigger("success", "Notes saved."),
    )


@router.get("/{org_id}/inline/parent/")
async def org_inline_parent_get(
    org_id: str,
    request: Request,
    user: AdminUser | RedirectResponse = Depends(get_admin_user),
    db=Depends(get_db),
):
    """Return read partial for parent org field."""
    redirect, user = check_auth(user)
    if redirect:
        return redirect
    org = await db.fetchrow("SELECT * FROM organizations WHERE id=$1", org_id)
    if not org:
        raise HTTPException(status_code=404)
    parent = None
    if org["parent_id"]:
        parent = await db.fetchrow(
            "SELECT o.id, dn.display_name FROM organizations o"
            " LEFT JOIN v_org_display_names dn ON dn.organization_id=o.id"
            " WHERE o.id=$1",
            org["parent_id"],
        )
    return templates.TemplateResponse(
        request,
        "admin/orgs/partials/_parent_read.html",
        {"org": org, "parent": parent},
    )


@router.post("/{org_id}/inline/parent/")
async def org_inline_parent_post(
    org_id: str,
    request: Request,
    parent_id: str = Form(""),
    user: AdminUser | RedirectResponse = Depends(get_admin_user),
    db=Depends(get_db),
):
    """Save parent org inline; return updated read partial."""
    redirect, user = check_auth(user)
    if redirect:
        return redirect
    if parent_id and parent_id == org_id:
        raise HTTPException(status_code=422, detail="An organization cannot be its own parent")
    org = await db.fetchrow("SELECT * FROM organizations WHERE id=$1", org_id)
    if not org:
        raise HTTPException(status_code=404)
    resolved = parent_id.strip() or None
    if resolved:
        exists = await db.fetchval("SELECT id FROM organizations WHERE id=$1", resolved)
        if not exists:
            raise HTTPException(status_code=422, detail="Parent organization not found")
    await db.execute(
        "UPDATE organizations SET parent_id=$1 WHERE id=$2", resolved, org_id
    )
    org = await db.fetchrow("SELECT * FROM organizations WHERE id=$1", org_id)
    parent = None
    if org["parent_id"]:
        parent = await db.fetchrow(
            "SELECT o.id, dn.display_name FROM organizations o"
            " LEFT JOIN v_org_display_names dn ON dn.organization_id=o.id"
            " WHERE o.id=$1",
            org["parent_id"],
        )
    if not is_htmx(request):
        return RedirectResponse(f"/admin/orgs/{org_id}/", status_code=303)
    if parent:
        flash_body = f"Parent set to <strong>{escape(parent['display_name'])}</strong>."
        flash_level = "success"
    else:
        flash_body = "Parent organization cleared."
        flash_level = "info"
    return templates.TemplateResponse(
        request,
        "admin/orgs/partials/_parent_read.html",
        {"org": org, "parent": parent},
        headers=flash_trigger(flash_level, flash_body),
    )


@router.get("/{org_id}/inline/parent/edit/")
async def org_inline_parent_edit_get(
    org_id: str,
    request: Request,
    user: AdminUser | RedirectResponse = Depends(get_admin_user),
    db=Depends(get_db),
):
    """Return the edit form partial for parent org field."""
    redirect, user = check_auth(user)
    if redirect:
        return redirect
    org = await db.fetchrow("SELECT * FROM organizations WHERE id=$1", org_id)
    if not org:
        raise HTTPException(status_code=404)
    parent = None
    if org["parent_id"]:
        parent = await db.fetchrow(
            "SELECT o.id, dn.display_name FROM organizations o"
            " LEFT JOIN v_org_display_names dn ON dn.organization_id=o.id"
            " WHERE o.id=$1",
            org["parent_id"],
        )
    return templates.TemplateResponse(
        request,
        "admin/orgs/partials/_parent_form.html",
        {"org": org, "parent": parent},
    )


@router.get("/{org_id}/")
async def org_detail(
    org_id: str,
    request: Request,
    user: AdminUser | RedirectResponse = Depends(get_admin_user),
    db=Depends(get_db),
    org_dup_count: int = Depends(get_org_dup_count),
    person_dup_count: int = Depends(get_person_dup_count),
):
    """Organization detail view."""
    redirect, user = check_auth(user)
    if redirect:
        return redirect

    org = await db.fetchrow("SELECT * FROM organizations WHERE id = $1", org_id)
    if not org:
        raise HTTPException(status_code=404, detail="Organization not found")

    names = await db.fetch(
        "SELECT * FROM organization_names WHERE organization_id = $1"
        " ORDER BY is_canonical DESC, name_type, name",
        org_id,
    )
    acronyms = await db.fetch(
        "SELECT * FROM organization_acronyms WHERE organization_id = $1"
        " ORDER BY is_canonical DESC, acronym",
        org_id,
    )
    addresses = await db.fetch(
        """SELECT ea.*, a.standardized, a.address_line_1, a.city, a.region, a.postal_code
           FROM entity_addresses ea JOIN addresses a ON a.id = ea.address_id
           WHERE ea.entity_type = 'organization' AND ea.entity_id = $1""",
        org_id,
    )
    email_contacts = await db.fetch(
        "SELECT * FROM contact_methods"
        " WHERE entity_type = 'organization' AND entity_id = $1 AND contact_type = 'email'",
        org_id,
    )
    phone_contacts = await db.fetch(
        "SELECT * FROM contact_methods"
        " WHERE entity_type = 'organization' AND entity_id = $1 AND contact_type = 'phone'",
        org_id,
    )
    links = await db.fetch(
        """SELECT l.*, lt.display_name AS link_type_name, lt.is_social
           FROM links l JOIN link_types lt ON lt.id = l.link_type_id
           WHERE l.entity_type = 'organization' AND l.entity_id = $1""",
        org_id,
    )
    identifiers = await db.fetch(
        """SELECT i.*, eit.display_name AS type_name, eit.full_name AS type_full_name
           FROM identifiers i
           JOIN entity_identifier_types eit ON eit.id = i.entity_identifier_type_id
           WHERE i.entity_id = $1 AND eit.entity_type = 'organization'""",
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
    parent = None
    if org["parent_id"]:
        parent = await db.fetchrow(
            """SELECT o.id, dn.display_name
               FROM organizations o
               LEFT JOIN v_org_display_names dn ON dn.organization_id = o.id
               WHERE o.id = $1""",
            org["parent_id"],
        )

    canonical_name = next((n["name"] for n in names if n["is_canonical"]), "")
    canonical_acronym = next((a["acronym"] for a in acronyms if a["is_canonical"]), "")

    display_name_row = await db.fetchrow(
        "SELECT display_name FROM v_org_display_names WHERE organization_id = $1", org_id
    )
    display_name = display_name_row["display_name"] if display_name_row else None

    return templates.TemplateResponse(
        request,
        "admin/orgs/detail.html",
        {
            "user": user,
            "active_section": "orgs",
            "org": org,
            "org_id": org_id,
            "canonical_name": canonical_name,
            "canonical_acronym": canonical_acronym,
            "display_name": display_name,
            "names": names,
            "acronyms": acronyms,
            "addresses": addresses,
            "email_contacts": email_contacts,
            "phone_contacts": phone_contacts,
            "links": links,
            "identifiers": identifiers,
            "children": children,
            "roles": roles,
            "parent": parent,
            "org_dup_count": org_dup_count,
            "person_dup_count": person_dup_count,
        },
    )


@router.get("/{org_id}/children/search/")
async def children_search(
    org_id: str,
    request: Request,
    q: str = "",
    user: AdminUser | RedirectResponse = Depends(get_admin_user),
    db=Depends(get_db),
):
    """Typeahead search for adding a child org — excludes self and existing children."""
    redirect, user = check_auth(user)
    if redirect:
        return redirect
    results = []
    if q.strip():
        escaped = q.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        results = await db.fetch(
            """SELECT o.id, dn.display_name
               FROM organizations o
               LEFT JOIN v_org_display_names dn ON dn.organization_id = o.id
               WHERE o.archived_at IS NULL
                 AND o.id != $2
                 AND (o.parent_id IS NULL OR o.parent_id != $2)
                 AND dn.display_name ILIKE $1 ESCAPE '\\'
               ORDER BY dn.display_name NULLS LAST
               LIMIT 20""",
            f"%{escaped}%",
            org_id,
        )
    return templates.TemplateResponse(
        request,
        "admin/orgs/partials/_search_results.html",
        {"results": results},
    )


@router.get("/{org_id}/children/new-row/")
async def children_new_row(
    org_id: str,
    request: Request,
    user: AdminUser | RedirectResponse = Depends(get_admin_user),
    db=Depends(get_db),
):
    """Return empty child search form row."""
    redirect, user = check_auth(user)
    if redirect:
        return redirect
    return templates.TemplateResponse(
        request, "admin/orgs/partials/_child_form_row.html", {"org_id": org_id}
    )


@router.post("/{org_id}/children/")
async def children_add(
    org_id: str,
    request: Request,
    child_id: str = Form(...),
    user: AdminUser | RedirectResponse = Depends(get_admin_user),
    db=Depends(get_db),
):
    """Link an existing org as a child of this org."""
    redirect, user = check_auth(user)
    if redirect:
        return redirect
    if child_id == org_id:
        raise HTTPException(status_code=422, detail="An organization cannot be its own child")
    child = await db.fetchrow("SELECT id FROM organizations WHERE id=$1", child_id)
    if not child:
        raise HTTPException(status_code=422, detail="Child organization not found")
    await db.execute("UPDATE organizations SET parent_id=$1 WHERE id=$2", org_id, child_id)
    row = await db.fetchrow(
        """SELECT o.id, o.active, o.archived_at, dn.display_name AS canonical_name
           FROM organizations o
           LEFT JOIN v_org_display_names dn ON dn.organization_id=o.id
           WHERE o.id=$1""",
        child_id,
    )
    if not is_htmx(request):
        return RedirectResponse(f"/admin/orgs/{org_id}/", status_code=303)
    return templates.TemplateResponse(
        request,
        "admin/orgs/partials/_child_row.html",
        {"org_id": org_id, "child": row},
        headers=flash_trigger(
            "success",
            f"<strong>{escape(row['canonical_name'])}</strong> linked as child.",
        ),
    )


@router.delete("/{org_id}/children/{child_id}/")
async def children_remove(
    org_id: str,
    child_id: str,
    request: Request,
    user: AdminUser | RedirectResponse = Depends(get_admin_user),
    db=Depends(get_db),
):
    """Unlink a child org (clears its parent_id)."""
    redirect, user = check_auth(user)
    if redirect:
        return redirect
    child = await db.fetchrow(
        "SELECT id FROM organizations WHERE id=$1 AND parent_id=$2", child_id, org_id
    )
    if not child:
        raise HTTPException(status_code=404)
    await db.execute("UPDATE organizations SET parent_id=NULL WHERE id=$1", child_id)
    return HTMLResponse(
        content="",
        status_code=200,
        headers=flash_trigger("info", "Child organization unlinked."),
    )


@router.post("/{org_id}/archive/")
async def org_archive(
    org_id: str,
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


@router.post("/{org_id}/unarchive/")
async def org_unarchive(
    org_id: str,
    user: AdminUser | RedirectResponse = Depends(get_admin_user),
    db=Depends(get_db),
):
    """Restore an archived organization."""
    redirect, user = check_auth(user)
    if redirect:
        return redirect
    org = await db.fetchrow("SELECT id, archived_at FROM organizations WHERE id = $1", org_id)
    if not org:
        raise HTTPException(status_code=404, detail="Organization not found")
    if not org["archived_at"]:
        raise HTTPException(status_code=409, detail="Organization is not archived")
    await db.execute("UPDATE organizations SET archived_at = NULL WHERE id = $1", org_id)
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
    if is_htmx(request):
        return Response(
            status_code=204,
            headers={"HX-Location": "/admin/orgs/?flash=deleted"},
        )
    return RedirectResponse("/admin/orgs/?flash=deleted", status_code=303)
