"""The curation overlay's write side (#498): pin, unpin, and row scope.

A pin is a curator's decision that PM's value for a producer-owned field wins
over every later snapshot. The mapping models join active pins as
``COALESCE(overlay, mapped)`` (docs/SCHEMA.md § Curation overlay), so PM state
for that slice is f(snapshot, overlay) and the applier needs no per-row gate.

Keyed ``(entity_type, entity_id, field)``; only an *active* row holds a field
(a partial unique index backs it). Unpin archives, and a changed value archives
the old pin before inserting the new one, so every decision keeps its author
and its time. A pin exists only for an entity in the producer's row scope — a
live or merged ``producer_crosswalk`` row — because the models apply nothing
else, and an inert override is the one outcome the design rules out.

Producer-free by construction: *which* fields are pinnable is the admin's slot
registry (``src/api/admin/overlay_slots.py``), held to the ownership manifest
by a structural test. This module knows the table and the scope, nothing more.
"""

from dataclasses import dataclass
from datetime import datetime

import asyncpg

from src.core.db import generate_id
from src.core.ingestion.crosswalk import IN_SCOPE
from src.core.logging import get_logger

__all__ = [
    "OverlayError",
    "Pin",
    "active_pin",
    "active_pins",
    "in_scope",
    "pin",
    "pin_changed",
    "unpin",
    "unpin_pin",
]

logger = get_logger(__name__)

_COLUMNS = "id, entity_type, entity_id, field, value, note, created_by, created_at, archived_at"
_SCOPE_SQL = (
    "SELECT EXISTS (SELECT 1 FROM producer_crosswalk"
    " WHERE kind = $1 AND pm_id = $2 AND resolution = ANY($3::text[]))"
)
_ACTIVE_SQL = (
    f"SELECT {_COLUMNS} FROM curation_overlay"
    " WHERE entity_type = $1 AND entity_id = $2 AND archived_at IS NULL"
)
_INSERT_SQL = (
    "INSERT INTO curation_overlay (id, entity_type, entity_id, field, value, note, created_by)"
    f" VALUES ($1, $2, $3, $4, $5, $6, $7) RETURNING {_COLUMNS}"
)
_ARCHIVE_SQL = "UPDATE curation_overlay SET archived_at = NOW() WHERE id = $1"
_UNPIN_ID_SQL = (
    "UPDATE curation_overlay SET archived_at = NOW() WHERE id = $1 AND archived_at IS NULL"
    f" RETURNING {_COLUMNS}"
)
_UNPIN_SQL = (
    "UPDATE curation_overlay SET archived_at = NOW()"
    " WHERE entity_type = $1 AND entity_id = $2 AND field = $3 AND archived_at IS NULL"
    " RETURNING id"
)


class OverlayError(ValueError):
    """A pin the overlay cannot hold; the message says why."""


@dataclass(frozen=True)
class Pin:
    """One `curation_overlay` row."""

    id: str
    entity_type: str
    entity_id: str
    field: str
    value: str | None
    note: str | None
    created_by: str | None
    created_at: datetime
    archived_at: datetime | None


def _pin(row: asyncpg.Record) -> Pin:
    return Pin(**dict(row))


def _text(value: object) -> str | None:
    """`curation_overlay.value` is TEXT: a year arrives as 2018 and is held as '2018'."""
    return None if value is None else str(value)


async def in_scope(conn: asyncpg.Connection, entity_type: str, entity_id: str) -> bool:
    """Whether the entity is in a producer's row scope — the only rows a pin reaches."""
    return await conn.fetchval(_SCOPE_SQL, entity_type, entity_id, list(IN_SCOPE))


async def active_pins(conn: asyncpg.Connection, entity_type: str, entity_id: str) -> list[Pin]:
    """The entity's live pins, by field."""
    rows = await conn.fetch(_ACTIVE_SQL + " ORDER BY field", entity_type, entity_id)
    return [_pin(r) for r in rows]


async def active_pin(
    conn: asyncpg.Connection, entity_type: str, entity_id: str, field: str
) -> Pin | None:
    """The live pin on one field, or None."""
    row = await conn.fetchrow(_ACTIVE_SQL + " AND field = $3", entity_type, entity_id, field)
    return _pin(row) if row is not None else None


async def pin(
    conn: asyncpg.Connection,
    entity_type: str,
    entity_id: str,
    field: str,
    value: object,
    *,
    user_id: str,
    note: str | None = None,
) -> Pin:
    """Hold ``value`` for the field over every later snapshot; return the live pin.

    The same value again is a no-op. A different value archives the live pin and
    inserts a fresh one. ``value`` None asserts the field should be empty.
    ``user_id`` must be an ``app_users`` row (the admin's ``provision_app_user``).
    Run inside the caller's transaction, so the pin lands with the edit or not at all.
    """
    if not await in_scope(conn, entity_type, entity_id):
        raise OverlayError(
            f"{entity_type} {entity_id} is outside the producer's row scope;"
            " a pin there would be applied nowhere"
        )
    text = _text(value)
    current = await active_pin(conn, entity_type, entity_id, field)
    if current is not None and current.value == text:
        return current
    if current is not None:
        await conn.execute(_ARCHIVE_SQL, current.id)
    row = await conn.fetchrow(
        _INSERT_SQL, generate_id(), entity_type, entity_id, field, text, note, user_id
    )
    logger.info("pinned %s.%s on %s by %s", entity_type, field, entity_id, user_id)
    return _pin(row)


async def unpin(
    conn: asyncpg.Connection, entity_type: str, entity_id: str, field: str, *, user_id: str
) -> bool:
    """Archive the live pin, letting the producer's value return on the next apply.

    Returns whether there was one. The row stays as history.
    """
    row = await conn.fetchrow(_UNPIN_SQL, entity_type, entity_id, field)
    if row is not None:
        logger.info("unpinned %s.%s on %s by %s", entity_type, field, entity_id, user_id)
    return row is not None


async def unpin_pin(conn: asyncpg.Connection, pin_id: str, *, user_id: str) -> Pin | None:
    """Archive one pin by id — only while it is still the live one.

    A list row can be older than its page: the pin it shows may have been
    replaced since, and unpinning by field would archive the newer pin instead.
    Returns the archived pin, or None when that row was no longer live.
    """
    row = await conn.fetchrow(_UNPIN_ID_SQL, pin_id)
    if row is None:
        return None
    held = _pin(row)
    logger.info("unpinned %s.%s on %s by %s", held.entity_type, held.field, held.entity_id, user_id)
    return held


async def pin_changed(
    conn: asyncpg.Connection,
    entity_type: str,
    entity_id: str,
    *,
    before: dict[str, object],
    after: dict[str, object],
    user_id: str,
) -> list[str]:
    """The admin's edit hook: pin every field whose value the edit moved.

    ``before`` and ``after`` are the slots' values read either side of the edit,
    in its transaction. A field left unchanged pins nothing (a locale edit is not
    a claim about the value); an entity outside the row scope is direct curation
    and pins nothing. Returns the fields pinned, for the flash.
    """
    fields = list(dict.fromkeys([*before, *after]))
    changed = [f for f in fields if before.get(f) != after.get(f)]
    if not changed or not await in_scope(conn, entity_type, entity_id):
        return []
    for f in changed:
        await pin(conn, entity_type, entity_id, f, after.get(f), user_id=user_id)
    return changed
