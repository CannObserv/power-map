"""Re-point polymorphic ancillary of roles & role_assignments during merge/delete.

Two entity types keep ancillary rows with **no FK**, so a merge or direct DELETE
can silently orphan them — rows pointing at an id that no longer exists, invisible
to every UI and to the change feed:

- **role_assignment (#324):** ``links`` / ``contact_methods`` / ``field_confidence``
  / ``identifiers`` / ``import_provenance`` keyed on
  ``(entity_type='role_assignment', entity_id)`` (identifiers scope through
  ``entity_identifier_types`` instead of an ``entity_type`` column).
- **role (#326):** ``links`` / ``contact_methods`` keyed on ``(entity_type='role',
  entity_id)`` — the two surfaces the admin role editors populate. (role is excluded
  from identifiers/field_confidence/import_provenance.)

Before a delete, each merge path re-homes the loser's ancillary onto the survivor
(``rehome_conflicting_assignment_ancillary`` for assignments,
:func:`rehome_role_ancillary` for roles): each row is re-pointed, or deleted when
the survivor already carries an identical one (mirrors
``scripts/archive_legacy_legislator_roles.py::_migrate_rows``); ``import_provenance``
is append-only and always re-points (never dedups). An admin hard delete — of any
entity type — instead drops the rows outright (:func:`delete_entity_ancillary`,
#605), relying on the entity's own 'deleted' tombstone.

**Survivor signal (#327).** ``links`` / ``contact_methods`` / ``identifiers`` now
carry touch-cascade triggers, so a re-point ``UPDATE`` self-emits an
``entity_changes`` 'updated' for the survivor (one per moved row). Only the
trigger-less telemetry tables — ``field_confidence`` and ``import_provenance``
(written per-ingestion, deliberately not triggered) — still need a manual emit,
gated on a move of one of those (:data:`TRIGGERLESS_ANCILLARY_TABLES`). ``rehome_role_ancillary``
touches only triggered tables, so it emits nothing manually at all.

**Curator overrides (#514).** ``curation_overlay`` is the same hazard for every
entity type the producer crosswalk scopes (person / organization / role /
assignment): :func:`rehome_curation_overlay` carries a loser's overrides to its
survivor, the survivor's own override winning a field clash.

**Entity events (#611).** A person or org merge re-points every event naming the
loser, as owner or as link target, onto the survivor
(:func:`rehome_entity_events`).
"""

from collections import defaultdict
from typing import NamedTuple

import asyncpg

from src.core.citations import CITABLE_ENTITY_TABLES

# Ancillary tables with NO touch-cascade trigger (#327): a re-point does not
# self-emit, so a survivor whose only change is one of these needs a manual
# entity_changes signal. Everything else (links/contact_methods/identifiers) is
# trigger-driven. Kept trigger-less on purpose — both are written per-ingestion,
# so a trigger would emit an 'updated' on every audit/confidence row.
TRIGGERLESS_ANCILLARY_TABLES = frozenset({"field_confidence", "import_provenance"})


class _AncillarySpec(NamedTuple):
    """One polymorphic ancillary table + the identity that dedups a row within an entity.

    ``key_fields=None`` marks an **append-only** table (e.g. ``import_provenance``):
    every row re-points wholesale, never dedups — history is not collapsed. Such a
    spec leaves ``exists_sql`` unused and ``select_sql`` need only return ``id``.
    """

    name: str
    select_sql: str  # $1 = source assignment id → rows with id (+ the two key cols)
    exists_sql: str | None  # $1 = target id, $2/$3 = key values → 1 if survivor has it
    key_fields: tuple[str, str] | None  # None → append-only, re-point every row


