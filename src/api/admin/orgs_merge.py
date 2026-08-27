"""Admin views for org merge and duplicate review."""

import asyncpg
from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request
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
    resolve_query_flash,
    with_flash,
)
from src.api.admin.list_filters import parse_list_filters
from src.api.admin.org_dups import (
    CANDIDATE_WHERE,
    invalidate_dup_count_cache,
)
from src.api.admin.orgs_queries import VALID_STATUSES, query_orgs_rows
from src.core.ancillary_migrate import (
    delete_event_citations_for_owner,
    rehome_assignment_relationships,
    rehome_citations,
    rehome_conflicting_assignment_ancillary,
    rehome_role_ancillary,
)
from src.core.db import generate_id
from src.core.logging import get_logger
from src.core.merge_signals import mirror_subscriptions, record_merge_tombstones
from src.core.org_lifecycle import count_open_assignments, get_org_ended_on

logger = get_logger(__name__)

_LIST_TARGET = "orgs-list-region"


def _parse_list_filters_from_hx_current_url(request: Request) -> dict:
    """Parse the orgs list filters from HX-Current-URL (see `parse_list_filters`).

    Thin wrapper binding the org-specific status set (four-valued with ``all``,
    #306 — `inactive` is org-only), imported from `orgs_queries` so the two
    can't drift; the parsing logic (and the default page size) is shared with
    People via `src.api.admin.list_filters`.
    """
    return parse_list_filters(request, valid_statuses=VALID_STATUSES)


def _dropped_assignments_note(dropped: int) -> str:
    """Flash suffix reporting duplicate role assignments dropped during a merge.

    Empty string when none were dropped. Shared by the list/duplicates flow
    (`org_merge`) and the detail flow (`org_merge_with`) so the wording can't
    drift between them.
    """
    if not dropped:
        return ""
    return f" {dropped} duplicate role assignment{'s' if dropped != 1 else ''} dropped."


async def _winner_lifespan_note(db, winner_id: str) -> str:
    """Flash suffix warning when the surviving org is past its lifespan (#307).

    A merge re-points the loser's assignments onto the winner; if the winner is
    archived/inactive/ended, that silently re-creates the "live members on a
    defunct org" state the lifespan invariant exists to prevent. Shared by both
    merge flows so the wording can't drift.
    """
    row = await db.fetchrow("SELECT active, archived_at FROM organizations WHERE id=$1", winner_id)
    if row is None:
        return ""
    ended_on = await get_org_ended_on(db, winner_id)
    if row["active"] and row["archived_at"] is None and ended_on is None:
        return ""
    open_count = await count_open_assignments(db, winner_id)
    if not open_count:
        return ""
    noun = "assignment remains" if open_count == 1 else "assignments remain"
    return f" Warning: {open_count} open {noun} on the merged organization — close or re-home them."


templates = Jinja2Templates(directory="src/templates")
router = APIRouter(prefix="/orgs", tags=["admin-orgs-merge"])


async def _render_orgs_list_region(request: Request, db, user: AdminUser, flash_body: str):
    """Re-render the orgs list region (rows + caption total + sticky pagination).

    Shared by the list-flow merge (`org_merge`) and the list-context detail-modal
    merge (`org_merge_with` with `return_to=list`, #255) so the post-merge swap
    stays identical. Filter state is read from HX-Current-URL.
    """
    filters = _parse_list_filters_from_hx_current_url(request)
    rows, count, pctx, hidden_matches = await query_orgs_rows(db, **filters)
    ctx = {
        "user": user,
        "active_section": "orgs",
        "orgs": rows,
        "total": count,
        "q": filters["q"],
        "status": filters["status"],
        "page_size": filters["page_size"],
        "hidden_matches": hidden_matches,
        **pctx,
    }
    return templates.TemplateResponse(
        request,
        "admin/orgs/_region.html",
        ctx,
        headers=flash_trigger("success", flash_body, extra={"refreshDupBadge": True}),
    )


