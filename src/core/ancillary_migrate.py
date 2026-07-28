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
is append-only and always re-points (never dedups). A role hard-delete instead
drops the rows outright (:func:`delete_role_ancillary`), relying on the role's own
'deleted' tombstone.

**Survivor signal (#327).** ``links`` / ``contact_methods`` / ``identifiers`` now
carry touch-cascade triggers, so a re-point ``UPDATE`` self-emits an
``entity_changes`` 'updated' for the survivor (one per moved row). Only the
trigger-less telemetry tables — ``field_confidence`` and ``import_provenance``
(written per-ingestion, deliberately not triggered) — still need a manual emit,
gated on a move of one of those (:data:`TRIGGERLESS_ANCILLARY_TABLES`). ``rehome_role_ancillary``
touches only triggered tables, so it emits nothing manually at all.
"""

from collections import defaultdict
from typing import NamedTuple

import asyncpg

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
    """
    return await _migrate_specs(db, _ROLE_SPECS, loser_id, winner_id)


async def delete_role_ancillary(db: asyncpg.Connection, role_id: str) -> None:
    """Hard-delete a role's own contacts/links before the role row is removed.

    The polymorphic rows have no FK, so a bare ``DELETE FROM roles`` would strand
    them. The role-delete path already emits a 'deleted' tombstone for the role, so
    no per-table outbox signal is needed here — subscribers drop the whole role.
    """
    for table in ("links", "contact_methods"):
        await db.execute(f"DELETE FROM {table} WHERE entity_type='role' AND entity_id=$1", role_id)