# Identity per table mirrors the archive script's key_fields and the ON CONFLICT
# targets in schema.sql. Identifiers have no entity_type column: their
# role_assignment scope comes from the joined entity_identifier_types row, and
# their per-entity identity is (entity_identifier_type_id, value).
_SPECS: tuple[_AncillarySpec, ...] = (
    _AncillarySpec(
        name="links",
        select_sql=(
            "SELECT id, url, link_type_id FROM links"
            " WHERE entity_type='role_assignment' AND entity_id=$1"
        ),
        exists_sql=(
            "SELECT 1 FROM links WHERE entity_type='role_assignment'"
            " AND entity_id=$1 AND url=$2 AND link_type_id=$3"
        ),
        key_fields=("url", "link_type_id"),
    ),
    _AncillarySpec(
        name="contact_methods",
        select_sql=(
            "SELECT id, contact_type, value FROM contact_methods"
            " WHERE entity_type='role_assignment' AND entity_id=$1"
        ),
        exists_sql=(
            "SELECT 1 FROM contact_methods WHERE entity_type='role_assignment'"
            " AND entity_id=$1 AND contact_type=$2 AND value=$3"
        ),
        key_fields=("contact_type", "value"),
    ),
    _AncillarySpec(
        name="field_confidence",
        select_sql=(
            "SELECT id, field_name, value_hash FROM field_confidence"
            " WHERE entity_type='role_assignment' AND entity_id=$1"
        ),
        exists_sql=(
            "SELECT 1 FROM field_confidence WHERE entity_type='role_assignment'"
            " AND entity_id=$1 AND field_name=$2 AND value_hash=$3"
        ),
        key_fields=("field_name", "value_hash"),
    ),
    _AncillarySpec(
        name="identifiers",
        select_sql=(
            "SELECT i.id, i.entity_identifier_type_id, i.value FROM identifiers i"
            " JOIN entity_identifier_types t ON t.id = i.entity_identifier_type_id"
            " WHERE t.entity_type='role_assignment' AND i.entity_id=$1"
        ),
        exists_sql=(
            "SELECT 1 FROM identifiers"
            " WHERE entity_id=$1 AND entity_identifier_type_id=$2 AND value=$3"
        ),
        key_fields=("entity_identifier_type_id", "value"),
    ),
    _AncillarySpec(
        # Append-only import audit (#324 CR2): no unique key, each row a distinct
        # historical event — re-point every row, never dedup.
        name="import_provenance",
        select_sql=(
            "SELECT id FROM import_provenance WHERE entity_type='role_assignment' AND entity_id=$1"
        ),
        exists_sql=None,
        key_fields=None,
    ),
)


async def count_orphaned_role_assignment_ancillary(
    db: asyncpg.Connection,
) -> dict[str, int]:
    """Count ancillary rows pointing at a role_assignment id that no longer exists.

    The polymorphic ancillary has no FK, so a merge (or any direct DELETE) that
    drops an assignment can strand these rows undetected. Returns ``{table: n}``
    for every spec; the daily guard (#324) warns when any count is non-zero.
    """
    counts: dict[str, int] = {}
    for spec in _SPECS:
        if spec.name == "identifiers":
            sql = (
                "SELECT count(*) FROM identifiers i"
                " JOIN entity_identifier_types t ON t.id = i.entity_identifier_type_id"
                " WHERE t.entity_type='role_assignment'"
                " AND NOT EXISTS (SELECT 1 FROM role_assignments ra WHERE ra.id = i.entity_id)"
            )
        else:
            sql = (
                f"SELECT count(*) FROM {spec.name} x"
                " WHERE x.entity_type='role_assignment'"
                " AND NOT EXISTS (SELECT 1 FROM role_assignments ra WHERE ra.id = x.entity_id)"
            )
        counts[spec.name] = await db.fetchval(sql)
    return counts


async def _migrate_specs(
    db: asyncpg.Connection,
    specs: tuple[_AncillarySpec, ...],
    from_id: str,
    to_id: str,
) -> dict[str, tuple[int, int]]:
    """Re-point/dedup one entity's ancillary onto another for the given specs.

    Shared by the role_assignment (:data:`_SPECS`) and role (:data:`_ROLE_SPECS`)
    migrators — the only difference between the two is the spec list.
    """
    result: dict[str, tuple[int, int]] = {}
    for spec in specs:
        moved = deduped = 0
        for row in await db.fetch(spec.select_sql, from_id):
            if spec.key_fields is None:
                # Append-only table (e.g. import_provenance): re-point every row.
                await db.execute(
                    f"UPDATE {spec.name} SET entity_id=$2 WHERE id=$1", row["id"], to_id
                )
                moved += 1
                continue
            exists = await db.fetchval(
                spec.exists_sql, to_id, row[spec.key_fields[0]], row[spec.key_fields[1]]
            )
            if exists:
                await db.execute(f"DELETE FROM {spec.name} WHERE id=$1", row["id"])
                deduped += 1
            else:
                await db.execute(
                    f"UPDATE {spec.name} SET entity_id=$2 WHERE id=$1", row["id"], to_id
                )
                moved += 1
        result[spec.name] = (moved, deduped)
    return result


async def migrate_role_assignment_ancillary(
    db: asyncpg.Connection, from_id: str, to_id: str
) -> dict[str, tuple[int, int]]:
    """Re-point one assignment's ancillary onto another; dedup exact duplicates.

    Returns ``{table: (moved, deduped)}``. ``moved`` rows are re-pointed to
    ``to_id``; ``deduped`` rows are deleted because ``to_id`` already carries an
    identical row. Append-only specs (``key_fields=None``, e.g. ``import_provenance``)
    re-point every row and never dedup, so their ``deduped`` count is always 0.
    Does not emit outbox signals — callers do, via
    :func:`rehome_conflicting_assignment_ancillary`.
    """
    return await _migrate_specs(db, _SPECS, from_id, to_id)


