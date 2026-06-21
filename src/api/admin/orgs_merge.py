"""Admin views for org merge and duplicate review."""

import asyncpg
from fastapi import APIRouter, Depends, Form, HTTPException, Request
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
    invalidate_dup_count_cache,
)
from src.core.db import generate_id

templates = Jinja2Templates(directory="src/templates")
router = APIRouter(prefix="/orgs", tags=["admin-orgs-merge"])


async def _fetch_duplicate_pairs(db) -> list:
    """Return near-duplicate org pairs; empty list if pg_trgm not installed."""
    try:
        return await db.fetch(
            f"""WITH cands AS (
                SELECT DISTINCT ON (a.id, b.id)
                    a.id AS a_id, dn_a.name AS a_name_raw, a.created_at AS a_created,
                    b.id AS b_id, dn_b.name AS b_name_raw, b.created_at AS b_created,
                    similarity(dn_a.name, dn_b.name) AS score,
                    (SELECT count(*) FROM roles
                     WHERE organization_id = a.id AND archived_at IS NULL) AS a_roles,
                    (SELECT count(*) FROM roles
                     WHERE organization_id = b.id AND archived_at IS NULL) AS b_roles
                {CANDIDATE_WHERE}
                ORDER BY a.id, b.id, similarity(dn_a.name, dn_b.name) DESC
            )
            SELECT
                cands.a_id,
                COALESCE(
                    cands.a_name_raw || ' (' || acr_a.acronym || ')',
                    cands.a_name_raw
                ) AS a_name,
                cands.a_created,
                cands.b_id,
                COALESCE(
                    cands.b_name_raw || ' (' || acr_b.acronym || ')',
                    cands.b_name_raw
                ) AS b_name,
                cands.b_created,
                cands.score,
                cands.a_roles,
                cands.b_roles
            FROM cands
            LEFT JOIN organization_acronyms acr_a
                ON acr_a.organization_id = cands.a_id AND acr_a.is_canonical = TRUE
            LEFT JOIN organization_acronyms acr_b
                ON acr_b.organization_id = cands.b_id AND acr_b.is_canonical = TRUE
            ORDER BY cands.score DESC"""
        )
    except asyncpg.exceptions.UndefinedFunctionError:
        return []


