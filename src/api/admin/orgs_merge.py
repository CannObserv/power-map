"""Admin views for org merge and duplicate review."""

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from markupsafe import escape

from src.api.admin.deps import (
    AdminUser,
    escape_like,
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
from src.api.admin.people_dups import get_person_dup_count
from src.core.db import generate_id

templates = Jinja2Templates(directory="src/templates")
router = APIRouter(prefix="/orgs", tags=["admin-orgs-merge"])


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


async def _execute_merge(db, winner_id: str, loser_id: str) -> None:
    """Merge loser into winner inside a transaction. Invalidates dup count cache."""
    async with db.transaction():
        winner = await db.fetchrow(
            "SELECT id FROM organizations WHERE id=$1 FOR UPDATE", winner_id
        )
        loser = await db.fetchrow(
            "SELECT id FROM organizations WHERE id=$1 FOR UPDATE", loser_id
        )
        if not winner or not loser:
            raise HTTPException(status_code=404, detail="Organization not found")
        await db.execute(
            "UPDATE organizations SET parent_id=$1 WHERE parent_id=$2",
            winner_id, loser_id,
        )
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
        await db.execute(
            "UPDATE roles SET organization_id=$1 WHERE organization_id=$2",
            winner_id, loser_id,
        )
        for table in ("entity_addresses", "contact_methods", "links",
                      "import_provenance", "field_confidence"):
            await db.execute(
                f"UPDATE {table} SET entity_id=$1"
                f" WHERE entity_type='organization' AND entity_id=$2",
                winner_id, loser_id,
            )
        await db.execute(
            "UPDATE identifiers SET entity_id=$1 WHERE entity_id=$2",
            winner_id, loser_id,
        )
        await db.execute(
            "DELETE FROM duplicate_dismissals"
            " WHERE entity_type='organization'"
            "   AND ((entity_a_id=$1 AND entity_b_id=$2)"
            "    OR  (entity_a_id=$2 AND entity_b_id=$1))",
            winner_id, loser_id,
        )
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


@router.get("/duplicates/")
async def orgs_duplicates(
    request: Request,
    user: AdminUser = Depends(get_admin_user),
    db=Depends(get_db),
    org_dup_count: int = Depends(get_org_dup_count),
    person_dup_count: int = Depends(get_person_dup_count),
):
    """List near-duplicate organization pairs for review."""
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
    user: AdminUser = Depends(get_admin_user),
    db=Depends(get_db),
):
    """Merge loser into winner: reassign all references, hard-delete loser."""
    winner_name = await db.fetchval(
        "SELECT display_name FROM v_org_display_names WHERE organization_id=$1", winner_id
    )
    loser_name = await db.fetchval(
        "SELECT display_name FROM v_org_display_names WHERE organization_id=$1", loser_id
    )
    await _execute_merge(db, winner_id, loser_id)
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


@router.post("/{winner_id}/merge-with/{loser_id}/")
async def org_merge_with(
    winner_id: str,
    loser_id: str,
    request: Request,
    user: AdminUser = Depends(get_admin_user),
    db=Depends(get_db),
):
    """Merge loser into winner from detail page; redirect to winner detail."""
    winner_name = await db.fetchval(
        "SELECT display_name FROM v_org_display_names WHERE organization_id=$1", winner_id
    )
    loser_name = await db.fetchval(
        "SELECT display_name FROM v_org_display_names WHERE organization_id=$1", loser_id
    )
    await _execute_merge(db, winner_id, loser_id)
    body = (
        f'Merged <strong>{escape(loser_name)}</strong> into '
        f'<strong>{escape(winner_name)}</strong>. '
        f'Review names, roles, and contact info for duplicates.'
    )
    redirect_url = f"/admin/orgs/{winner_id}/"
    if is_htmx(request):
        return HTMLResponse(
            "",
            headers={**flash_trigger("success", body), "HX-Redirect": redirect_url},
        )
    return RedirectResponse(redirect_url, status_code=303)


@router.post("/{id_a}/dismiss-duplicate/{id_b}/")
async def org_dismiss_duplicate(
    id_a: str,
    id_b: str,
    request: Request,
    user: AdminUser = Depends(get_admin_user),
    db=Depends(get_db),
):
    """Record that this pair is not a duplicate (suppress from future results)."""
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


@router.get("/{org_id}/merge-target-search/")
async def org_merge_target_search(
    org_id: str,
    request: Request,
    q: str = "",
    user: AdminUser = Depends(get_admin_user),
    db=Depends(get_db),
):
    """Typeahead search for merge target — excludes current org and archived orgs."""
    results = []
    if q.strip():
        results = await db.fetch(
            """SELECT o.id, dn.display_name
               FROM organizations o
               LEFT JOIN v_org_display_names dn ON dn.organization_id = o.id
               WHERE o.archived_at IS NULL
                 AND o.id != $1
                 AND dn.display_name ILIKE $2 ESCAPE '\\'
               ORDER BY dn.display_name NULLS LAST
               LIMIT 20""",
            org_id,
            f"%{escape_like(q.strip())}%",
        )
    return templates.TemplateResponse(
        request,
        "admin/orgs/partials/_search_results.html",
        {"results": results},
    )