async def rehome_conflicting_assignment_ancillary(
    db: asyncpg.Connection, pairs: list[tuple[str, str]]
) -> dict[str, tuple[int, int]]:
    """Migrate ancillary for every ``(loser, winner)`` pair; signal changed survivors.

    Call this immediately before a merge hard-deletes the loser assignments. The
    survivor is signalled via ``entity_changes`` 'updated' when its ancillary
    actually moved (a pure dedup changes nothing on the survivor, so no signal):
    re-pointing a ``links`` / ``contact_methods`` / ``identifiers`` row self-emits
    through that table's touch trigger (#327), while a ``field_confidence`` /
    ``import_provenance`` move (trigger-less) gets a manual emit here. Returns
    per-table ``(moved, deduped)`` totals across all pairs.
    """
    totals: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    touched_survivors: set[str] = set()
    for loser_id, winner_id in pairs:
        counts = await migrate_role_assignment_ancillary(db, loser_id, winner_id)
        # Citations (#319) on the deleted assignment move to the survivor too;
        # NULL-safe identity, self-emitting touch trigger (no manual signal).
        c_moved, c_deduped = await migrate_citations(db, "role_assignment", loser_id, winner_id)
        counts["citations"] = (c_moved, c_deduped)
        for table, (moved, deduped) in counts.items():
            totals[table][0] += moved
            totals[table][1] += deduped
            # Triggered tables self-emit on re-point (#327); only a trigger-less
            # move needs a manual survivor signal.
            if moved and table in TRIGGERLESS_ANCILLARY_TABLES:
                touched_survivors.add(winner_id)
    for winner_id in touched_survivors:
        await db.execute(
            "INSERT INTO entity_changes (entity_type, entity_id, change_kind)"
            " VALUES ('role_assignment', $1, 'updated')",
            winner_id,
        )
    return {table: (counts[0], counts[1]) for table, counts in totals.items()}


# ---------------------------------------------------------------------------
# Role-assignment relationship edges (#301)
#
# Unlike the polymorphic ancillary above, role_assignment_relationships is an
# FK-backed table (ON DELETE CASCADE on both endpoints). So it can never *orphan*
# — but that CASCADE is exactly the hazard on merge: hard-deleting a losing
# assignment would silently CASCADE-delete its active edges (a staffer/principal
# relationship vanishing). Every merge that hard-deletes an assignment must
# re-home the loser's *active* edges onto the survivor BEFORE the delete. Archived
# edges are left to CASCADE (already retracted — they emitted their 'updated' +
# archived_at on retract). The re-point UPDATE self-emits the edge's own change row
# (trg_entity_changes_* is INSERT/UPDATE) plus touches both endpoints; a dedup /
# CASCADE hard-delete fires only the touch trigger (the change trigger is not a
# DELETE trigger) — so a hard-deleted edge leaves no per-edge tombstone. The
# parenthetical that used to stand here — "exactly how merged role_assignments are
# handled (the parent person/org tombstone covers it)" — was the assumption #467
# disproved: a subscriber's feed is filtered by its OWN subscriptions, which are
# per-assignment, so a parent tombstone it may not hold announces nothing. Merged
# role_assignments now get their own tombstone via
# :func:`src.core.merge_signals.record_merge_tombstones`. Edges keep the old
# behaviour deliberately: an edge is reachable only through its endpoints, both of
# which are announced, so it has no independent anchor to repair.
# ---------------------------------------------------------------------------


async def rehome_assignment_relationships(
    db: asyncpg.Connection, pairs: list[tuple[str, str]]
) -> None:
    """Re-point active relationship edges off each ``(loser, winner)`` assignment.

    For every loser assignment, its active edges (either endpoint) move to the
    winner, EXCEPT where the move would create a self-edge (the other endpoint is
    already the winner) or collide with an edge the winner already has (same
    ``from``/``to``/``rel_type``) — those are deleted (the winner's copy wins),
    mirroring the ancillary dedup. Call immediately before the merge hard-deletes
    the loser assignments; the FK ``ON DELETE CASCADE`` then cleans up only the
    now-archived remainder. No manual outbox emit: the re-point UPDATE self-emits the
    edge's change row + touches both endpoints, while a dedup / CASCADE hard-delete
    fires only the touch trigger (``trg_entity_changes_*`` is INSERT/UPDATE-only, so a
    hard-deleted edge emits no per-edge tombstone — consistent with merged
    role_assignments, whose parent person/org tombstone covers them).
    """
    for loser_id, winner_id in pairs:
        if loser_id == winner_id:
            continue
        # from-side: drop self-edges + winner collisions, then re-point the rest.
        await db.execute(
            """DELETE FROM role_assignment_relationships r
               WHERE r.from_assignment_id=$1 AND r.archived_at IS NULL
                 AND ( r.to_assignment_id=$2
                       OR EXISTS (
                           SELECT 1 FROM role_assignment_relationships s
                           WHERE s.from_assignment_id=$2
                             AND s.to_assignment_id=r.to_assignment_id
                             AND s.rel_type_id=r.rel_type_id
                             AND s.archived_at IS NULL) )""",
            loser_id,
            winner_id,
        )
        await db.execute(
            "UPDATE role_assignment_relationships SET from_assignment_id=$2"
            " WHERE from_assignment_id=$1 AND archived_at IS NULL",
            loser_id,
            winner_id,
        )
        # to-side: same, mirrored.
        await db.execute(
            """DELETE FROM role_assignment_relationships r
               WHERE r.to_assignment_id=$1 AND r.archived_at IS NULL
                 AND ( r.from_assignment_id=$2
                       OR EXISTS (
                           SELECT 1 FROM role_assignment_relationships s
                           WHERE s.to_assignment_id=$2
                             AND s.from_assignment_id=r.from_assignment_id
                             AND s.rel_type_id=r.rel_type_id
                             AND s.archived_at IS NULL) )""",
            loser_id,
            winner_id,
        )
        await db.execute(
            "UPDATE role_assignment_relationships SET to_assignment_id=$2"
            " WHERE to_assignment_id=$1 AND archived_at IS NULL",
            loser_id,
            winner_id,
        )


