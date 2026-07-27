"""Admin views for person merge and duplicate review."""

from datetime import UTC, datetime

import asyncpg
from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from markupsafe import escape

from src.api.admin.deps import (
    AdminUser,
    flash_trigger,
    get_admin_user,
    get_db,
    is_htmx,
)
from src.api.admin.list_filters import parse_list_filters
from src.api.admin.people_dups import (
    CANDIDATE_WHERE,
)
from src.api.admin.people_dups import (
    invalidate_dup_count_cache as invalidate_person_dup_count_cache,
)
from src.api.admin.people_queries import VALID_STATUSES, query_people_rows
from src.core.ancillary_migrate import rehome_conflicting_assignment_ancillary
from src.core.db import generate_id
from src.core.observation import NO_AUTO_CANONICAL_NAME_TYPES, heal_person_canonical

_LIST_TARGET = "people-list-region"

# Winner-vs-loser name-identity match, shared verbatim by the #309 reading
# re-point UPDATE and the dedup DELETE in `merge_person_into` so the two can
# never drift (CR #309 finding 1). Correlates a loser row `l` against a winner
# row `w`; every consumer binds the loser filter at $1, the winner at $2, and
# `NO_AUTO_CANONICAL_NAME_TYPES` as the $3 text[]. Two ordinary display types
# sharing (name, locale, script, visibility) collapse; a
# NO_AUTO_CANONICAL_NAME_TYPES row (mrz/reading/romanization/deadname) only ever
# matches its exact name_type. Rationale for each clause lives on the DELETE.
_NAME_IDENTITY_MATCH_SQL = (
    " w.name = l.name"
    " AND w.visibility = l.visibility"
    " AND w.locale IS NOT DISTINCT FROM l.locale"
    " AND w.script IS NOT DISTINCT FROM l.script"
    " AND ("
    "       w.name_type = l.name_type"
    "       OR (w.name_type <> ALL($3::text[]) AND l.name_type <> ALL($3::text[]))"
    "     )"
)

# Re-point reading/romanization children of about-to-be-deduped loser rows at
# the winner's surviving equivalent — the LATERAL picks one deterministically
# (lowest id) when several winner rows match.
_REPOINT_READING_CHILDREN_SQL = (
    "UPDATE person_names child"
    "   SET reading_of_id = m.id"
    "  FROM person_names l"
    "  CROSS JOIN LATERAL ("
    "       SELECT w.id FROM person_names w"
    "        WHERE w.person_id=$2 AND" + _NAME_IDENTITY_MATCH_SQL + " ORDER BY w.id LIMIT 1"
    "   ) m"
    " WHERE l.person_id=$1"
    "   AND child.reading_of_id = l.id"
)

