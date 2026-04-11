"""Admin views for person merge and duplicate review."""

from datetime import UTC, datetime

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
from src.api.admin.org_dups import get_org_dup_count
from src.api.admin.people_dups import (
    CANDIDATE_WHERE,
    get_person_dup_count,
)
from src.api.admin.people_dups import (
    invalidate_dup_count_cache as invalidate_person_dup_count_cache,
)
from src.core.db import generate_id

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
    org_dup_count: int = Depends(get_org_dup_count),
    person_dup_count: int = Depends(get_person_dup_count),
):
    """List near-duplicate person pairs for review."""
    pairs = await _fetch_duplicate_pairs(db)
    ctx = {
        "user": user,
        "active_section": "people_duplicates",
        "pairs": pairs,
        "org_dup_count": org_dup_count,
        "person_dup_count": person_dup_count,
    }
    return templates.TemplateResponse(
        request,
        "admin/people/_duplicates_region.html"
        if is_htmx(request)
        else "admin/people/duplicates.html",
        ctx,
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
        winner = await db.fetchrow("SELECT id, notes FROM people WHERE id=$1 FOR UPDATE", winner_id)
        loser = await db.fetchrow("SELECT id, notes FROM people WHERE id=$1 FOR UPDATE", loser_id)
        if not winner or not loser:
            raise HTTPException(status_code=404, detail="Person not found")

        # notes: prefix loser's notes with merge metadata and append to winner
        if loser["notes"]:
            merge_date = datetime.now(UTC).strftime("%Y-%m-%d")
            prefix = f"Merged from {loser_name} on {merge_date} by {user.email}"
            appended = f"{prefix}\n{loser['notes']}"
            new_notes = f"{winner['notes']}\n\n{appended}" if winner["notes"] else appended
            await db.execute("UPDATE people SET notes=$1 WHERE id=$2", new_notes, winner_id)

        # person_names: demote loser's canonical to alias, drop exact name duplicates,
        # then reassign remaining loser names to winner
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

        # role_assignments: delete conflicts (same role+start_date on both), then reassign
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

        # Polymorphic entity tables
        for table in (
            "contact_methods",
            "links",
            "entity_addresses",
            "import_provenance",
            "field_confidence",
        ):
            await db.execute(
                f"UPDATE {table} SET entity_id=$1 WHERE entity_type='person' AND entity_id=$2",
                winner_id,
                loser_id,
            )

        # identifiers (no entity_type column)
        await db.execute(
            "UPDATE identifiers SET entity_id=$1 WHERE entity_id=$2",
            winner_id,
            loser_id,
        )

        # duplicate_dismissals: delete the merged pair, reassign any others referencing loser
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

    invalidate_person_dup_count_cache()

    if is_htmx(request):
        pairs = await _fetch_duplicate_pairs(db)
        body = (
            f"Merged <strong>{escape(loser_name)}</strong> into "
            f'<a href="/admin/people/{winner_id}/"><strong>{escape(winner_name)}</strong></a>. '
            f"Review role assignments and contact info for duplicates."
        )
        ctx = {
            "user": user,
            "active_section": "people_duplicates",
            "pairs": pairs,
        }
        return templates.TemplateResponse(
            request,
            "admin/people/_duplicates_region.html",
            ctx,
            headers=flash_trigger("success", body),
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
    invalidate_person_dup_count_cache()
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
            headers=flash_trigger("info", "Pair marked as not a duplicate."),
        )
    return RedirectResponse("/admin/people/duplicates/", status_code=303)