# ---------------------------------------------------------------------------
# Role-level ancillary (#326)
#
# A role *definition* carries only two of the polymorphic ancillary surfaces —
# ``contact_methods`` and ``links`` keyed on ``(entity_type='role', entity_id)``.
# (identifiers / field_confidence / import_provenance exclude 'role'.) The admin
# contacts/links editors (#326) make these rows routinely populated, so the three
# role-deleting paths (role hard-delete, role merge, org-merge role-pair) must
# clean them up or they orphan exactly like the assignment case — same no-FK model.
# Merges re-home (dedup) onto the surviving role; hard-delete removes them.
# ---------------------------------------------------------------------------

_ROLE_SPECS: tuple[_AncillarySpec, ...] = (
    _AncillarySpec(
        name="links",
        select_sql=(
            "SELECT id, url, link_type_id FROM links WHERE entity_type='role' AND entity_id=$1"
        ),
        exists_sql=(
            "SELECT 1 FROM links WHERE entity_type='role'"
            " AND entity_id=$1 AND url=$2 AND link_type_id=$3"
        ),
        key_fields=("url", "link_type_id"),
    ),
    _AncillarySpec(
        name="contact_methods",
        select_sql=(
            "SELECT id, contact_type, value FROM contact_methods"
            " WHERE entity_type='role' AND entity_id=$1"
        ),
        exists_sql=(
            "SELECT 1 FROM contact_methods WHERE entity_type='role'"
            " AND entity_id=$1 AND contact_type=$2 AND value=$3"
        ),
        key_fields=("contact_type", "value"),
    ),
)


async def count_orphaned_role_ancillary(db: asyncpg.Connection) -> dict[str, int]:
    """Count contacts/links pointing at a role id that no longer exists.

    The role-level polymorphic ancillary has no FK, so a merge or direct DELETE
    that drops a role can strand these rows undetected — the role analogue of
    :func:`count_orphaned_role_assignment_ancillary`. Returns ``{table: n}`` for
    each role spec; the daily guard (#326) warns when any count is non-zero.
    """
    counts: dict[str, int] = {}
    for spec in _ROLE_SPECS:
        counts[spec.name] = await db.fetchval(
            f"SELECT count(*) FROM {spec.name} x"
            " WHERE x.entity_type='role'"
            " AND NOT EXISTS (SELECT 1 FROM roles r WHERE r.id = x.entity_id)"
        )
    return counts


async def rehome_role_ancillary(
    db: asyncpg.Connection, loser_id: str, winner_id: str
) -> dict[str, tuple[int, int]]:
    """Re-point a loser role's contacts/links onto the winner before a merge delete.

    Mirrors :func:`rehome_conflicting_assignment_ancillary` for the role case: each
    row is re-pointed onto ``winner_id`` or deleted when the winner already carries
    an identical one. Both role specs (``links`` / ``contact_methods``) carry touch
    triggers (#327), so a re-point self-emits the survivor 'role' 'updated' signal
    (one per moved row) — no manual emit here. Returns per-table ``(moved, deduped)``.
    Role citations (#319) re-home alongside (NULL-safe, self-emitting trigger).
    """
    result = await _migrate_specs(db, _ROLE_SPECS, loser_id, winner_id)
    result["citations"] = await migrate_citations(db, "role", loser_id, winner_id)
    return result


# ---------------------------------------------------------------------------
# Citations (#319)
#
# Citations are polymorphic no-FK ancillary spanning **all seven** citable entity
# types (org / person / role / role_assignment / jurisdiction / person_name /
# entity_event), so a merge that collapses any of them can strand a citation. The
# identity that dedups a citation within an entity is (field_name, url) with
# NULLS NOT DISTINCT (mirrors uq_citation_identity over active rows) — so the
# generic _AncillarySpec machinery (exact ``=`` on non-null keys) can't serve it;
# the migrator below is NULL-safe. Citations carry a touch-cascade trigger, so a
# re-point self-emits the survivor 'updated' signal — no manual emit here.
# ---------------------------------------------------------------------------