# Drop loser rows whose identity matches a winner row (dedup). The trailing ")"
# closes the EXISTS opened above; _NAME_IDENTITY_MATCH_SQL ends on its own paren
# (the name_type group), so the assembled tail reads `…]))  )`.
_DEDUP_LOSER_NAMES_SQL = (
    "DELETE FROM person_names l"
    " WHERE l.person_id=$1"
    "   AND EXISTS ("
    "       SELECT 1 FROM person_names w WHERE w.person_id=$2 AND" + _NAME_IDENTITY_MATCH_SQL + ")"
)


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
    keep_name_ids: list[str] | None = None,
) -> None:
    """Merge `loser_id` into `winner_id` — reassign references + hard-delete loser.

    Caller MUST own the surrounding transaction; this function executes
    flat SQL inside it (acquires `FOR UPDATE` locks first). Caller is also
    responsible for `await invalidate_person_dup_count_cache(db)` after commit.

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
        keep_name_ids: curated-merge selection (#255). ``None`` (default)
            keeps the established behavior — inherit ALL of the loser's names,
            including deadnames / hidden (#121). A list keeps only those loser
            ``person_names.id`` rows (transferred as non-canonical aliases); the
            rest are dropped. An empty list drops every loser name.

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

    # Curated merge (#255): when the admin made an explicit keep/drop selection in
    # the preview modal, drop the unchecked loser names FIRST; the standard
    # demote+dedup+transfer below then moves only what remains. `keep_name_ids=None`
    # skips this — preserving the #121 inherit-ALL-names default for direct/script
    # callers (deadnames, hidden names, etc.).
    # #323: the curated drop shares the dedup DELETE's reading_of_id ON DELETE
    # CASCADE exposure, but only in one direction. Dropping a reading whose parent
    # is *kept* (case A) or dropping both (case B) are deliberate admin choices and
    # stay as-is. The dangerous case is C: the admin explicitly *keeps* a reading
    # but leaves its parent unchecked — dropping the parent would silently destroy
    # the kept child. Guard it by extending the keep-set to the parents of any kept
    # child: a row that anchors a checked `reading_of_id` child (reading /
    # romanization / mrz) is implicitly required, so it survives the drop keyed on
    # the FK's presence, not name_type (and the dedup below may still collapse it
    # into a winner equivalent, with #309 re-pointing the reading). The
    # `reading_of_id IS NOT NULL` filter keeps the subquery NULL-free so `NOT IN`
    # can't evaluate to UNKNOWN and swallow the whole DELETE.
    if keep_name_ids is not None:
        if keep_name_ids:
            placeholders = ", ".join(f"${i + 2}" for i in range(len(keep_name_ids)))
            await db.execute(
                f"DELETE FROM person_names WHERE person_id=$1"
                f" AND id NOT IN ({placeholders})"
                f" AND id NOT IN ("
                f"     SELECT reading_of_id FROM person_names"
                f"      WHERE person_id=$1 AND reading_of_id IS NOT NULL"
                f"        AND id IN ({placeholders})"
                f" )",
                loser_id,
                *keep_name_ids,
            )
        else:
            await db.execute("DELETE FROM person_names WHERE person_id=$1", loser_id)

    # person_names: demote loser's canonical, drop exact-name duplicates,
    # then reassign remaining loser names to winner.
    # visibility-allowlist (issue #121): merge deduplicates and reassigns
    # ALL name rows regardless of visibility — the merged winner must
    # inherit the loser's deadnames, hidden names, etc.
    await db.execute(
        "UPDATE person_names SET is_canonical=FALSE WHERE person_id=$1 AND is_canonical=TRUE",
        loser_id,
    )
    # Before the dedup DELETE runs, re-point any reading/romanization children
    # (#309) hanging off a loser row that is about to be deduped away.
    # `reading_of_id` is ON DELETE CASCADE, so a furigana row whose legal parent
    # duplicates a winner row would be destroyed even though the name-family edge
    # (#121) is not a duplicate of anything the winner holds. The re-point and
    # the DELETE share `_NAME_IDENTITY_MATCH_SQL` so the "which loser rows go
    # away" predicate can't drift between them. Scope: the dedup DELETE only;
    # curated `keep_name_ids` drops are deliberate admin choices and stay as-is.
    await db.execute(
        _REPOINT_READING_CHILDREN_SQL,
        loser_id,
        winner_id,
        list(NO_AUTO_CANONICAL_NAME_TYPES),
    )
    # Dedup on identity, not on the bare string (CR4 #30). Name-only matching
    # deleted the loser's `mrz` row because the winner had a `legal` row with
    # the same text — the data loss CR3 #22 fixed on the observation side.
    #
    # But merge is not `write_names`, and a pure four-column identity key is too
    # strict here: consolidating two records that were each split into
    # legal + variant leaves the winner holding `Jody` as *both*, which is
    # redundant rather than two claims. So a loser row is dropped when the text,
    # locale and script all match a winner row AND either the name_types are
    # equal, or **both** are ordinary display types.
    #
    # NO_AUTO_CANONICAL_NAME_TYPES (mrz, reading, romanization, deadname) are
    # never treated as interchangeable: identical text in one of those is a
    # machine-readable rendering, a distinct claim from a display name.
    #
    # `visibility` is part of the identity too (CR5 #43/#44) and is compared on
    # BOTH branches. Without it a `hidden` winner row absorbed a `public` loser
    # row carrying the same text — and since the loser's canonical is demoted
    # just above, that deleted the only promotable name and left the merged
    # person blank, defeating the heal below. It also silently dropped
    # `legal_only` claims, breaking the #121 guarantee that the winner inherits
    # the loser's restricted names.
    await db.execute(
        _DEDUP_LOSER_NAMES_SQL,
        loser_id,
        winner_id,
        list(NO_AUTO_CANONICAL_NAME_TYPES),
    )
    await db.execute(
        "UPDATE person_names SET person_id=$1 WHERE person_id=$2",
        winner_id,
        loser_id,
    )
    # The loser's canonical was demoted above, so a winner that had no display
    # pointer would end up blank even though a usable name just arrived (CR4
    # #29). Merge is the only mutation left that can violate the #308 invariant
    # without repairing it — the observation path, the name-delete path and the
    # backfill all self-heal. No-op when the winner already displays.
    await heal_person_canonical(db, winner_id)

    # role_assignments: delete conflicts (same role+start_date), then reassign.
    # #324: re-home the conflict rows' polymorphic ancillary onto the surviving
    # winner assignment BEFORE the hard-delete, else links / contact_methods /
    # field_confidence / identifiers keyed on the deleted id are silently orphaned.
    conflict_pairs = await db.fetch(
        """SELECT l.id AS loser_ra, w.id AS winner_ra
           FROM role_assignments l
           JOIN role_assignments w
             ON w.person_id=$2 AND w.archived_at IS NULL
            AND w.role_id = l.role_id
            AND w.start_date IS NOT DISTINCT FROM l.start_date
           WHERE l.person_id=$1 AND l.archived_at IS NULL""",
        loser_id,
        winner_id,
    )
    await rehome_conflicting_assignment_ancillary(
        db, [(r["loser_ra"], r["winner_ra"]) for r in conflict_pairs]
    )
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

    # entity_addresses: drop loser rows where winner already has same address_id+type.
    # The validity window is part of the identity (#181) — IS NOT DISTINCT FROM so a
    # loser row covering a different window survives as history, not a duplicate.
    await db.execute(
        """DELETE FROM entity_addresses l
           WHERE l.entity_type='person' AND l.entity_id=$1
             AND EXISTS (
                 SELECT 1 FROM entity_addresses w
                 WHERE w.entity_type='person' AND w.entity_id=$2
                   AND w.address_id   = l.address_id
                   AND w.address_type = l.address_type
                   AND w.valid_from   IS NOT DISTINCT FROM l.valid_from
                   AND w.valid_until  IS NOT DISTINCT FROM l.valid_until
             )""",
        loser_id,
        winner_id,
    )

    # contact_methods: drop loser rows where winner already has same contact_type+value.
    await db.execute(
        """DELETE FROM contact_methods
           WHERE entity_type='person' AND entity_id=$1
             AND (contact_type, value) IN (
                 SELECT contact_type, value FROM contact_methods
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
        "INSERT INTO deleted_entities (entity_type, entity_id, merged_into)"
        " VALUES ('person', $1, $2) ON CONFLICT DO NOTHING",
        loser_id,
        winner_id,
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
    return RedirectResponse("/admin/people/duplicates/", status_code=303)


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
    return RedirectResponse(redirect_url, status_code=303)


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