@router.get("/{org_id}/merge-search/")
async def org_merge_search_modal(
    org_id: str,
    request: Request,
    user: AdminUser = Depends(get_admin_user),
    db=Depends(get_db),
):
    """Return modal fragment for step 1 of manual merge: typeahead search."""
    org = await db.fetchrow("SELECT id FROM organizations WHERE id=$1", org_id)
    if not org:
        raise HTTPException(status_code=404)
    org_name = await db.fetchval(
        "SELECT display_name FROM v_org_display_names WHERE organization_id=$1", org_id
    )
    return templates.TemplateResponse(
        request,
        "admin/orgs/_merge_search_modal.html",
        {"org_id": org_id, "org_name": org_name},
    )


@router.get("/{id_a}/merge-preview/{id_b}/")
async def org_merge_preview(
    id_a: str,
    id_b: str,
    request: Request,
    winner: str | None = None,
    user: AdminUser = Depends(get_admin_user),
    db=Depends(get_db),
):
    """Return preview modal: impact of merging id_b into id_a (or flipped via ?winner=)."""
    winner_id = winner if winner in (id_a, id_b) else id_a
    loser_id = id_b if winner_id == id_a else id_a

    org_a = await db.fetchrow(
        "SELECT id FROM organizations WHERE id=$1 AND archived_at IS NULL", id_a
    )
    org_b = await db.fetchrow(
        "SELECT id FROM organizations WHERE id=$1 AND archived_at IS NULL", id_b
    )
    if not org_a or not org_b:
        raise HTTPException(status_code=404)

    winner_name = await db.fetchval(
        "SELECT display_name FROM v_org_display_names WHERE organization_id=$1", winner_id
    )
    loser_name = await db.fetchval(
        "SELECT display_name FROM v_org_display_names WHERE organization_id=$1", loser_id
    )

    transferred_names = await db.fetch(
        "SELECT name, name_type FROM organization_names"
        " WHERE organization_id=$1 AND is_canonical=FALSE",
        loser_id,
    )
    dropped_name = await db.fetchrow(
        "SELECT name FROM organization_names WHERE organization_id=$1 AND is_canonical=TRUE",
        loser_id,
    )
    transferred_acronyms = await db.fetch(
        "SELECT acronym FROM organization_acronyms"
        " WHERE organization_id=$1 AND is_canonical=FALSE",
        loser_id,
    )
    dropped_acronym = await db.fetchrow(
        "SELECT acronym FROM organization_acronyms WHERE organization_id=$1 AND is_canonical=TRUE",
        loser_id,
    )

    roles_count = await db.fetchval(
        "SELECT count(*) FROM roles WHERE organization_id=$1 AND archived_at IS NULL",
        loser_id,
    )
    contacts_count = await db.fetchval(
        "SELECT count(*) FROM contact_methods"
        " WHERE entity_type='organization' AND entity_id=$1",
        loser_id,
    )
    links_count = await db.fetchval(
        "SELECT count(*) FROM links WHERE entity_type='organization' AND entity_id=$1",
        loser_id,
    )
    addresses_count = await db.fetchval(
        "SELECT count(*) FROM entity_addresses"
        " WHERE entity_type='organization' AND entity_id=$1",
        loser_id,
    )
    identifiers_count = await db.fetchval(
        """SELECT count(*) FROM identifiers i
           JOIN entity_identifier_types eit ON eit.id = i.entity_identifier_type_id
           WHERE i.entity_id=$1 AND eit.entity_type='organization'""",
        loser_id,
    )

    conflicting_roles = await db.fetch(
        """SELECT r_l.title
           FROM roles r_l
           JOIN roles r_w ON lower(r_w.title) = lower(r_l.title)
                          AND r_w.organization_id = $2
                          AND r_w.archived_at IS NULL
           WHERE r_l.organization_id = $1
             AND r_l.archived_at IS NULL""",
        loser_id, winner_id,
    )

    return templates.TemplateResponse(
        request,
        "admin/orgs/_merge_preview_modal.html",
        {
            "org_a_id": id_a,
            "org_b_id": id_b,
            "winner_id": winner_id,
            "loser_id": loser_id,
            "winner_name": winner_name,
            "loser_name": loser_name,
            "transferred_names": transferred_names,
            "dropped_name": dropped_name,
            "transferred_acronyms": transferred_acronyms,
            "dropped_acronym": dropped_acronym,
            "roles_count": roles_count,
            "contacts_count": contacts_count,
            "links_count": links_count,
            "addresses_count": addresses_count,
            "identifiers_count": identifiers_count,
            "conflicting_roles": conflicting_roles,
        },
    )