async def migrate_citations(
    db: asyncpg.Connection, entity_type: str, from_id: str, to_id: str
) -> tuple[int, int]:
    """Re-point one entity's citations onto another; dedup active identity twins.

    Returns ``(moved, deduped)``. A loser citation that is **active** and whose
    ``(field_name, url)`` already exists **active** on the survivor is deleted
    (would otherwise violate ``uq_citation_identity``); everything else re-points.
    Archived rows always re-point — they're outside the active unique index, so no
    collision is possible. Both DELETE and UPDATE fire the citation touch trigger,
    so the survivor's 'updated' signal is emitted automatically.
    """
    moved = deduped = 0
    rows = await db.fetch(
        "SELECT id, field_name, url, archived_at FROM citations"
        " WHERE entity_type=$1 AND entity_id=$2",
        entity_type,
        from_id,
    )
    for row in rows:
        if row["archived_at"] is None:
            twin = await db.fetchval(
                "SELECT 1 FROM citations WHERE entity_type=$1 AND entity_id=$2"
                " AND field_name IS NOT DISTINCT FROM $3 AND url IS NOT DISTINCT FROM $4"
                " AND archived_at IS NULL",
                entity_type,
                to_id,
                row["field_name"],
                row["url"],
            )
            if twin:
                await db.execute("DELETE FROM citations WHERE id=$1", row["id"])
                deduped += 1
                continue
        await db.execute("UPDATE citations SET entity_id=$2 WHERE id=$1", row["id"], to_id)
        moved += 1
    return moved, deduped


async def rehome_citations(
    db: asyncpg.Connection, entity_type: str, pairs: list[tuple[str, str]]
) -> tuple[int, int]:
    """Migrate citations for every ``(loser, winner)`` pair of one entity type.

    Call immediately before a merge hard-deletes the losers. Returns aggregate
    ``(moved, deduped)``. The touch trigger self-emits every survivor signal.
    """
    moved = deduped = 0
    for loser_id, winner_id in pairs:
        m, d = await migrate_citations(db, entity_type, loser_id, winner_id)
        moved += m
        deduped += d
    return moved, deduped


async def delete_citations(db: asyncpg.Connection, entity_type: str, entity_id: str) -> None:
    """Hard-delete a citable entity's own citations before the entity row is removed.

    For a sub-entity with no survivor to re-home onto (a curated person_name drop, an
    admin event hard-delete): the assertion the citation supported no longer exists.
    """
    await db.execute(
        "DELETE FROM citations WHERE entity_type=$1 AND entity_id=$2", entity_type, entity_id
    )


async def delete_event_citations_for_owner(
    db: asyncpg.Connection, owner_type: str, owner_id: str
) -> None:
    """Delete citations on ``entity_event`` rows owned by a parent about to be removed.

    For a hard delete (:func:`delete_entity_ancillary`), which drops the parent's
    events with it. A merge re-homes the events instead, citations and all
    (:func:`rehome_entity_events`, #611).
    """
    await db.execute(
        "DELETE FROM citations WHERE entity_type='entity_event' AND entity_id IN"
        " (SELECT id FROM entity_events WHERE entity_type=$1 AND entity_id=$2)",
        owner_type,
        owner_id,
    )


# ---------------------------------------------------------------------------
# Entity events (#611)
#
# `entity_events` names a person or org two ways, neither with an FK: as the
# event's owner (`entity_type`, `entity_id`) and as its link target
# (`linked_entity_type`, `linked_entity_id`, e.g. a predecessor's `succeeded_by`).
# A merge hard-deletes the loser, so it re-points both onto the survivor. The
# touch trigger bumps the owner and both the old and new linked org on every
# re-point, so no manual signal is needed.
# ---------------------------------------------------------------------------

#: The entity types `entity_events` can own or link (its two CHECKs).
EVENT_ENTITY_TYPES = frozenset({"person", "organization"})

