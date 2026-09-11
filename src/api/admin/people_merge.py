"""Admin views for person merge and duplicate review."""

import asyncpg
from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from markupsafe import escape

from src.api.admin.deps import (
    AdminUser,
    flash_trigger,
    get_admin_user,
    get_db,
    is_htmx,
    resolve_query_flash,
    with_flash,
)
from src.api.admin.list_filters import parse_list_filters
from src.api.admin.people_dups import (
    CANDIDATE_WHERE,
)
from src.api.admin.people_dups import (
    invalidate_dup_count_cache as invalidate_person_dup_count_cache,
)
from src.api.admin.people_queries import VALID_STATUSES, query_people_rows
from src.core.db import generate_id
from src.core.person_merge import PersonNotFoundError, merge_person_into

_LIST_TARGET = "people-list-region"


def _parse_list_filters_from_hx_current_url(request: Request) -> dict:
    """Parse the people list filters from HX-Current-URL (see `parse_list_filters`).

    Thin wrapper binding the people-specific status set (three-valued with
    ``all``, #306), imported from `people_queries` so the two can't drift; the
    parsing logic (and the default page size) is shared with Orgs via
    `src.api.admin.list_filters`.
    """
    return parse_list_filters(request, valid_statuses=VALID_STATUSES)


templates = Jinja2Templates(directory="src/templates")
router = APIRouter(prefix="/people", tags=["admin-people-merge"])


async def _render_people_list_region(request: Request, db, user: AdminUser, flash_body: str):
    """Re-render the people list region (rows + caption total + sticky pagination).

    Shared by the list-flow merge (`person_merge`) and the list-context preview-modal
    merge (`person_merge_with` with `return_to=list`, #255). Filter state read from
    HX-Current-URL.
    """
    filters = _parse_list_filters_from_hx_current_url(request)
    rows, count, pctx, hidden_matches = await query_people_rows(db, **filters)
    ctx = {
        "user": user,
        "active_section": "people",
        "people": rows,
        "total": count,
        "q": filters["q"],
        "status": filters["status"],
        "page_size": filters["page_size"],
        "hidden_matches": hidden_matches,
        **pctx,
    }
    return templates.TemplateResponse(
        request,
        "admin/people/_region.html",
        ctx,
        headers=flash_trigger("success", flash_body, extra={"refreshDupBadge": True}),
    )


async def _fetch_duplicate_pairs(db) -> list:
    """Return near-duplicate person pairs; empty list if pg_trgm not installed."""
    try:
        return await db.fetch(
            f"""SELECT
                sub.a_id,
                COALESCE(vdn_a.display_name, sub.a_match_name) AS a_name,
                sub.a_match_name,
                sub.a_match_is_canonical,
                sub.a_match_visibility,
                sub.a_created,
                sub.b_id,
                COALESCE(vdn_b.display_name, sub.b_match_name) AS b_name,
                sub.b_match_name,
                sub.b_match_is_canonical,
                sub.b_match_visibility,
                sub.b_created,
                sub.score,
                sub.a_roles,
                sub.b_roles
            FROM (
                SELECT DISTINCT ON (a.id, b.id)
                    a.id AS a_id,
                    dn_a.name AS a_match_name,
                    dn_a.is_canonical AS a_match_is_canonical,
                    dn_a.visibility AS a_match_visibility,
                    a.created_at AS a_created,
                    b.id AS b_id,
                    dn_b.name AS b_match_name,
                    dn_b.is_canonical AS b_match_is_canonical,
                    dn_b.visibility AS b_match_visibility,
                    b.created_at AS b_created,
                    similarity(dn_a.name, dn_b.name) AS score,
                    (SELECT count(*) FROM role_assignments
                     WHERE person_id = a.id AND archived_at IS NULL) AS a_roles,
                    (SELECT count(*) FROM role_assignments
                     WHERE person_id = b.id AND archived_at IS NULL) AS b_roles
                {CANDIDATE_WHERE}
                ORDER BY a.id, b.id, similarity(dn_a.name, dn_b.name) DESC
            ) sub
            LEFT JOIN v_person_display_names vdn_a ON vdn_a.person_id = sub.a_id
            LEFT JOIN v_person_display_names vdn_b ON vdn_b.person_id = sub.b_id
            ORDER BY score DESC"""
        )
    except asyncpg.exceptions.UndefinedFunctionError:
        return []


@router.get("/duplicates/")
async def people_duplicates(
    request: Request,
    flash: str | None = Query(None),
    user: AdminUser = Depends(get_admin_user),
    db=Depends(get_db),
):
    """List near-duplicate person pairs for review."""
    pairs = await _fetch_duplicate_pairs(db)
    flash_msg, resp_headers = resolve_query_flash(request, {}, flash)
    ctx = {
        "user": user,
        "active_section": "people_duplicates",
        "pairs": pairs,
        "flash_msg": flash_msg,
    }
    return templates.TemplateResponse(
        request,
        "admin/people/_duplicates_region.html"
        if is_htmx(request)
        else "admin/people/duplicates.html",
        ctx,
        headers=resp_headers,
    )


