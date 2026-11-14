"""One value of a ``*_cannabis_observer`` identifier type names one entity (#812).

``uq_identifiers_cannabis_observer`` (schema.sql) is a partial unique index on
``identifiers (entity_identifier_type_id, value)`` over the four type ids below.
It covers every row: an archived holder keeps its identifiers (#481) and so
still owns the value. A write that would put a held value on a second entity is
rejected ``identifier_taken``, distinct from ``identifier_conflict`` (this
entity already holds a *different* value of the type).
"""

import re
from dataclasses import dataclass

import asyncpg

#: The index, and the constraint name a ``unique_violation`` on it carries.
IDENTIFIER_TAKEN_GUARD = "uq_identifiers_cannabis_observer"

#: The type ids the index predicate names, as literals: a partial index can't
#: read ``entity_identifier_types``. Fixed seeds (#808); a test holds this set
#: equal to both the seeded ids and the live predicate.
UNIQUE_IDENTIFIER_TYPE_IDS = frozenset(
    {
        "01M7DPQMCVA815DZ2M8PXS198H",  # org_cannabis_observer
        "01M7DPQMH6CXKNTY183BXDMN1V",  # person_cannabis_observer
        "01M7DPQMPBJEQ52A6TN3GGTGDA",  # role_cannabis_observer
        "01M7DPQMVS8Y6PX1KB5CES7SGQ",  # role_assignment_cannabis_observer
    }
)

#: ``unique_violation`` detail for the index: ``Key (entity_identifier_type_id,
#: value)=(<type id>, <value>) already exists.`` A type id is a ULID, so the first
#: ``, `` ends it and the value keeps any commas or parentheses of its own.
_KEY_DETAIL = re.compile(
    r"^Key \(entity_identifier_type_id, value\)=\(([0-9A-Z]{26}), (.*)\) already exists\.$",
    re.DOTALL,
)


#: The holder of a (type, value), archived or not. Inline per entity type rather
#: than a table map: this module sits under ``observation``, which every map's
#: home imports.
_HOLDER_SQL = """
SELECT i.entity_id,
  CASE t.entity_type
      WHEN 'person' THEN (SELECT archived_at FROM people WHERE id = i.entity_id)
      WHEN 'organization' THEN (SELECT archived_at FROM organizations WHERE id = i.entity_id)
      WHEN 'role' THEN (SELECT archived_at FROM roles WHERE id = i.entity_id)
      WHEN 'role_assignment' THEN (SELECT archived_at FROM role_assignments WHERE id = i.entity_id)
      WHEN 'jurisdiction' THEN (SELECT archived_at FROM jurisdictions WHERE id = i.entity_id)
  END IS NOT NULL AS archived
FROM identifiers i
JOIN entity_identifier_types t ON t.id = i.entity_identifier_type_id
WHERE i.entity_identifier_type_id = $1 AND i.value = $2
  AND i.entity_id IS DISTINCT FROM $3
ORDER BY i.id
LIMIT 1
"""


@dataclass(frozen=True)
class Holder:
    """The entity holding a value: its id, and whether it is archived (#481)."""

    entity_id: str
    archived: bool


def is_identifier_taken(exc: asyncpg.UniqueViolationError) -> bool:
    """True when a ``unique_violation`` is this index's, not another constraint's."""
    return exc.constraint_name == IDENTIFIER_TAKEN_GUARD


def taken_key(exc: asyncpg.UniqueViolationError) -> tuple[str, str] | None:
    """The ``(type id, value)`` the index refused, read off the violation's detail."""
    match = _KEY_DETAIL.match(exc.detail or "")
    return (match[1], match[2]) if match else None


async def identifier_holder(
    conn, type_id: str, value: str, *, other_than: str | None = None
) -> Holder | None:
    """The entity holding ``value`` of ``type_id``, archived included; None if nobody.

    ``other_than`` skips the entity asking, for a caller that has already found it
    does not hold the value itself.
    """
    row = await conn.fetchrow(_HOLDER_SQL, type_id, value, other_than)
    return None if row is None else Holder(row["entity_id"], row["archived"])


def taken_reason(slug: str, holder: Holder | None) -> str:
    """``identifier_taken: '<slug>' held by [archived ]'<id>'`` — the observation reason."""
    if holder is None:
        return f"identifier_taken: {slug!r}"
    state = "archived " if holder.archived else ""
    return f"identifier_taken: {slug!r} held by {state}{holder.entity_id!r}"


async def unique_violation_reason(conn, exc: asyncpg.UniqueViolationError) -> str:
    """An observation route's reason for a ``unique_violation`` that aborted its write.

    This index's violation is a lost race past ``write_additional_identifiers``'
    pre-check, and answers what the pre-check would have: ``identifier_taken``,
    naming the type and the holder. Any other is ``db_constraint_violation``, as
    before. The route's transaction has rolled back, so ``conn`` reads again.
    """
    if not is_identifier_taken(exc):
        return "db_constraint_violation"
    key = taken_key(exc)
    if key is None:
        return "identifier_taken"
    type_id, value = key
    slug = await conn.fetchval("SELECT slug FROM entity_identifier_types WHERE id = $1", type_id)
    if slug is None:
        return "identifier_taken"
    return taken_reason(slug, await identifier_holder(conn, type_id, value))