async def _fetch_duplicate_pairs(db) -> list:
    """Return near-duplicate org pairs; empty list if pg_trgm not installed."""
    try:
        return await db.fetch(
            f"""WITH cands AS (
                SELECT DISTINCT ON (a.id, b.id)
                    a.id AS a_id,
                    dn_a.name AS a_match_name,
                    dn_a.is_canonical AS a_match_is_canonical,
                    a.created_at AS a_created,
                    b.id AS b_id,
                    dn_b.name AS b_match_name,
                    dn_b.is_canonical AS b_match_is_canonical,
                    b.created_at AS b_created,
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
                COALESCE(vdn_a.display_name, cands.a_match_name) AS a_name,
                cands.a_match_name,
                cands.a_match_is_canonical,
                cands.a_created,
                cands.b_id,
                COALESCE(vdn_b.display_name, cands.b_match_name) AS b_name,
                cands.b_match_name,
                cands.b_match_is_canonical,
                cands.b_created,
                cands.score,
                cands.a_roles,
                cands.b_roles
            FROM cands
            LEFT JOIN v_org_display_names vdn_a ON vdn_a.organization_id = cands.a_id
            LEFT JOIN v_org_display_names vdn_b ON vdn_b.organization_id = cands.b_id
            ORDER BY cands.score DESC"""
        )
    except asyncpg.exceptions.UndefinedFunctionError:
        return []