async def _execute_merge(
    db,
    winner_id: str,
    loser_id: str,
    keep_name_ids: list[str] | None = None,
    keep_acronym_ids: list[str] | None = None,
    role_pairs_to_merge: list[tuple[str, str]] | None = None,
) -> int:
    """Merge loser into winner inside a transaction. Invalidates dup count cache.

    Returns count of duplicate role assignments that were dropped during role merging.
    keep_name_ids / keep_acronym_ids: when provided (even empty), only those IDs are
      transferred; others are deleted. When None, original bulk-transfer behavior applies.
    role_pairs_to_merge: list of (winner_role_id, loser_role_id) pairs to merge before
      the bulk role UPDATE, avoiding unique-constraint violations on (org_id, lower(title)).
    """
    dropped_assignments = 0
    async with db.transaction():
        winner = await db.fetchrow("SELECT id FROM organizations WHERE id=$1 FOR UPDATE", winner_id)
        loser = await db.fetchrow("SELECT id FROM organizations WHERE id=$1 FOR UPDATE", loser_id)
        if not winner or not loser:
            raise HTTPException(status_code=404, detail="Organization not found")

        # Merge conflicting role pairs BEFORE the bulk role UPDATE to avoid violating
        # uq_role_org_title (organization_id, lower(title)) WHERE archived_at IS NULL.
        if role_pairs_to_merge:
            for winner_role_id, loser_role_id in role_pairs_to_merge:
                active = await db.fetch(
                    "SELECT person_id, start_date, end_date, is_current, notes"
                    " FROM role_assignments WHERE role_id=$1 AND archived_at IS NULL",
                    loser_role_id,
                )
                for a in active:
                    exists = await db.fetchval(
                        "SELECT 1 FROM role_assignments"
                        " WHERE person_id=$1 AND role_id=$2 AND archived_at IS NULL"
                        " AND start_date IS NOT DISTINCT FROM $3",
                        a["person_id"],
                        winner_role_id,
                        a["start_date"],
                    )
                    if not exists:
                        await db.execute(
                            "INSERT INTO role_assignments"
                            " (id, person_id, role_id, start_date, end_date, is_current, notes)"
                            " VALUES ($1,$2,$3,$4,$5,$6,$7)",
                            generate_id(),
                            a["person_id"],
                            winner_role_id,
                            a["start_date"],
                            a["end_date"],
                            a["is_current"],
                            a["notes"],
                        )
                    else:
                        dropped_assignments += 1
                # Transfer archived assignments to winner role (no dedup needed)
                await db.execute(
                    "UPDATE role_assignments SET role_id=$1"
                    " WHERE role_id=$2 AND archived_at IS NOT NULL",
                    winner_role_id,
                    loser_role_id,
                )
                # Remove remaining active assignments from loser role (the dropped dupes)
                await db.execute(
                    "DELETE FROM role_assignments WHERE role_id=$1 AND archived_at IS NULL",
                    loser_role_id,
                )
                await db.execute("DELETE FROM roles WHERE id=$1", loser_role_id)

        await db.execute(
            "UPDATE organizations SET parent_id=$1 WHERE parent_id=$2",
            winner_id,
            loser_id,
        )

        if keep_name_ids is not None:
            if keep_name_ids:
                placeholders = ", ".join(f"${i + 3}" for i in range(len(keep_name_ids)))
                await db.execute(
                    f"UPDATE organization_names SET organization_id=$1, is_canonical=FALSE"
                    f" WHERE organization_id=$2 AND id IN ({placeholders})",
                    winner_id,
                    loser_id,
                    *keep_name_ids,
                )
            await db.execute(
                "DELETE FROM organization_names WHERE organization_id=$1",
                loser_id,
            )
        else:
            await db.execute(
                "UPDATE organization_names SET organization_id=$1"
                " WHERE organization_id=$2 AND is_canonical=FALSE",
                winner_id,
                loser_id,
            )
            await db.execute(
                "DELETE FROM organization_names WHERE organization_id=$1 AND is_canonical=TRUE",
                loser_id,
            )

        if keep_acronym_ids is not None:
            if keep_acronym_ids:
                placeholders = ", ".join(f"${i + 3}" for i in range(len(keep_acronym_ids)))
                await db.execute(
                    f"UPDATE organization_acronyms SET organization_id=$1, is_canonical=FALSE"
                    f" WHERE organization_id=$2 AND id IN ({placeholders})",
                    winner_id,
                    loser_id,
                    *keep_acronym_ids,
                )
            await db.execute(
                "DELETE FROM organization_acronyms WHERE organization_id=$1",
                loser_id,
            )
        else:
            await db.execute(
                "UPDATE organization_acronyms SET organization_id=$1"
                " WHERE organization_id=$2 AND is_canonical=FALSE",
                winner_id,
                loser_id,
            )
            await db.execute(
                "DELETE FROM organization_acronyms WHERE organization_id=$1 AND is_canonical=TRUE",
                loser_id,
            )

        # Safeguard: auto-resolve any remaining title conflicts not covered by
        # role_pairs_to_merge (defense-in-depth against form manipulation or
        # race conditions between preview load and merge submit).
        remaining_conflicts = await db.fetch(
            """SELECT r_l.id AS loser_role_id, r_w.id AS winner_role_id
               FROM roles r_l
               JOIN roles r_w ON lower(r_w.title) = lower(r_l.title)
                              AND r_w.organization_id = $2
                              AND r_w.archived_at IS NULL
               WHERE r_l.organization_id = $1
                 AND r_l.archived_at IS NULL""",
            loser_id,
            winner_id,
        )
        for conflict in remaining_conflicts:
            w_role = conflict["winner_role_id"]
            l_role = conflict["loser_role_id"]
            active = await db.fetch(
                "SELECT person_id, start_date, end_date, is_current, notes"
                " FROM role_assignments WHERE role_id=$1 AND archived_at IS NULL",
                l_role,
            )
            for a in active:
                exists = await db.fetchval(
                    "SELECT 1 FROM role_assignments"
                    " WHERE person_id=$1 AND role_id=$2 AND archived_at IS NULL"
                    " AND start_date IS NOT DISTINCT FROM $3",
                    a["person_id"],
                    w_role,
                    a["start_date"],
                )
                if not exists:
                    await db.execute(
                        "INSERT INTO role_assignments"
                        " (id, person_id, role_id, start_date, end_date, is_current, notes)"
                        " VALUES ($1,$2,$3,$4,$5,$6,$7)",
                        generate_id(),
                        a["person_id"],
                        w_role,
                        a["start_date"],
                        a["end_date"],
                        a["is_current"],
                        a["notes"],
                    )
                else:
                    dropped_assignments += 1
            await db.execute(
                "UPDATE role_assignments SET role_id=$1"
                " WHERE role_id=$2 AND archived_at IS NOT NULL",
                w_role,
                l_role,
            )
            await db.execute(
                "DELETE FROM role_assignments WHERE role_id=$1 AND archived_at IS NULL",
                l_role,
            )
            await db.execute("DELETE FROM roles WHERE id=$1", l_role)

        await db.execute(
            "UPDATE roles SET organization_id=$1 WHERE organization_id=$2",
            winner_id,
            loser_id,
        )
        # jurisdiction affiliations: dedup then reassign to winner.
        await db.execute(
            """DELETE FROM organization_jurisdiction_affiliations
               WHERE organization_id=$1
                 AND (jurisdiction_id, affiliation_type_id) IN (
                     SELECT jurisdiction_id, affiliation_type_id
                     FROM organization_jurisdiction_affiliations
                     WHERE organization_id=$2
                 )""",
            loser_id,
            winner_id,
        )
        await db.execute(
            "UPDATE organization_jurisdiction_affiliations"
            " SET organization_id=$1 WHERE organization_id=$2",
            winner_id,
            loser_id,
        )
        # links: drop loser's duplicates (same url+link_type already on winner) before reassigning.
        await db.execute(
            """DELETE FROM links
               WHERE entity_type='organization' AND entity_id=$1
                 AND (url, link_type_id) IN (
                     SELECT url, link_type_id FROM links
                     WHERE entity_type='organization' AND entity_id=$2
                 )""",
            loser_id,
            winner_id,
        )

        for table in (
            "entity_addresses",
            "contact_methods",
            "links",
            "import_provenance",
            "field_confidence",
        ):
            await db.execute(
                f"UPDATE {table} SET entity_id=$1"
                f" WHERE entity_type='organization' AND entity_id=$2",
                winner_id,
                loser_id,
            )
        await db.execute(
            "UPDATE identifiers SET entity_id=$1 WHERE entity_id=$2",
            winner_id,
            loser_id,
        )
        await db.execute(
            "DELETE FROM duplicate_dismissals"
            " WHERE entity_type='organization'"
            "   AND ((entity_a_id=$1 AND entity_b_id=$2)"
            "    OR  (entity_a_id=$2 AND entity_b_id=$1))",
            winner_id,
            loser_id,
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
            loser_id,
            winner_id,
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
            loser_id,
            winner_id,
        )
        await db.execute(
            """UPDATE duplicate_dismissals
               SET entity_a_id = LEAST($1, entity_b_id),
                   entity_b_id = GREATEST($1, entity_b_id)
               WHERE entity_type='organization' AND entity_a_id=$2""",
            winner_id,
            loser_id,
        )
        await db.execute(
            """UPDATE duplicate_dismissals
               SET entity_a_id = LEAST(entity_a_id, $1),
                   entity_b_id = GREATEST(entity_a_id, $1)
               WHERE entity_type='organization' AND entity_b_id=$2""",
            winner_id,
            loser_id,
        )
        await db.execute("DELETE FROM organizations WHERE id=$1", loser_id)
        await db.execute(
            "INSERT INTO deleted_entities (entity_type, entity_id) VALUES ('organization', $1)"
            " ON CONFLICT DO NOTHING",
            loser_id,
        )
    await invalidate_dup_count_cache(db)
    return dropped_assignments