# Every event naming the loser, as owner or as link target. Oldest first, so
# that when two loser-side rows collapse, the earlier one stands.
_LOSER_EVENTS_SQL = (
    "SELECT id, entity_type, entity_id, event_type_id, linked_entity_type, linked_entity_id,"
    "       event_year, event_month, event_day, event_hour, event_minute, event_second,"
    "       archived_at, created_at"
    "  FROM entity_events"
    " WHERE (entity_type = $1 AND entity_id = $2)"
    "    OR (linked_entity_type = $1 AND linked_entity_id = $2)"
    " ORDER BY created_at, id"
    " FOR UPDATE"
)
# Observation's create-path content-dedup key (`_create_event`), archived rows
# included: a re-point must not leave two rows observation could attach to.
_EVENT_TWIN_SQL = (
    "SELECT id FROM entity_events"
    " WHERE entity_type = $1 AND entity_id = $2 AND event_type_id = $3"
    "   AND event_year IS NOT DISTINCT FROM $4 AND event_month IS NOT DISTINCT FROM $5"
    "   AND event_day IS NOT DISTINCT FROM $6 AND event_hour IS NOT DISTINCT FROM $7"
    "   AND event_minute IS NOT DISTINCT FROM $8 AND event_second IS NOT DISTINCT FROM $9"
    "   AND linked_entity_id IS NOT DISTINCT FROM $10 AND id <> $11"
    " ORDER BY created_at, id LIMIT 1"
)
# The active edge `uq_entity_events_succession_edge` would hold against the row.
_ACTIVE_SUCCESSION_EDGE_SQL = (
    "SELECT ev.id, ev.created_at FROM entity_events ev"
    " JOIN entity_event_types t ON t.id = ev.event_type_id AND t.slug = 'succeeded_by'"
    " WHERE ev.entity_id = $1 AND ev.linked_entity_id = $2 AND ev.archived_at IS NULL"
    "   AND ev.id <> $3"
)
_IS_SUCCESSION_SQL = "SELECT slug = 'succeeded_by' FROM entity_event_types WHERE id = $1"


async def _rehome_one_event(
    db: asyncpg.Connection, row: asyncpg.Record, entity_type: str, loser_id: str, winner_id: str
) -> int | None:
    """Re-point one event naming ``loser_id``; return how many rows it archived.

    ``None`` means the event was a content twin and is gone.
    """
    owner = row["entity_id"]
    if row["entity_type"] == entity_type and owner == loser_id:
        owner = winner_id
    linked = row["linked_entity_id"]
    if row["linked_entity_type"] == entity_type and linked == loser_id:
        linked = winner_id

    twin = await db.fetchval(
        _EVENT_TWIN_SQL,
        row["entity_type"],
        owner,
        row["event_type_id"],
        row["event_year"],
        row["event_month"],
        row["event_day"],
        row["event_hour"],
        row["event_minute"],
        row["event_second"],
        linked,
        row["id"],
    )
    if twin:
        await migrate_citations(db, "entity_event", row["id"], twin)
        await db.execute("DELETE FROM entity_events WHERE id=$1", row["id"])
        return None

    archive = False
    archived = 0
    if row["archived_at"] is None:
        if row["linked_entity_type"] == row["entity_type"] and linked == owner:
            archive = True  # a self-link says nothing; keep it as history only
        elif await db.fetchval(_IS_SUCCESSION_SQL, row["event_type_id"]):
            edge = await db.fetchrow(_ACTIVE_SUCCESSION_EDGE_SQL, owner, linked, row["id"])
            # One active edge per pair: the later of the two goes, the index's own
            # reconciliation rule. It goes first, as the index is checked per row.
            if edge and (edge["created_at"], edge["id"]) < (row["created_at"], row["id"]):
                archive = True
            elif edge:
                await db.execute(
                    "UPDATE entity_events SET archived_at = NOW() WHERE id=$1", edge["id"]
                )
                archived += 1
    await db.execute(
        "UPDATE entity_events SET entity_id=$2, linked_entity_id=$3,"
        " archived_at = CASE WHEN $4 THEN NOW() ELSE archived_at END WHERE id=$1",
        row["id"],
        owner,
        linked,
        archive,
    )
    return archived + int(archive)


async def rehome_entity_events(
    db: asyncpg.Connection, entity_type: str, pairs: list[tuple[str, str]]
) -> tuple[int, int, int]:
    """Re-point every event naming each loser onto its survivor.

    Call before a person or org merge hard-deletes its losers. Both kinds of
    reference move: the loser's own events (with their citations and ULIDs, so
    a producer's ``pm_event_id`` still resolves) and other events' links to it.
    Archived rows move too, or they would name a deleted id. A re-point can
    collide three ways:

    - **content twin** — the re-pointed row matches another event on the same
      owner by observation's content-dedup key. The row already naming the
      survivor stands, archived or not (a retraction there is authoritative);
      the moving row's citations join it and the moving row is deleted.
    - **self-link** — the event would link its owner to itself; it is
      re-pointed and archived.
    - **succession edge** — two active ``succeeded_by`` edges on one pair
      (``uq_entity_events_succession_edge``); the later by ``(created_at, id)``
      is archived, whichever side it came from.

    Returns ``(moved, deduped, archived)``: ``moved`` counts the rows
    re-pointed, ``deduped`` the twins deleted, and ``archived`` the active rows
    the merge archived, re-pointed or standing — a re-pointed row it archives
    counts in both.
    """
    if entity_type not in EVENT_ENTITY_TYPES:
        raise ValueError(
            f"not an entity_events entity type: {entity_type!r}"
            f" (one of {', '.join(sorted(EVENT_ENTITY_TYPES))})"
        )
    moved = deduped = archived = 0
    for loser_id, winner_id in pairs:
        for row in await db.fetch(_LOSER_EVENTS_SQL, entity_type, loser_id):
            n_archived = await _rehome_one_event(db, row, entity_type, loser_id, winner_id)
            if n_archived is None:
                deduped += 1
            else:
                moved += 1
                archived += n_archived
    return moved, deduped, archived