async def _absorb_role(db, winner_role_id: str, loser_role_id: str) -> int:
    """Fold the loser role's assignments into the winner role, then drop the emptied role.

    Returns the number of assignments dropped as true duplicates.

    **Identity-preserving (#467).** Every assignment with no winner-side twin is
    *re-pointed* — `UPDATE ... SET role_id` — so it keeps its ULID, its
    `source_key_id`, and its ancillary. This function replaces an
    INSERT-a-copy-then-DELETE-the-original pass that reminted every id and
    silently broke every `pm_assignment_id` anchor a producer held.

    Only a genuine `(person_id, role_id, start_date)` collision is resolved by
    deleting the loser row — that tuple is exactly what
    `uq_role_assignment_person_role_start` refuses, so it is the one case
    re-pointing cannot serve. Each such delete is announced (#467) with
    `merged_into` naming the survivor, so a subscriber can rebind rather than
    merely notice an id stopped resolving.

    Both of `_execute_merge`'s conflict passes route through here: the explicit
    `merge_role_pairs` one and the safeguard for pairs the form did not submit.
    They were near-duplicate blocks, which is how the safeguard copy came to drop
    `source_key_id` while the other preserved it (#324 CR3).
    """
    conflicts = await db.fetch(
        """SELECT l.id AS loser_ra, w.id AS winner_ra
           FROM role_assignments l
           JOIN role_assignments w
             ON w.role_id = $2 AND w.archived_at IS NULL
            AND w.person_id = l.person_id
            AND w.start_date IS NOT DISTINCT FROM l.start_date
           WHERE l.role_id = $1 AND l.archived_at IS NULL""",
        loser_role_id,
        winner_role_id,
    )
    pairs = [(r["loser_ra"], r["winner_ra"]) for r in conflicts]
    # The colliding loser rows are about to be hard-deleted: their polymorphic
    # ancillary (#324 / #319 citations) and their active relationship edges (#301,
    # FK ON DELETE CASCADE) must reach the survivor first, and anyone watching them
    # must also start watching the survivor (#467).
    await rehome_conflicting_assignment_ancillary(db, pairs)
    await rehome_assignment_relationships(db, pairs)
    await mirror_subscriptions(db, pairs)
    await db.execute(
        "DELETE FROM role_assignments WHERE id = ANY($1::text[])",
        [loser_ra for loser_ra, _ in pairs],
    )
    await record_merge_tombstones(db, "role_assignment", pairs)
    # Everything left keeps its id. Archived rows re-point too: the unique index is
    # partial on active rows, so a retracted tenure can never be the collision.
    await db.execute(
        "UPDATE role_assignments SET role_id=$1 WHERE role_id=$2",
        winner_role_id,
        loser_role_id,
    )
    # #326: the loser role's own contacts/links have no FK — re-home before the delete.
    await rehome_role_ancillary(db, loser_role_id, winner_role_id)
    await mirror_subscriptions(db, [(loser_role_id, winner_role_id)])
    await db.execute("DELETE FROM roles WHERE id=$1", loser_role_id)
    await record_merge_tombstones(db, "role", [(loser_role_id, winner_role_id)])
    return len(pairs)


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
        #
        # The pairs are unvalidated form input and `_absorb_role` hard-deletes the
        # loser role it is handed *and* publishes a `role/deleted merged_into=…` row
        # for it, so an unfiltered list would let a manipulated submit destroy an
        # unrelated role and inject a false rebind into subscribers' feeds. Accept a
        # pair only if it is one the preview could have offered: both roles **active**
        # (`conflicting_roles` filters `archived_at IS NULL`) and each in the org it
        # must belong to. Archiving matters as much as the org does — the bulk
        # `UPDATE roles SET organization_id` below re-parents archived roles onto the
        # winner, so an archived role is data this merge preserves. A dropped pair
        # that is a genuine conflict is still caught by the safeguard pass below.
        if role_pairs_to_merge:
            org_of_role = {
                r["id"]: r["organization_id"]
                for r in await db.fetch(
                    "SELECT id, organization_id FROM roles"
                    " WHERE id = ANY($1::text[]) AND archived_at IS NULL",
                    [rid for pair in role_pairs_to_merge for rid in pair],
                )
            }
            for winner_role_id, loser_role_id in role_pairs_to_merge:
                if (
                    org_of_role.get(winner_role_id) != winner_id
                    or org_of_role.get(loser_role_id) != loser_id
                ):
                    logger.warning(
                        "merge_role_pair_out_of_scope",
                        extra={
                            "winner_org_id": winner_id,
                            "loser_org_id": loser_id,
                            "winner_role_id": winner_role_id,
                            "loser_role_id": loser_role_id,
                        },
                    )
                    continue
                dropped_assignments += await _absorb_role(db, winner_role_id, loser_role_id)

        await db.execute(
            "UPDATE organizations SET parent_id=$1 WHERE parent_id=$2",
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
            # Non-lossy demote+transfer (#255): preserve the loser's canonical name
            # as a non-canonical alias on the winner instead of deleting it. Dedup by
            # lower(name) against names the winner already holds (incl. its canonical)
            # to avoid redundant rows; demote ALL transferred rows to non-canonical so
            # the winner keeps its single canonical name (uq_org_canonical_name).
            await db.execute(
                "DELETE FROM organization_names"
                " WHERE organization_id=$1"
                "   AND lower(name) IN ("
                "       SELECT lower(name) FROM organization_names WHERE organization_id=$2)",
                loser_id,
                winner_id,
            )
            await db.execute(
                "UPDATE organization_names SET organization_id=$1, is_canonical=FALSE"
                " WHERE organization_id=$2",
                winner_id,
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
            # Non-lossy demote+transfer (#255): same rule as names — keep the loser's
            # canonical acronym as a non-canonical alias on the winner. Dedup by
            # lower(acronym); demote all to non-canonical (uq_org_canonical_acronym).
            await db.execute(
                "DELETE FROM organization_acronyms"
                " WHERE organization_id=$1"
                "   AND lower(acronym) IN ("
                "       SELECT lower(acronym) FROM organization_acronyms WHERE organization_id=$2)",
                loser_id,
                winner_id,
            )
            await db.execute(
                "UPDATE organization_acronyms SET organization_id=$1, is_canonical=FALSE"
                " WHERE organization_id=$2",
                winner_id,
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
            dropped_assignments += await _absorb_role(
                db, conflict["winner_role_id"], conflict["loser_role_id"]
            )

        await db.execute(
            "UPDATE roles SET organization_id=$1 WHERE organization_id=$2",
            winner_id,
            loser_id,
        )
        # Dedup: remove loser rows where winner already has the same logical record.
        # Each table that follows in the bulk reassign loop needs a corresponding dedup
        # DELETE here; omitting one silently produces duplicate rows after merge.
        # entity_addresses: FK-level dedup only; normalised-form dedup lives in write_addresses().
        # The validity window is part of the identity (#181) — IS NOT DISTINCT FROM so a
        # loser row covering a different window survives as history, not a duplicate.
        await db.execute(
            """DELETE FROM entity_addresses l
               WHERE l.entity_type='organization' AND l.entity_id=$1
                 AND EXISTS (
                     SELECT 1 FROM entity_addresses w
                     WHERE w.entity_type='organization' AND w.entity_id=$2
                       AND w.address_id   = l.address_id
                       AND w.address_type = l.address_type
                       AND w.valid_from   IS NOT DISTINCT FROM l.valid_from
                       AND w.valid_until  IS NOT DISTINCT FROM l.valid_until
                 )""",
            loser_id,
            winner_id,
        )
        await db.execute(
            """DELETE FROM contact_methods
               WHERE entity_type='organization' AND entity_id=$1
                 AND (contact_type, value) IN (
                     SELECT contact_type, value FROM contact_methods
                     WHERE entity_type='organization' AND entity_id=$2
                 )""",
            loser_id,
            winner_id,
        )
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

        # Reassign: bulk-move all remaining loser rows to winner.
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
        # Citations (#319) on the loser org move to the winner before the delete.
        await rehome_citations(db, "organization", [(loser_id, winner_id)])
        # The loser's entity_events aren't re-pointed by merge (they dangle when the
        # org is deleted), so their citations would orphan — drop them (#319).
        await delete_event_citations_for_owner(db, "organization", loser_id)

        # #467: a key watching the loser also watches the winner from here on. The
        # loser's own subscription stays — the feed joins subscriptions at read time,
        # so removing it would hide the tombstone written just below.
        await mirror_subscriptions(db, [(loser_id, winner_id)])

        await db.execute("DELETE FROM organizations WHERE id=$1", loser_id)
        await db.execute(
            "INSERT INTO deleted_entities (entity_type, entity_id, merged_into)"
            " VALUES ('organization', $1, $2) ON CONFLICT DO NOTHING",
            loser_id,
            winner_id,
        )
    await invalidate_dup_count_cache(db)
    return dropped_assignments


@router.get("/duplicates/")
async def orgs_duplicates(
    request: Request,
    flash: str | None = Query(None),
    user: AdminUser = Depends(get_admin_user),
    db=Depends(get_db),
):
    """List near-duplicate organization pairs for review."""
    pairs = await _fetch_duplicate_pairs(db)
    flash_msg, resp_headers = resolve_query_flash(request, {}, flash)
    ctx = {
        "user": user,
        "active_section": "orgs_duplicates",
        "pairs": pairs,
        "flash_msg": flash_msg,
    }
    template = (
        "admin/orgs/_duplicates_region.html" if is_htmx(request) else "admin/orgs/duplicates.html"
    )
    return templates.TemplateResponse(request, template, ctx, headers=resp_headers)


@router.post("/{winner_id}/merge/{loser_id}/")
async def org_merge(
    winner_id: str,
    loser_id: str,
    request: Request,
    user: AdminUser = Depends(get_admin_user),
    db=Depends(get_db),
):
    """Merge loser into winner: reassign all references, hard-delete loser.

    Bulk (non-curated) path — `_execute_merge` with `keep_name_ids=None`, which is
    now non-lossy (demote+transfer, #255). Since #255 the list UI no longer enters
    here; it opens the preview modal and posts to `org_merge_with` with
    `return_to=list`. This route remains for the duplicates-review fallback,
    programmatic/script callers, and as the defense-in-depth non-lossy bulk merge.
    """
    if winner_id == loser_id:
        raise HTTPException(status_code=400, detail="Cannot merge an organization with itself")
    winner_name = await db.fetchval(
        "SELECT display_name FROM v_org_display_names WHERE organization_id=$1", winner_id
    )
    loser_name = await db.fetchval(
        "SELECT display_name FROM v_org_display_names WHERE organization_id=$1", loser_id
    )
    dropped = await _execute_merge(db, winner_id, loser_id)
    if is_htmx(request):
        body = (
            f"Merged <strong>{escape(loser_name)}</strong> into "
            f'<a href="/admin/orgs/{winner_id}/"><strong>{escape(winner_name)}</strong></a>. '
            f"Review URLs, roles, and contact info for duplicates."
        )
        # Parity with the detail-flow `org_merge_with`: surface silently-dropped
        # duplicate role assignments so the admin knows data changed shape.
        body += _dropped_assignments_note(dropped)
        body += await _winner_lifespan_note(db, winner_id)
        # List-flow branch (#250): merge initiated from /admin/orgs/. HX-Target
        # identifies the swap region; re-render the full `_region.html` (rows +
        # caption total + sticky pagination) so post-merge counts stay
        # consistent. Filter state preserved via HX-Current-URL.
        if request.headers.get("HX-Target") == _LIST_TARGET:
            return await _render_orgs_list_region(request, db, user, body)
        # Duplicates-review-screen branch (existing).
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
            headers=flash_trigger("success", body, extra={"refreshDupBadge": True}),
        )
    return RedirectResponse(with_flash("/admin/orgs/duplicates/", "saved"), status_code=303)


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
    return_to: str = Form(default="detail"),
):
    """Merge loser into winner from a preview modal.

    `return_to="list"` (modal opened from the orgs list, #255) re-renders the orgs
    list region in place; otherwise (detail / duplicates screens) HX-Redirects to
    the winner detail page.
    """
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
    body += _dropped_assignments_note(dropped)
    body += await _winner_lifespan_note(db, winner_id)
    # List-context merge (#255): the preview modal was opened from the orgs list, so
    # re-render the list region in place instead of redirecting to winner detail.
    if (
        return_to == "list"
        and is_htmx(request)
        and request.headers.get("HX-Target") == _LIST_TARGET
    ):
        return await _render_orgs_list_region(request, db, user, body)
    redirect_url = f"/admin/orgs/{winner_id}/"
    if is_htmx(request):
        return HTMLResponse(
            "",
            headers={**flash_trigger("success", body), "HX-Redirect": redirect_url},
        )
    return RedirectResponse(with_flash(redirect_url, "saved"), status_code=303)


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
                "success", "Pair marked as not a duplicate.", extra={"refreshDupBadge": True}
            ),
        )
    return RedirectResponse(with_flash("/admin/orgs/duplicates/", "removed"), status_code=303)


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
    ctx: str = "",
    user: AdminUser = Depends(get_admin_user),
    db=Depends(get_db),
):
    """Return preview modal: impact of merging id_b into id_a (or flipped via ?winner=).

    `ctx="list"` (modal opened from the orgs list, #255) makes the modal form submit
    back to the list region (`return_to=list`) instead of redirecting to detail.
    """
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

    # #469 guardrail: both orgs carrying DIFFERENT values of one external
    # identifier type are two source records — a producer keys them 1:1, and a
    # merge silently makes that mapping N:1. Surface the conflict, demote the
    # merge behind an explicit acknowledgement, and offer link-as-successors.
    identifier_conflicts = await db.fetch(
        """SELECT eit.slug, eit.display_name,
                  i_w.value AS winner_value, i_l.value AS loser_value
           FROM identifiers i_w
           JOIN identifiers i_l
             ON i_l.entity_identifier_type_id = i_w.entity_identifier_type_id
            AND i_l.value <> i_w.value
           JOIN entity_identifier_types eit ON eit.id = i_w.entity_identifier_type_id
           WHERE i_w.entity_id = $1 AND i_l.entity_id = $2
             AND eit.entity_type = 'organization'
             AND NOT eit.is_internal
           ORDER BY eit.slug, i_w.value, i_l.value""",
        winner_id,
        loser_id,
    )

    # #467: state the blast radius on the assignments *before* the merge runs, not
    # in the flash afterwards. Every loser assignment moves; the subset that
    # collides with a winner row on (person, role, start_date) is the only part
    # that is destructive, and it is the number an admin needs before clicking.
    # Mirrors `_absorb_role`'s conflict query, scoped across every colliding pair.
    assignments_count = await db.fetchval(
        """SELECT count(*) FROM role_assignments ra
           JOIN roles r ON r.id = ra.role_id
           WHERE r.organization_id = $1 AND ra.archived_at IS NULL""",
        loser_id,
    )
    dropped_assignments_count = await db.fetchval(
        """SELECT count(*)
           FROM roles r_l
           JOIN roles r_w ON lower(r_w.title) = lower(r_l.title)
                          AND r_w.organization_id = $2
                          AND r_w.archived_at IS NULL
           JOIN role_assignments l ON l.role_id = r_l.id AND l.archived_at IS NULL
           JOIN role_assignments w
             ON w.role_id = r_w.id AND w.archived_at IS NULL
            AND w.person_id = l.person_id
            AND w.start_date IS NOT DISTINCT FROM l.start_date
           WHERE r_l.organization_id = $1 AND r_l.archived_at IS NULL""",
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
            "identifier_conflicts": identifier_conflicts,
            "assignments_count": assignments_count,
            "dropped_assignments_count": dropped_assignments_count,
            "ctx": ctx,
        },
    )