@router.get("/duplicates/")
async def orgs_duplicates(
    request: Request,
    user: AdminUser = Depends(get_admin_user),
    db=Depends(get_db),
):
    """List near-duplicate organization pairs for review."""
    pairs = await _fetch_duplicate_pairs(db)
    ctx = {
        "user": user,
        "active_section": "orgs_duplicates",
        "pairs": pairs,
    }
    template = (
        "admin/orgs/_duplicates_region.html" if is_htmx(request) else "admin/orgs/duplicates.html"
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
    if winner_id == loser_id:
        raise HTTPException(status_code=400, detail="Cannot merge an organization with itself")
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
            f"Merged <strong>{escape(loser_name)}</strong> into "
            f'<a href="/admin/orgs/{winner_id}/"><strong>{escape(winner_name)}</strong></a>. '
            f"Review URLs, roles, and contact info for duplicates."
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
            headers=flash_trigger("success", body, extra={"refreshDupBadge": True}),
        )
    return RedirectResponse("/admin/orgs/duplicates/", status_code=303)


@router.post("/{winner_id}/merge-with/{loser_id}/")
async def org_merge_with(
    winner_id: str,
    loser_id: str,
    request: Request,
    user: AdminUser = Depends(get_admin_user),
    db=Depends(get_db),
    keep_name_ids: list[str] = Form(default=[]),
    keep_acronym_ids: list[str] = Form(default=[]),
    merge_role_pairs: list[str] = Form(default=[]),
):
    """Merge loser into winner from detail page; redirect to winner detail."""
    if winner_id == loser_id:
        raise HTTPException(status_code=400, detail="Cannot merge an organization with itself")
    winner_name = await db.fetchval(
        "SELECT display_name FROM v_org_display_names WHERE organization_id=$1", winner_id
    )
    loser_name = await db.fetchval(
        "SELECT display_name FROM v_org_display_names WHERE organization_id=$1", loser_id
    )
    parsed_pairs = [(p.split(":", 1)[0], p.split(":", 1)[1]) for p in merge_role_pairs if ":" in p]
    dropped = await _execute_merge(
        db,
        winner_id,
        loser_id,
        keep_name_ids=keep_name_ids,
        keep_acronym_ids=keep_acronym_ids,
        role_pairs_to_merge=parsed_pairs if parsed_pairs else None,
    )
    body = (
        f"Merged <strong>{escape(loser_name)}</strong> into "
        f"<strong>{escape(winner_name)}</strong>. "
        f"Review names, roles, and contact info for duplicates."
    )
    if dropped:
        body += f" {dropped} duplicate role assignment{'s' if dropped != 1 else ''} dropped."
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
        generate_id(),
        a,
        b,
        user.email,
    )
    await invalidate_dup_count_cache(db)
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
            headers=flash_trigger(
                "info", "Pair marked as not a duplicate.", extra={"refreshDupBadge": True}
            ),
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
    if id_a == id_b:
        raise HTTPException(status_code=400, detail="Cannot merge an organization with itself")
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

    winner_names_lower = {
        r["name"].lower()
        for r in await db.fetch(
            "SELECT name FROM organization_names WHERE organization_id=$1", winner_id
        )
    }
    winner_acronyms_lower = {
        r["acronym"].lower()
        for r in await db.fetch(
            "SELECT acronym FROM organization_acronyms WHERE organization_id=$1", winner_id
        )
    }

    transferred_names = [
        r
        for r in await db.fetch(
            "SELECT id, name, name_type FROM organization_names"
            " WHERE organization_id=$1 AND is_canonical=FALSE",
            loser_id,
        )
        if r["name"].lower() not in winner_names_lower
    ]
    _dropped_name = await db.fetchrow(
        "SELECT id, name FROM organization_names WHERE organization_id=$1 AND is_canonical=TRUE",
        loser_id,
    )
    dropped_name = (
        _dropped_name
        if _dropped_name and _dropped_name["name"].lower() not in winner_names_lower
        else None
    )
    transferred_acronyms = [
        r
        for r in await db.fetch(
            "SELECT id, acronym FROM organization_acronyms"
            " WHERE organization_id=$1 AND is_canonical=FALSE",
            loser_id,
        )
        if r["acronym"].lower() not in winner_acronyms_lower
    ]
    _dropped_acronym = await db.fetchrow(
        "SELECT id, acronym FROM organization_acronyms"
        " WHERE organization_id=$1 AND is_canonical=TRUE",
        loser_id,
    )
    dropped_acronym = (
        _dropped_acronym
        if _dropped_acronym and _dropped_acronym["acronym"].lower() not in winner_acronyms_lower
        else None
    )

    roles_count = await db.fetchval(
        "SELECT count(*) FROM roles WHERE organization_id=$1 AND archived_at IS NULL",
        loser_id,
    )
    contacts_count = await db.fetchval(
        "SELECT count(*) FROM contact_methods WHERE entity_type='organization' AND entity_id=$1",
        loser_id,
    )
    links_count = await db.fetchval(
        "SELECT count(*) FROM links WHERE entity_type='organization' AND entity_id=$1",
        loser_id,
    )
    addresses_count = await db.fetchval(
        "SELECT count(*) FROM entity_addresses WHERE entity_type='organization' AND entity_id=$1",
        loser_id,
    )
    identifiers_count = await db.fetchval(
        """SELECT count(*) FROM identifiers i
           JOIN entity_identifier_types eit ON eit.id = i.entity_identifier_type_id
           WHERE i.entity_id=$1 AND eit.entity_type='organization'""",
        loser_id,
    )

    conflicting_roles = await db.fetch(
        """SELECT r_l.id AS loser_role_id, r_w.id AS winner_role_id, r_l.title
           FROM roles r_l
           JOIN roles r_w ON lower(r_w.title) = lower(r_l.title)
                          AND r_w.organization_id = $2
                          AND r_w.archived_at IS NULL
           WHERE r_l.organization_id = $1
             AND r_l.archived_at IS NULL""",
        loser_id,
        winner_id,
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
