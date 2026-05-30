"""Core observation service: identifier-based entity match or create."""

from enum import StrEnum

from src.core.db import generate_id
from src.core.logging import get_logger

logger = get_logger(__name__)


class Disposition(StrEnum):
    AUTO_ATTACHED = "auto-attached"
    NEW = "new"
    REJECTED = "rejected"


async def resolve_entity(
    conn,
    identifier_type_slug: str,
    identifier_value: str,
) -> tuple[str, str, Disposition]:
    """Find or create the entity identified by the given identifier.

    Returns (entity_id, entity_type, disposition).

    disposition is:
      - AUTO_ATTACHED  if an existing identifier row was found
      - NEW            if a new entity + identifier row were created
      - REJECTED       if the identifier_type_slug is unknown

    Raises nothing — REJECTED is returned, not raised.
    """
    # Resolve identifier type
    eit = await conn.fetchrow(
        "SELECT id, entity_type FROM entity_identifier_types WHERE slug = $1",
        identifier_type_slug,
    )
    if eit is None:
        logger.warning("Unknown identifier_type_slug=%r", identifier_type_slug)
        return "", "", Disposition.REJECTED

    entity_identifier_type_id = eit["id"]
    entity_type = eit["entity_type"]

    # Try exact match
    existing = await conn.fetchrow(
        "SELECT entity_id FROM identifiers WHERE entity_identifier_type_id = $1 AND value = $2",
        entity_identifier_type_id,
        identifier_value,
    )
    if existing:
        return existing["entity_id"], entity_type, Disposition.AUTO_ATTACHED

    # No match — create entity + identifier
    entity_id = await _create_entity(conn, entity_type)
    await conn.execute(
        "INSERT INTO identifiers (id, entity_id, entity_identifier_type_id, value)"
        " VALUES ($1, $2, $3, $4)",
        generate_id(),
        entity_id,
        entity_identifier_type_id,
        identifier_value,
    )
    logger.info(
        "Created %s entity_id=%s for identifier_type=%s value=%r",
        entity_type,
        entity_id,
        identifier_type_slug,
        identifier_value,
    )
    return entity_id, entity_type, Disposition.NEW


async def _create_entity(conn, entity_type: str) -> str:
    """Insert a minimal entity row and return its id."""
    entity_id = generate_id()
    if entity_type == "person":
        await conn.execute("INSERT INTO people (id) VALUES ($1)", entity_id)
    elif entity_type == "organization":
        await conn.execute("INSERT INTO organizations (id) VALUES ($1)", entity_id)
    elif entity_type == "role_assignment":
        # role_assignments require person_id + role_id — cannot be created bare
        raise ValueError("Cannot create bare role_assignment entity from observation")
    else:
        raise ValueError(f"Unknown entity_type: {entity_type!r}")
    return entity_id
