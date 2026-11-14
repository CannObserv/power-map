"""Identifier reads shared by the role and assignment routes (#808, #809).

Both read sides follow the people/orgs detail shape (``{id, type_id, type_slug,
value}``, internal types omitted), and both list routes take the same optional
``identifier_type`` + ``identifier_value`` filter, so the two read the same way
on the wire.
"""

from typing import Any

#: The entity types the list-route lookup serves (#808, #809).
_LOOKUP_TYPES = frozenset({"role", "role_assignment"})


async def fetch_identifiers(db: Any, entity_type: str, entity_id: str) -> list[dict]:
    """Return an entity's external identifiers, ordered by type slug then value."""
    rows = await db.fetch(
        """
        SELECT i.id, i.entity_identifier_type_id AS type_id, t.slug AS type_slug, i.value
        FROM identifiers i
        JOIN entity_identifier_types t ON t.id = i.entity_identifier_type_id
        WHERE i.entity_id = $1 AND t.entity_type = $2 AND NOT t.is_internal
        ORDER BY t.slug, i.value
        """,
        entity_id,
        entity_type,
    )
    return [dict(r) for r in rows]


def identifier_match(entity_type: str, id_column: str, type_param: int, value_param: int) -> str:
    """SQL predicate: the row holds the identifier, or the filter is absent.

    ``$type_param`` / ``$value_param`` are both NULL (no filter) or both set — the
    route's ``identifier_filter`` dependency guarantees it. An ``EXISTS`` rather
    than a join, so an entity holding the value twice is still one row, and every
    holder is returned: identifiers have no ``(type, value)`` uniqueness, so a
    duplicate link shows as two rows rather than one silent pick. Only stored rows
    match — a ``pm_*`` type has none; read an entity by id with ``GET /{id}``.
    """
    if entity_type not in _LOOKUP_TYPES:
        raise ValueError(f"no identifier lookup for {entity_type!r}")
    return (
        f"(${type_param}::TEXT IS NULL OR EXISTS ("
        " SELECT 1 FROM identifiers i"
        " JOIN entity_identifier_types t ON t.id = i.entity_identifier_type_id"
        f" WHERE i.entity_id = {id_column} AND t.entity_type = '{entity_type}'"
        f" AND t.slug = ${type_param} AND i.value = ${value_param}))"
    )