async def count_orphaned_citations(db: asyncpg.Connection) -> dict[str, int]:
    """Count citations pointing at an entity id that no longer exists, per type.

    Covers all seven citable entity types (#319). The daily guard warns when any
    count is non-zero; keys are namespaced ``citation.<entity_type>`` by the audit.
    """
    counts: dict[str, int] = {}
    for entity_type, table in CITABLE_ENTITY_TABLES.items():
        counts[entity_type] = await db.fetchval(
            "SELECT count(*) FROM citations c"
            " WHERE c.entity_type=$1"
            f" AND NOT EXISTS (SELECT 1 FROM {table} e WHERE e.id = c.entity_id)",
            entity_type,
        )
    return counts


# ---------------------------------------------------------------------------
# Curation overlay (#514)
#
# `curation_overlay` holds a curator's PM-wins overrides on producer-owned fields
# (#497/#498), keyed on (entity_type, entity_id) with no FK — polymorphic like
# every table above. The mapping layer joins it on the *surviving* pm_id, so an
# override left on a merged-away id stops applying without a word. Every merge
# step that mirrors a subscription re-homes these rows beside it
# (`tests/api/admin/test_merge_identity_sweep.py` holds the paths to that).
# ---------------------------------------------------------------------------

#: The overlay's `entity_type` vocabulary — the producer crosswalk's `kind`, so an
#: assignment is `assignment` here where `deleted_entities` says `role_assignment`.
OVERLAY_ENTITY_TYPES = frozenset({"person", "organization", "role", "assignment"})

# Pins whose *value* is an id of the merging type (#498): organization.parent_id
# names an org. The merge re-points the live column (`UPDATE organizations SET
# parent_id` in orgs_merge), so the pin follows it, history included — a pin left
# naming the deleted loser would have the applier write it back.
_ID_VALUED_FIELDS: dict[str, tuple[str, ...]] = {"organization": ("parent_id",)}
_REPOINT_VALUES_SQL = (
    "UPDATE curation_overlay SET value = $3"
    " WHERE entity_type = $1 AND field = ANY($4::text[]) AND value = $2"
)
# Re-pointed, the survivor's pin naming the loser now names the survivor; moved,
# the loser's pin naming the survivor would too. No org is its own parent, so
# either is displaced, as an unpin is — before the clash check, so a sound pin on
# the other side is free to stand.
_ARCHIVE_SELF_NAMING_SQL = (
    "UPDATE curation_overlay SET archived_at = NOW()"
    " WHERE entity_type = $1 AND field = ANY($4::text[]) AND archived_at IS NULL"
    "   AND entity_id IN ($2, $3) AND value = $3"
    " RETURNING id"
)
# A loser's *active* pin on a field the survivor holds live is displaced:
# archived, as an unpin is (#498), then carried across below as history. Only an
# active pin holds a field, so a survivor's archived pin clashes with nothing.
# A merge displaces, no curator unpins, so `archived_by` stays NULL here.
_ARCHIVE_CLASHING_OVERRIDES_SQL = (
    "UPDATE curation_overlay l SET archived_at = NOW()"
    " WHERE l.entity_type = $1 AND l.entity_id = $2 AND l.archived_at IS NULL"
    "   AND EXISTS (SELECT 1 FROM curation_overlay w"
    "                WHERE w.entity_type = $1 AND w.entity_id = $3 AND w.field = l.field"
    "                  AND w.archived_at IS NULL)"
    " RETURNING l.id"
)
# Then everything the loser holds moves — live pins and archived history alike.
_MOVE_OVERRIDES_SQL = (
    "UPDATE curation_overlay SET entity_id = $3 WHERE entity_type = $1 AND entity_id = $2"
    " RETURNING archived_at IS NULL AS active"
)


