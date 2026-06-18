"""Admin views for person merge and duplicate review."""

from datetime import UTC, datetime
from urllib.parse import parse_qs, urlsplit

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from markupsafe import escape

from src.api.admin.deps import (
    AdminUser,
    flash_trigger,
    get_admin_user,
    get_db,
    is_htmx,
)
from src.api.admin.people_dups import (
    CANDIDATE_WHERE,
)
from src.api.admin.people_dups import (
    invalidate_dup_count_cache as invalidate_person_dup_count_cache,
)
from src.api.admin.people_queries import query_people_rows
from src.core.db import generate_id

_LIST_TARGET = "people-list-region"
_DEFAULT_PAGE_SIZE = 50
_VALID_STATUSES = {"active", "archived"}


def _parse_list_filters_from_hx_current_url(request: Request) -> dict:
    """Parse `q` / `status` / `page` / `page_size` out of HX-Current-URL.

    HX-Current-URL is sent by HTMX on every request and carries the URL
    the user is currently on (e.g. `/admin/people/?q=foo&status=archived`).
    The merge POST has no query string of its own, so this header is the
    source of truth for which list view to re-render.

    Falls back to defaults silently on a missing or malformed header — the
    merge succeeded; surfacing a parse error here would be needlessly noisy.
    """
    defaults = {"q": "", "status": "active", "page": 1, "page_size": _DEFAULT_PAGE_SIZE}
    raw = request.headers.get("HX-Current-URL", "")
    if not raw:
        return defaults
    try:
        params = parse_qs(urlsplit(raw).query)
    except ValueError:
        return defaults

    q = (params.get("q", [""])[0] or "").strip()
    status = (params.get("status", ["active"])[0] or "active").lower()
    if status not in _VALID_STATUSES:
        status = "active"
    try:
        page = max(1, int(params.get("page", ["1"])[0]))
    except (TypeError, ValueError):
        page = 1
    try:
        page_size = int(params.get("page_size", [str(_DEFAULT_PAGE_SIZE)])[0])
    except (TypeError, ValueError):
        page_size = _DEFAULT_PAGE_SIZE
    if not 10 <= page_size <= 500:
        page_size = _DEFAULT_PAGE_SIZE
    return {"q": q, "status": status, "page": page, "page_size": page_size}


templates = Jinja2Templates(directory="src/templates")
router = APIRouter(prefix="/people", tags=["admin-people-merge"])


async def _fetch_duplicate_pairs(db) -> list:
    """Return near-duplicate person pairs; empty list if pg_trgm not installed."""
    try:
        return await db.fetch(
            f"""SELECT
                a.id AS a_id, dn_a.display_name AS a_name, a.created_at AS a_created,
                b.id AS b_id, dn_b.display_name AS b_name, b.created_at AS b_created,
                similarity(dn_a.display_name, dn_b.display_name) AS score,
                (SELECT count(*) FROM role_assignments
                 WHERE person_id = a.id AND archived_at IS NULL) AS a_roles,
                (SELECT count(*) FROM role_assignments
                 WHERE person_id = b.id AND archived_at IS NULL) AS b_roles
            {CANDIDATE_WHERE}
            ORDER BY score DESC"""
        )
    except asyncpg.exceptions.UndefinedFunctionError:
        return []


@router.get("/duplicates/")
async def people_duplicates(
    request: Request,
    user: AdminUser = Depends(get_admin_user),
    db=Depends(get_db),
):
    """List near-duplicate person pairs for review."""
    pairs = await _fetch_duplicate_pairs(db)
    ctx = {
        "user": user,
        "active_section": "people_duplicates",
        "pairs": pairs,
    }
    return templates.TemplateResponse(
        request,
        "admin/people/_duplicates_region.html"
        if is_htmx(request)
        else "admin/people/duplicates.html",
        ctx,
    )


class PersonNotFoundError(LookupError):
    """Raised by merge_person_into when winner or loser id is not in `people`."""


