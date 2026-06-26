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