async def rehome_curation_overlay(
    db: asyncpg.Connection, entity_type: str, pairs: list[tuple[str, str]]
) -> tuple[int, int]:
    """Move each loser's curator overrides onto its survivor; return ``(moved, archived)``.

    ``pairs`` is ``[(loser_id, winner_id), ...]``, the shape every merge path
    already holds. One *active* override per (entity, field) is the table's own
    rule, so on a clash the **survivor's** override stands — it is the decision
    made on the record that continues — and the loser's is archived, as an unpin
    is (#498), and carried across as history rather than deleted. ``moved``
    counts the live pins that arrive live; ``archived`` the ones displaced. Pairs
    run one at a time, so two losers folding into one survivor cannot both claim
    a field.

    A pin whose value is an id of the merging type (``organization.parent_id``)
    is re-pointed too, loser to survivor, as the merge re-points the live column;
    one that would then name its own entity is displaced.
    """
    if entity_type not in OVERLAY_ENTITY_TYPES:
        raise ValueError(
            f"not a curation_overlay entity type: {entity_type!r}"
            f" (one of {', '.join(sorted(OVERLAY_ENTITY_TYPES))})"
        )
    id_fields = list(_ID_VALUED_FIELDS.get(entity_type, ()))
    moved = archived = 0
    for loser_id, winner_id in pairs:
        if id_fields:
            args = (entity_type, loser_id, winner_id, id_fields)
            await db.execute(_REPOINT_VALUES_SQL, *args)
            archived += len(await db.fetch(_ARCHIVE_SELF_NAMING_SQL, *args))
        archived += len(
            await db.fetch(_ARCHIVE_CLASHING_OVERRIDES_SQL, entity_type, loser_id, winner_id)
        )
        rows = await db.fetch(_MOVE_OVERRIDES_SQL, entity_type, loser_id, winner_id)
        moved += sum(1 for r in rows if r["active"])
    return moved, archived


# ---------------------------------------------------------------------------
# Hard delete (#605)
#
# An admin hard delete has no survivor, so an entity's polymorphic rows go with
# it. Left behind, an identifier names a deleted id: resolve_entity rejects the
# producer's next observation of it as `<type>_archived` (#481), for good,
# instead of minting a new entity.
# ---------------------------------------------------------------------------

#: Polymorphic tables keyed on (entity_type, entity_id) that an entity owns
#: outright. A type a table's CHECK does not admit simply matches no row.
_OWNED_TABLES = ("links", "contact_methods", "citations", "field_confidence", "import_provenance")

#: The types an admin hard delete removes, spelt as `deleted_entities` spells them.
HARD_DELETABLE_TYPES = frozenset(
    {"person", "organization", "jurisdiction", "role", "role_assignment"}
)


async def delete_entity_ancillary(db: asyncpg.Connection, entity_type: str, entity_id: str) -> None:
    """Drop every polymorphic row an entity owns, before its hard ``DELETE``.

    Call inside the delete's transaction, ahead of the entity row and its FK
    children (a person's names carry citations of their own). The route's
    'deleted' tombstone announces the removal, so no per-table signal is
    needed. Outliving the entity on purpose: ``deleted_entities`` and
    ``entity_changes`` (the tombstone and its outbox) and
    ``api_key_entity_subscriptions`` (the change feed joins it to deliver
    that tombstone). Another entity's event linking here is not this
    entity's to drop: the route refuses the delete first, via
    ``inbound_link_conflict`` (#608).

    Raises ``ValueError`` on a type outside :data:`HARD_DELETABLE_TYPES`: a
    misspelt one would match no row and strand them all without a word.
    """
    if entity_type not in HARD_DELETABLE_TYPES:
        raise ValueError(
            f"not a hard-deletable entity type: {entity_type!r}"
            f" (one of {', '.join(sorted(HARD_DELETABLE_TYPES))})"
        )
    if entity_type == "person":
        await db.execute(
            "DELETE FROM citations WHERE entity_type='person_name' AND entity_id IN"
            " (SELECT id FROM person_names WHERE person_id=$1)",
            entity_id,
        )
    await delete_event_citations_for_owner(db, entity_type, entity_id)
    await db.execute(
        "DELETE FROM entity_events WHERE entity_type=$1 AND entity_id=$2", entity_type, entity_id
    )
    for table in _OWNED_TABLES:
        await db.execute(
            f"DELETE FROM {table} WHERE entity_type=$1 AND entity_id=$2", entity_type, entity_id
        )
    await db.execute(
        "DELETE FROM identifiers i USING entity_identifier_types t"
        " WHERE t.id = i.entity_identifier_type_id AND t.entity_type=$1 AND i.entity_id=$2",
        entity_type,
        entity_id,
    )
    # The address goes with its link, as the admin address delete does — unless
    # another entity's link or an event's place still uses it.
    address_ids = [
        r["address_id"]
        for r in await db.fetch(
            "DELETE FROM entity_addresses WHERE entity_type=$1 AND entity_id=$2"
            " RETURNING address_id",
            entity_type,
            entity_id,
        )
    ]
    await db.execute(
        "DELETE FROM addresses a WHERE a.id = ANY($1::text[])"
        " AND NOT EXISTS (SELECT 1 FROM entity_addresses ea WHERE ea.address_id = a.id)"
        " AND NOT EXISTS (SELECT 1 FROM entity_events ev WHERE ev.event_place_address_id = a.id)",
        address_ids,
    )
    overlay_type = "assignment" if entity_type == "role_assignment" else entity_type
    await db.execute(
        "DELETE FROM curation_overlay WHERE entity_type=$1 AND entity_id=$2",
        overlay_type,
        entity_id,
    )
