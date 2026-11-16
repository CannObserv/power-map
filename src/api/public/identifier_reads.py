"""Identifier reads shared by the role, assignment, people and org routes.

The role and assignment read sides follow the people/orgs detail shape (``{id,
type_id, type_slug, value}``, internal types omitted), and both list routes take
the same optional ``identifier_type`` + ``identifier_value`` filter (#808, #809).
``people/search`` and ``orgs/search`` in identifier mode return every holder the
same way (#811), so all four entity types answer a lookup alike on the wire.
"""

from typing import Any

#: The entity types an identifier lookup serves: the list-route filter (#808,
#: #809) and the search routes' identifier mode (#811).
_LOOKUP_TYPES = frozenset({"person", "organization", "role", "role_assignment"})


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


def identifier_holders(entity_type: str, type_param: int, value_param: int) -> str:
    """SQL subquery: the ids of every entity holding the identifier.

    For ``{id_column} IN (...)``. ``IN`` makes it a semi-join, so an entity
    holding the value in two rows is still one holder, and every holder is
    returned: identifiers have no ``(type, value)`` uniqueness, so a duplicate
    link shows as two rows rather than one silent pick. The ``(type, value)``
    lookup index drives it. Only stored rows match — a ``pm_*`` type has none;
    read an entity by id with ``GET /{id}``. The search routes' identifier mode
    uses it bare, both params always set (#811): a generic plan could not fold
    away :func:`identifier_match`'s "filter absent" branch.
    """
    if entity_type not in _LOOKUP_TYPES:
        raise ValueError(f"no identifier lookup for {entity_type!r}")
    return (
        "SELECT i.entity_id FROM identifiers i"
        " JOIN entity_identifier_types t ON t.id = i.entity_identifier_type_id"
        f" WHERE t.entity_type = '{entity_type}'"
        f" AND t.slug = ${type_param} AND i.value = ${value_param}"
    )


def identifier_match(entity_type: str, id_column: str, type_param: int, value_param: int) -> str:
    """SQL predicate: the row holds the identifier, or the filter is absent.

    ``$type_param`` / ``$value_param`` are both NULL (no filter) or both set — the
    route's ``identifier_filter`` dependency guarantees it. The list routes' optional
    filter (#808, #809), over :func:`identifier_holders`.
    """
    holders = identifier_holders(entity_type, type_param, value_param)
    return f"(${type_param}::TEXT IS NULL OR {id_column} IN ({holders}))"
