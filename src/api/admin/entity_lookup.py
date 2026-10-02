"""Shared lookups for the polymorphic linked-entity reference (person | organization).

`entity_events.linked_entity_id` is a polymorphic FK with no DB-level constraint,
so existence and type must be checked in the application. These helpers back both
the admin entity-search typeahead and event linked-entity validation (#172).
"""

import asyncpg

from src.api.admin.deps import escape_like

# Supported values for entity_events.linked_entity_type.
ENTITY_TYPES: tuple[str, ...] = ("person", "organization")

_SEARCH_QUERIES: dict[str, str] = {
    "person": """
        SELECT p.id, pn.display_name
        FROM people p
        LEFT JOIN v_person_display_names pn ON pn.person_id = p.id
        WHERE p.archived_at IS NULL
          AND pn.display_name ILIKE $1 ESCAPE '\\'
        ORDER BY pn.sort_key COLLATE "und-x-icu" NULLS LAST
        LIMIT 20
    """,
    "organization": """
        SELECT o.id, dn.display_name
        FROM organizations o
        LEFT JOIN v_org_display_names dn ON dn.organization_id = o.id
        WHERE o.archived_at IS NULL
          AND dn.display_name ILIKE $1 ESCAPE '\\'
        ORDER BY dn.display_name NULLS LAST
        LIMIT 20
    """,
}

_EXISTS_QUERIES: dict[str, str] = {
    "person": "SELECT 1 FROM people WHERE id = $1",
    "organization": "SELECT 1 FROM organizations WHERE id = $1",
}

_LABEL_QUERIES: dict[str, str] = {
    "person": """
        SELECT pn.display_name
        FROM people p
        LEFT JOIN v_person_display_names pn ON pn.person_id = p.id
        WHERE p.id = $1
    """,
    "organization": """
        SELECT dn.display_name
        FROM organizations o
        LEFT JOIN v_org_display_names dn ON dn.organization_id = o.id
        WHERE o.id = $1
    """,
}


async def search_entities(db: asyncpg.Connection, entity_type: str, q: str) -> list[asyncpg.Record]:
    """Typeahead search of people or orgs by display name.

    Returns records with ``id`` and ``display_name``. Empty list when the type is
    unsupported or the query is blank. Archived entities are excluded.
    """
    query = _SEARCH_QUERIES.get(entity_type)
    if query is None or not q.strip():
        return []
    return await db.fetch(query, f"%{escape_like(q.strip())}%")


async def entity_exists(db: asyncpg.Connection, entity_type: str, entity_id: str) -> bool:
    """Return whether an entity of the given type exists.

    Not archived-filtered: a link may legitimately point at an entity that was
    archived after the link was made. Used to validate a linked-entity reference.
    """
    query = _EXISTS_QUERIES.get(entity_type)
    if query is None or not entity_id:
        return False
    return await db.fetchval(query, entity_id) is not None


async def resolve_entity_label(
    db: asyncpg.Connection, entity_type: str, entity_id: str
) -> str | None:
    """Return the display name for an entity, or None if it has none / is unknown.

    Used to prefill the typeahead's visible value on edit. Callers should fall
    back to the raw id when this is None so the field is never silently blank.
    """
    query = _LABEL_QUERIES.get(entity_type)
    if query is None or not entity_id:
        return None
    return await db.fetchval(query, entity_id)


_ENTITY_TABLES: dict[str, str] = {"person": "people", "organization": "organizations"}

#: The constraint name the schema's link trigger raises ``foreign_key_violation`` under.
LINKED_ENTITY_GUARD = "trg_entity_events_linked_entity"


def linked_entity_vanished(exc: asyncpg.ForeignKeyViolationError) -> bool:
    """True when an event write failed because its linked entity is gone (#608).

    A writer validates the link, then writes later; a delete landing in between
    trips the schema trigger. Distinct from the event's real FKs (its place
    address, its type), which keep their own handling.
    """
    return exc.constraint_name == LINKED_ENTITY_GUARD


# Other entities' events naming this one, archived included — unarchiving one
# would restore the link. The entity's own events are not inbound: they go
# with it (#605), even one that links back to itself.
_INBOUND_LINKS_QUERY = """
    SELECT coalesce(pn.display_name, dn.display_name, ev.entity_id) AS label,
           t.display_name AS event_type, count(*) AS n
    FROM entity_events ev
    JOIN entity_event_types t ON t.id = ev.event_type_id
    LEFT JOIN v_person_display_names pn
      ON ev.entity_type = 'person' AND pn.person_id = ev.entity_id
    LEFT JOIN v_org_display_names dn
      ON ev.entity_type = 'organization' AND dn.organization_id = ev.entity_id
    WHERE ev.linked_entity_type = $1 AND ev.linked_entity_id = $2
      AND NOT (ev.entity_type = $1 AND ev.entity_id = $2)
    GROUP BY 1, 2
    ORDER BY 1, 2
"""

#: How many linking (entity, event type) pairs a 409 detail names before summarising.
_INBOUND_LINKS_SHOWN = 5


async def inbound_link_conflict(
    db: asyncpg.Connection, entity_type: str, entity_id: str
) -> str | None:
    """409 detail naming other entities' events that link to this one, or None (#608).

    ``linked_entity_id`` has no FK, so a hard delete must refuse here what a real
    FK reference would refuse. Call inside the delete's transaction, before any
    write: the entity row is locked ``FOR UPDATE`` first, so a concurrent link —
    whose schema trigger holds ``FOR KEY SHARE`` on that row — commits before the
    check runs and is seen by it.
    """
    await db.execute(
        f"SELECT 1 FROM {_ENTITY_TABLES[entity_type]} WHERE id = $1 FOR UPDATE", entity_id
    )
    rows = await db.fetch(_INBOUND_LINKS_QUERY, entity_type, entity_id)
    if not rows:
        return None
    named = [
        f"{r['event_type']} on {r['label']}" + (f" ({r['n']} events)" if r["n"] > 1 else "")
        for r in rows[:_INBOUND_LINKS_SHOWN]
    ]
    if len(rows) > _INBOUND_LINKS_SHOWN:
        named.append(f"and {len(rows) - _INBOUND_LINKS_SHOWN} more")
    return (
        "Cannot delete: other entities' events still link here — "
        + "; ".join(named)
        + ". Archive and delete those events first."
    )
