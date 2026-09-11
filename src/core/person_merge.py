"""The person merge primitive: fold one `people` row into another (#514).

Moved out of `src/api/admin/people_merge.py` so a caller outside the admin tree
can run exactly the merge the admin runs: the desired-state applier acting on a
producer's merge tombstone (#514), beside the admin routes and
`scripts/cleanup_person_name_data_quality.py`. Every caller owns the transaction.
"""

from datetime import UTC, datetime

from src.core.ancillary_migrate import (
    delete_event_citations_for_owner,
    migrate_citations,
    rehome_assignment_relationships,
    rehome_citations,
    rehome_conflicting_assignment_ancillary,
)
from src.core.merge_signals import mirror_subscriptions, record_merge_tombstones
from src.core.observation import NO_AUTO_CANONICAL_NAME_TYPES, heal_person_canonical

__all__ = ["PersonNotFoundError", "merge_person_into"]

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

    # #319: snapshot the loser's name ids before any name delete. A name that is
    # re-pointed keeps its id (its citations stay valid); one that is deleted (dedup
    # or curated drop) loses it. After the name ops, citations still pointing at a
    # vanished loser name id are cleaned up (dedup-matched ones are re-homed to the
    # winner name first, just below the dedup DELETE).
    loser_name_ids = [
        r["id"] for r in await db.fetch("SELECT id FROM person_names WHERE person_id=$1", loser_id)
    ]

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
    # #319: re-home citations off each loser name about to be deduped away → the
    # winner's surviving equivalent (same LATERAL lowest-id pick as the reading
    # re-point), so a matched-duplicate name's provenance follows the winner name
    # rather than orphaning. Must precede the dedup DELETE.
    dedup_name_pairs = await db.fetch(
        "SELECT l.id AS loser, m.id AS winner FROM person_names l"
        " CROSS JOIN LATERAL ("
        "     SELECT w.id FROM person_names w WHERE w.person_id=$2 AND"
        + _NAME_IDENTITY_MATCH_SQL
        + " ORDER BY w.id LIMIT 1"
        " ) m WHERE l.person_id=$1",
        loser_id,
        winner_id,
        list(NO_AUTO_CANONICAL_NAME_TYPES),
    )
    for pair in dedup_name_pairs:
        await migrate_citations(db, "person_name", pair["loser"], pair["winner"])

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
    # #319: any citation still pointing at a loser name id that was deleted (a
    # curated drop, or a dedup with no re-home target) is now a true orphan — the
    # name assertion is gone. Drop them (re-pointed names kept their id, so their
    # citations survive this NOT EXISTS filter).
    if loser_name_ids:
        await db.execute(
            "DELETE FROM citations WHERE entity_type='person_name'"
            " AND entity_id = ANY($1::text[])"
            " AND NOT EXISTS (SELECT 1 FROM person_names pn WHERE pn.id = citations.entity_id)",
            loser_name_ids,
        )
    # The loser's canonical was demoted above, so a winner that had no display
    # pointer would end up blank even though a usable name just arrived (CR4
    # #29). Merge is the only mutation left that can violate the #308 invariant
    # without repairing it — the observation path, the name-delete path and the
    # backfill all self-heal. No-op when the winner already displays.
    await heal_person_canonical(db, winner_id)

    # role_assignments: delete conflicts (same role+start_date), then reassign.
    # #324: re-home the conflict rows' polymorphic ancillary (links / contact_methods
    # / field_confidence / identifiers / import_provenance — see ancillary_migrate)
    # onto the surviving winner assignment BEFORE the hard-delete, else those rows
    # keyed on the deleted id are silently orphaned.
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
    _conflict_pairs = [(r["loser_ra"], r["winner_ra"]) for r in conflict_pairs]
    await rehome_conflicting_assignment_ancillary(db, _conflict_pairs)
    # #301: re-point the loser assignments' active relationship edges onto the
    # winner before the hard-delete, else FK ON DELETE CASCADE silently drops them.
    await rehome_assignment_relationships(db, _conflict_pairs)
    # #467: whoever watches a dropped duplicate also watches its survivor.
    await mirror_subscriptions(db, _conflict_pairs)
    # Delete exactly the rows we just re-homed — deriving the DELETE set from the
    # same `conflict_pairs` (rather than re-deriving via a COALESCE sentinel) keeps
    # the re-homed set and the deleted set provably identical, so no conflict row
    # can be deleted without its ancillary first moving to the survivor (#324 CR2).
    await db.execute(
        "DELETE FROM role_assignments WHERE id = ANY($1::text[])",
        [r["loser_ra"] for r in conflict_pairs],
    )
    # #467: each dropped duplicate is announced with the survivor it folded into.
    # The person tombstone below does not cover these — a producer polls
    # /api/v1/changes filtered by its own subscriptions, which are per-assignment.
    await record_merge_tombstones(db, "role_assignment", _conflict_pairs)
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

    # Citations (#319) on the loser person move to the winner before the delete
    # (whole-entity + field citations; NULL-safe dedup, self-emitting trigger).
    await rehome_citations(db, "person", [(loser_id, winner_id)])
    # The loser's entity_events aren't re-pointed by merge (they dangle when the
    # person is deleted), so their citations would orphan — drop them (#319).
    await delete_event_citations_for_owner(db, "person", loser_id)

    await db.execute("DELETE FROM people WHERE id=$1", loser_id)
    # #467: a key watching the loser also watches the winner; its own subscription
    # stays, or the tombstone below would have no audience.
    await mirror_subscriptions(db, [(loser_id, winner_id)])
    await db.execute(
        "INSERT INTO deleted_entities (entity_type, entity_id, merged_into)"
        " VALUES ('person', $1, $2) ON CONFLICT DO NOTHING",
        loser_id,
        winner_id,
    )
