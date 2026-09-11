"""The pinnable slots (#498): the producer-owned fields a curator can pin, and how
each one's live value is read.

A slot is an overlay pair ``(entity_type, field)`` — the manifest's
``(entity, overlay)`` — bound to the SQL that reads its value from the live
database. The edit hook, :func:`tracked`, reads an entity's slots either side of
an admin write, inside that write's transaction, and pins whatever the write
moved (:func:`src.core.curation_overlay.pin_changed`).

This registry, ``manifest.yml``'s ``overlay:`` keys and the dbt vocabulary test
(``overlay_field_unmapped.sql``) name the same five pairs, and
``tests/core/ingestion/mapping/test_overlay.py`` fails when they drift. The admin
cannot read the manifest itself: the mapping package imports dbt, which the
service's environment does not install.

A multi-row slot (names, acronyms) reads the canonical row, else the earliest.
Either way the value is one PM holds, so a pin of it is a noop for the applier,
never a re-insert. ``person_names`` is read directly (allow-listed in
``tests/core/test_visible_names_filter.py``): a slot mirrors the applier, which
matches every legal row whatever its visibility, and curators see every name.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field

import asyncpg

from src.core.curation_overlay import in_scope, pin_changed

__all__ = ["SLOTS", "Slot", "TrackedEdit", "read_slots", "slots_for", "tracked"]


@dataclass(frozen=True)
class Slot:
    """One pinnable field: its overlay pair, a label, and the SQL reading its value.

    ``sql`` takes the entity id as ``$1`` and yields at most one value; no row
    reads as None — the slot is empty.
    """

    entity_type: str
    field: str
    label: str
    sql: str


_SLOTS = (
    Slot(
        "person",
        "name",
        "Legal name",
        "SELECT name FROM person_names WHERE person_id = $1 AND name_type = 'legal'"
        " ORDER BY is_canonical DESC, created_at, id LIMIT 1",
    ),
    Slot(
        "organization",
        "legal_name",
        "Legal name",
        "SELECT name FROM organization_names WHERE organization_id = $1 AND name_type = 'legal'"
        " ORDER BY is_canonical DESC, created_at, id LIMIT 1",
    ),
    Slot(
        "organization",
        "acronym",
        "Acronym",
        "SELECT acronym FROM organization_acronyms WHERE organization_id = $1"
        " ORDER BY is_canonical DESC, created_at, id LIMIT 1",
    ),
    Slot(
        "organization",
        "parent_id",
        "Parent organization",
        "SELECT parent_id FROM organizations WHERE id = $1",
    ),
    Slot(
        "organization",
        "dissolved_year",
        "Dissolved year",
        "SELECT e.event_year FROM entity_events e"
        " JOIN entity_event_types t ON t.id = e.event_type_id"
        " WHERE e.entity_type = 'organization' AND e.entity_id = $1"
        "   AND t.slug = 'dissolved' AND e.archived_at IS NULL"
        " ORDER BY e.created_at, e.id LIMIT 1",
    ),
)

SLOTS: dict[tuple[str, str], Slot] = {(s.entity_type, s.field): s for s in _SLOTS}


def slots_for(entity_type: str) -> list[Slot]:
    """The pinnable slots of one entity type, in registry order."""
    return [s for s in _SLOTS if s.entity_type == entity_type]


async def read_slots(
    conn: asyncpg.Connection, entity_type: str, entity_id: str
) -> dict[str, object]:
    """Every slot's live value for one entity, keyed by field."""
    return {s.field: await conn.fetchval(s.sql, entity_id) for s in slots_for(entity_type)}


@dataclass
class TrackedEdit:
    """What a tracked edit pinned — the fields, for the route's flash."""

    pinned: list[str] = field(default_factory=list)


@asynccontextmanager
async def tracked(
    conn: asyncpg.Connection, entity_type: str, entity_id: str, *, user_id: str
) -> AsyncIterator[TrackedEdit]:
    """Wrap an admin write: read the slots, run it, read again, pin what moved.

    Use inside the route's transaction, so the pins land with the edit or not at
    all; an edit that raises never reaches the second read. An entity outside
    the producer's row scope is direct curation — nothing is read or pinned.
    ``user_id`` must be an ``app_users`` row (``provision_app_user``).
    """
    edit = TrackedEdit()
    if not slots_for(entity_type) or not await in_scope(conn, entity_type, entity_id):
        yield edit
        return
    before = await read_slots(conn, entity_type, entity_id)
    yield edit
    after = await read_slots(conn, entity_type, entity_id)
    edit.pinned = await pin_changed(
        conn, entity_type, entity_id, before=before, after=after, user_id=user_id
    )
