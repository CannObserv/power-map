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

from collections.abc import AsyncIterator, Iterable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field

import asyncpg

from src.core.curation_overlay import in_scope, pin_changed

__all__ = [
    "PRODUCER_LABEL",
    "SLOTS",
    "Slot",
    "TrackedEdit",
    "flash_key",
    "pinned_note",
    "read_slots",
    "slots_for",
    "tracked",
]

# The producer a pin wins over, as a curator reads it. The admin layer names it;
# `src/core` never does (`test_src_core_wa_free.py`). One producer today (#490);
# key it by crosswalk `source` when a second arrives.
PRODUCER_LABEL = "usa-wa"


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


def slots_for(entity_type: str, fields: Iterable[str] | None = None) -> list[Slot]:
    """The pinnable slots of one entity type, in registry order — ``fields`` narrows."""
    wanted = None if fields is None else set(fields)
    return [
        s for s in _SLOTS if s.entity_type == entity_type and (wanted is None or s.field in wanted)
    ]


async def read_slots(
    conn: asyncpg.Connection,
    entity_type: str,
    entity_id: str,
    fields: Iterable[str] | None = None,
) -> dict[str, object]:
    """The slots' live values for one entity, keyed by field."""
    return {s.field: await conn.fetchval(s.sql, entity_id) for s in slots_for(entity_type, fields)}


def pinned_note(pinned: list[str]) -> str:
    """The sentence an HTMX success flash gains when the edit pinned (static text)."""
    return f" Pinned: PM keeps it over {PRODUCER_LABEL}'s value." if pinned else ""


def flash_key(base: str, pinned: list[str]) -> str:
    """The non-HTMX fallback's flash key: ``saved`` → ``saved_pinned`` when it pinned."""
    return f"{base}_pinned" if pinned else base


@dataclass
class TrackedEdit:
    """What a tracked edit pinned — the fields, for the route's flash."""

    pinned: list[str] = field(default_factory=list)


@asynccontextmanager
async def tracked(
    conn: asyncpg.Connection,
    entity_type: str,
    entity_id: str,
    *,
    user_id: str,
    fields: Iterable[str] | None = None,
) -> AsyncIterator[TrackedEdit]:
    """Wrap an admin write: read the slots, run it, read again, pin what moved.

    Use inside the route's transaction, so the pins land with the edit or not at
    all; an edit that raises never reaches the second read. An entity outside
    the producer's row scope is direct curation — nothing is read or pinned.
    ``user_id`` must be an ``app_users`` row (``provision_app_user``). ``fields``
    narrows to the slots the write can move — an event edit reads only the
    dissolved year, and a person's events read nothing.
    """
    edit = TrackedEdit()
    fields = None if fields is None else tuple(fields)
    if not slots_for(entity_type, fields) or not await in_scope(conn, entity_type, entity_id):
        yield edit
        return
    before = await read_slots(conn, entity_type, entity_id, fields)
    yield edit
    after = await read_slots(conn, entity_type, entity_id, fields)
    edit.pinned = await pin_changed(
        conn, entity_type, entity_id, before=before, after=after, user_id=user_id
    )