@router.post("/{winner_id}/merge/{loser_id}/")
async def person_merge(
    winner_id: str,
    loser_id: str,
    request: Request,
    user: AdminUser = Depends(get_admin_user),
    db=Depends(get_db),
):
    """Merge loser into winner: reassign all references, hard-delete loser."""

    winner_name = await db.fetchval(
        "SELECT display_name FROM v_person_display_names WHERE person_id=$1", winner_id
    )
    loser_name = await db.fetchval(
        "SELECT display_name FROM v_person_display_names WHERE person_id=$1", loser_id
    )

    async with db.transaction():
        try:
            await merge_person_into(
                db,
                winner_id=winner_id,
                loser_id=loser_id,
                actor_email=user.email,
                loser_display_name=loser_name,
            )
        except PersonNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Person not found") from exc

    await invalidate_person_dup_count_cache(db)

    if is_htmx(request):
        body = (
            f"Merged <strong>{escape(loser_name)}</strong> into "
            f'<a href="/admin/people/{winner_id}/"><strong>{escape(winner_name)}</strong></a>. '
            f"Review role assignments and contact info."
        )
        # List-flow branch (issue #137): merge initiated from /admin/people/.
        # HX-Target identifies the swap region; we re-render the full
        # `_region.html` (rows + caption total + sticky pagination) so the
        # post-merge counts stay consistent. Filter state preserved via
        # HX-Current-URL.
        if request.headers.get("HX-Target") == _LIST_TARGET:
            return await _render_people_list_region(request, db, user, body)
        # Duplicates-review-screen branch (existing).
        pairs = await _fetch_duplicate_pairs(db)
        ctx = {
            "user": user,
            "active_section": "people_duplicates",
            "pairs": pairs,
        }
        return templates.TemplateResponse(
            request,
            "admin/people/_duplicates_region.html",
            ctx,
            headers=flash_trigger("success", body, extra={"refreshDupBadge": True}),
        )
    return RedirectResponse(with_flash("/admin/people/duplicates/", "saved"), status_code=303)


@router.get("/{winner_id}/merge-preview/{loser_id}/")
async def person_merge_preview(
    winner_id: str,
    loser_id: str,
    request: Request,
    ctx: str = "",
    user: AdminUser = Depends(get_admin_user),
    db=Depends(get_db),
):
    """Return the merge-preview modal: impact of merging loser_id into winner_id (#255).

    `ctx="list"` makes the modal form submit back to the people list region. The
    swap button re-requests with the ids flipped in the path to reverse direction.
    """
    if winner_id == loser_id:
        raise HTTPException(status_code=400, detail="Cannot merge a person with itself")
    winner = await db.fetchrow(
        "SELECT id FROM people WHERE id=$1 AND archived_at IS NULL", winner_id
    )
    loser = await db.fetchrow("SELECT id FROM people WHERE id=$1 AND archived_at IS NULL", loser_id)
    if not winner or not loser:
        raise HTTPException(status_code=404)

    winner_name = await db.fetchval(
        "SELECT display_name FROM v_person_display_names WHERE person_id=$1", winner_id
    )
    loser_name = await db.fetchval(
        "SELECT display_name FROM v_person_display_names WHERE person_id=$1", loser_id
    )
    # All loser names — the admin manages hidden / deadnames too (#121), and each is
    # keepable as an alias; visibility is surfaced as a badge so an unchecked drop of
    # a sensitive name is a deliberate, informed choice. Both sides of the
    # reading→parent linkage (#323) are surfaced so the cascade guard is visible on
    # the actionable rows: `reading_of_name` labels the child ("reading of X") and
    # `has_reading_child` flags the parent (unchecking it still keeps it if a checked
    # reading points at it), rather than letting the transfer look inconsistent.
    loser_names = await db.fetch(
        "SELECT n.id, n.name, n.is_canonical, n.visibility, n.name_type,"
        "       parent.name AS reading_of_name,"
        "       EXISTS ("
        "           SELECT 1 FROM person_names c"
        "            WHERE c.reading_of_id = n.id AND c.person_id = n.person_id"
        "       ) AS has_reading_child"
        "  FROM person_names n"
        "  LEFT JOIN person_names parent ON parent.id = n.reading_of_id"
        " WHERE n.person_id=$1 ORDER BY n.is_canonical DESC, n.name",
        loser_id,
    )
    roles_count = await db.fetchval(
        "SELECT count(*) FROM role_assignments WHERE person_id=$1 AND archived_at IS NULL",
        loser_id,
    )
    contacts_count = await db.fetchval(
        "SELECT count(*) FROM contact_methods WHERE entity_type='person' AND entity_id=$1",
        loser_id,
    )
    links_count = await db.fetchval(
        "SELECT count(*) FROM links WHERE entity_type='person' AND entity_id=$1",
        loser_id,
    )
    addresses_count = await db.fetchval(
        "SELECT count(*) FROM entity_addresses WHERE entity_type='person' AND entity_id=$1",
        loser_id,
    )
    identifiers_count = await db.fetchval(
        """SELECT count(*) FROM identifiers i
           JOIN entity_identifier_types eit ON eit.id = i.entity_identifier_type_id
           WHERE i.entity_id=$1 AND eit.entity_type='person'""",
        loser_id,
    )

    return templates.TemplateResponse(
        request,
        "admin/people/_merge_preview_modal.html",
        {
            "winner_id": winner_id,
            "loser_id": loser_id,
            "winner_name": winner_name,
            "loser_name": loser_name,
            "loser_names": loser_names,
            "roles_count": roles_count,
            "contacts_count": contacts_count,
            "links_count": links_count,
            "addresses_count": addresses_count,
            "identifiers_count": identifiers_count,
            "ctx": ctx,
        },
    )