async def merge_person_into(
    db,
    *,
    winner_id: str,
    loser_id: str,
    actor_email: str,
    loser_display_name: str | None = None,
) -> None:
    """Merge `loser_id` into `winner_id` — reassign references + hard-delete loser.

    Caller MUST own the surrounding transaction; this function executes
    flat SQL inside it (acquires `FOR UPDATE` locks first). Caller is also
    responsible for `invalidate_person_dup_count_cache()` after commit.

    Args:
        db: an asyncpg Connection or pool acquire — must support
            ``fetchrow`` / ``execute``.
        winner_id, loser_id: ULIDs of the two ``people`` rows.
        actor_email: shown in the merge audit prepended to ``people.notes``.
            Use the admin user's email from the route, or a tag like
            ``"data-quality-cleanup-#135"`` from a script.
        loser_display_name: optional pre-fetched display name for the
            audit-line. If ``None`` the helper looks it up via
            ``v_person_display_names``.

    Raises:
        PersonNotFoundError: when either ``winner_id`` or ``loser_id``
            is missing from ``people``.
    """
    winner = await db.fetchrow(
        "SELECT id, notes FROM people WHERE id=$1 FOR UPDATE",
        winner_id,
    )
    loser = await db.fetchrow(
        "SELECT id, notes FROM people WHERE id=$1 FOR UPDATE",
        loser_id,
    )
    if not winner or not loser:
        raise PersonNotFoundError(
            f"merge_person_into: missing person row "
            f"(winner_id={winner_id!r} found={bool(winner)}, "
            f"loser_id={loser_id!r} found={bool(loser)})"
        )

    if loser_display_name is None:
        loser_display_name = (
            await db.fetchval(
                "SELECT display_name FROM v_person_display_names WHERE person_id=$1",
                loser_id,
            )
            or loser_id
        )

    # notes: prefix loser's notes with merge metadata and append to winner
    if loser["notes"]:
        merge_date = datetime.now(UTC).strftime("%Y-%m-%d")
        prefix = f"Merged from {loser_display_name} on {merge_date} by {actor_email}"
        appended = f"{prefix}\n{loser['notes']}"
        new_notes = f"{winner['notes']}\n\n{appended}" if winner["notes"] else appended
        await db.execute(
            "UPDATE people SET notes=$1 WHERE id=$2",
            new_notes,
            winner_id,
        )

    # person_names: demote loser's canonical, drop exact-name duplicates,
    # then reassign remaining loser names to winner.
    # visibility-allowlist (issue #121): merge deduplicates and reassigns
    # ALL name rows regardless of visibility — the merged winner must
    # inherit the loser's deadnames, hidden names, etc.
    await db.execute(
        "UPDATE person_names SET is_canonical=FALSE WHERE person_id=$1 AND is_canonical=TRUE",
        loser_id,
    )
    await db.execute(
        "DELETE FROM person_names"
        " WHERE person_id=$1"
        "   AND name IN (SELECT name FROM person_names WHERE person_id=$2)",
        loser_id,
        winner_id,
    )
    await db.execute(
        "UPDATE person_names SET person_id=$1 WHERE person_id=$2",
        winner_id,
        loser_id,
    )

    # role_assignments: delete conflicts (same role+start_date), then reassign.
    await db.execute(
        """DELETE FROM role_assignments
           WHERE person_id=$1 AND archived_at IS NULL
             AND (role_id, COALESCE(start_date, '0001-01-01')) IN (
                 SELECT role_id, COALESCE(start_date, '0001-01-01')
                 FROM role_assignments
                 WHERE person_id=$2 AND archived_at IS NULL
             )""",
        loser_id,
        winner_id,
    )
    await db.execute(
        "UPDATE role_assignments SET person_id=$1 WHERE person_id=$2",
        winner_id,
        loser_id,
    )

    # links: drop loser's duplicates (same url+link_type already on winner) before reassigning.
    await db.execute(
        """DELETE FROM links
           WHERE entity_type='person' AND entity_id=$1
             AND (url, link_type_id) IN (
                 SELECT url, link_type_id FROM links
                 WHERE entity_type='person' AND entity_id=$2
             )""",
        loser_id,
        winner_id,
    )

    # Polymorphic entity tables.
    for table in (
        "contact_methods",
        "links",
        "entity_addresses",
        "import_provenance",
        "field_confidence",
    ):
        await db.execute(
            f"UPDATE {table} SET entity_id=$1 "  # noqa: S608
            f"WHERE entity_type='person' AND entity_id=$2",
            winner_id,
            loser_id,
        )

    # identifiers (no entity_type column).
    await db.execute(
        "UPDATE identifiers SET entity_id=$1 WHERE entity_id=$2",
        winner_id,
        loser_id,
    )

    # duplicate_dismissals: delete the merged pair, reassign others.
    await db.execute(
        "DELETE FROM duplicate_dismissals"
        " WHERE entity_type='person'"
        "   AND ((entity_a_id=$1 AND entity_b_id=$2)"
        "    OR  (entity_a_id=$2 AND entity_b_id=$1))",
        winner_id,
        loser_id,
    )
    await db.execute(
        """DELETE FROM duplicate_dismissals dd
           USING duplicate_dismissals dw
           WHERE dd.entity_type = 'person'
             AND dw.entity_type = 'person'
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
           WHERE dd.entity_type = 'person'
             AND dw.entity_type = 'person'
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
           WHERE entity_type='person' AND entity_a_id=$2""",
        winner_id,
        loser_id,
    )
    await db.execute(
        """UPDATE duplicate_dismissals
           SET entity_a_id = LEAST(entity_a_id, $1),
               entity_b_id = GREATEST(entity_a_id, $1)
           WHERE entity_type='person' AND entity_b_id=$2""",
        winner_id,
        loser_id,
    )

    await db.execute("DELETE FROM people WHERE id=$1", loser_id)
    await db.execute(
        "INSERT INTO deleted_entities (entity_type, entity_id) VALUES ('person', $1)"
        " ON CONFLICT DO NOTHING",
        loser_id,
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
            filters = _parse_list_filters_from_hx_current_url(request)
            rows, count, pctx = await query_people_rows(db, **filters)
            ctx = {
                "user": user,
                "active_section": "people",
                "people": rows,
                "total": count,
                "q": filters["q"],
                "status": filters["status"],
                "page_size": filters["page_size"],
                **pctx,
            }
            return templates.TemplateResponse(
                request,
                "admin/people/_region.html",
                ctx,
                headers=flash_trigger("success", body, extra={"refreshDupBadge": True}),
            )
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
    return RedirectResponse("/admin/people/duplicates/", status_code=303)


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
                "info", "Pair marked as not a duplicate.", extra={"refreshDupBadge": True}
            ),
        )
    return RedirectResponse("/admin/people/duplicates/", status_code=303)