@router.post("/{winner_id}/merge-with/{loser_id}/")
async def person_merge_with(
    winner_id: str,
    loser_id: str,
    request: Request,
    user: AdminUser = Depends(get_admin_user),
    db=Depends(get_db),
    keep_name_ids: list[str] = Form(default=[]),
    return_to: str = Form(default="detail"),
):
    """Curated person merge from the preview modal (#255).

    `keep_name_ids` is authoritative — only the checked loser names transfer (the
    rest are dropped). `return_to="list"` re-renders the people list region in place;
    otherwise HX-Redirect to the winner detail page.
    """
    if winner_id == loser_id:
        raise HTTPException(status_code=400, detail="Cannot merge a person with itself")
    winner_name = await db.fetchval(
        "SELECT display_name FROM v_person_display_names WHERE person_id=$1", winner_id
    )
    loser_name = await db.fetchval(
        "SELECT display_name FROM v_person_display_names WHERE person_id=$1", loser_id
    )
    async with db.transaction():
        try:
            await merge_person_into(
                db,
                winner_id=winner_id,
                loser_id=loser_id,
                actor_email=user.email,
                loser_display_name=loser_name,
                keep_name_ids=keep_name_ids,
            )
        except PersonNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Person not found") from exc
    await invalidate_person_dup_count_cache(db)

    body = (
        f"Merged <strong>{escape(loser_name)}</strong> into "
        f'<a href="/admin/people/{winner_id}/"><strong>{escape(winner_name)}</strong></a>. '
        f"Review role assignments and contact info."
    )
    if (
        return_to == "list"
        and is_htmx(request)
        and request.headers.get("HX-Target") == _LIST_TARGET
    ):
        return await _render_people_list_region(request, db, user, body)
    redirect_url = f"/admin/people/{winner_id}/"
    if is_htmx(request):
        return HTMLResponse(
            "",
            headers={**flash_trigger("success", body), "HX-Redirect": redirect_url},
        )
    return RedirectResponse(with_flash(redirect_url, "saved"), status_code=303)


@router.post("/{id_a}/dismiss-duplicate/{id_b}/")
async def person_dismiss_duplicate(
    id_a: str,
    id_b: str,
    request: Request,
    user: AdminUser = Depends(get_admin_user),
    db=Depends(get_db),
):
    """Record that this pair is not a duplicate (suppress from future results)."""
    # Store with consistent ordering (a < b)
    a, b = (id_a, id_b) if id_a < id_b else (id_b, id_a)
    await db.execute(
        "INSERT INTO duplicate_dismissals"
        " (id, entity_type, entity_a_id, entity_b_id, dismissed_by)"
        " VALUES ($1, 'person', $2, $3, $4)"
        " ON CONFLICT (entity_type, entity_a_id, entity_b_id) DO NOTHING",
        generate_id(),
        a,
        b,
        user.email,
    )
    await invalidate_person_dup_count_cache(db)
    if is_htmx(request):
        pairs = await _fetch_duplicate_pairs(db)
        ctx = {
            "user": user,
            "active_section": "people_duplicates",
            "pairs": pairs,
        }
        return templates.TemplateResponse(
            request,
            "admin/people/_duplicates_region.html",
            ctx,
            headers=flash_trigger(
                "success", "Pair marked as not a duplicate.", extra={"refreshDupBadge": True}
            ),
        )
    return RedirectResponse(with_flash("/admin/people/duplicates/", "removed"), status_code=303)
