"""Shared type aliases — keep DB enum-like constants in one place.

Mirrors the DB CHECK constraint values so admin forms, ingestion code,
and tests can reference a single source of truth. If the DB constraint
changes, update the alias here and the schema.sql CHECK in lockstep.
The `tests/core/test_types.py` parity tests fail when the two drift —
both Python-vs-schema and tuple-vs-Literal.
"""

from typing import Literal

# person_names_visibility_check (src/core/schema.sql)
PersonNameVisibility = Literal["public", "legal_only", "hidden"]

# person_names_name_type_check (src/core/schema.sql).
# Literal first — the static type. The runtime tuple is built from
# ``typing.get_args`` below so the two cannot drift.
PersonNameType = Literal[
    "legal",
    "preferred",
    "alias",
    "former",
    "initials",
    "maiden",
    "religious",
    "stage",
    "deadname",
    "reading",
    "romanization",
    "mrz",
    "variant",
]
# Order is the dropdown render order — semantic groupings (legal /
# common variants / cultural-awareness / phonetic decompositions /
# alt-spelling). Issue #135 added 'variant'.
PERSON_NAME_TYPES: tuple[PersonNameType, ...] = (
    "legal",
    "preferred",
    "alias",
    "former",
    "initials",
    "maiden",
    "religious",
    "stage",
    "deadname",
    "reading",
    "romanization",
    "mrz",
    "variant",
)

# organization_names CHECK (src/core/schema.sql).
OrgNameType = Literal["legal", "dba", "former"]
ORG_NAME_TYPES: tuple[OrgNameType, ...] = ("legal", "dba", "former")

# entity_identifier_types.entity_type CHECK (src/core/schema.sql).
EntityType = Literal["organization", "person", "role_assignment", "jurisdiction"]
VALID_ENTITY_TYPES: tuple[EntityType, ...] = (
    "organization",
    "person",
    "role_assignment",
    "jurisdiction",
)

# addresses.precision CHECK (src/core/schema.sql migration #170).
# Subset allowed for event_place_address_id linkage — city-level or finer.
# NULL precision is also accepted (pre-geocoding / historical records); only
# a known low-precision value (country, region) is rejected.
EVENT_PLACE_PRECISIONS: frozenset[str] = frozenset({"city", "postal", "street"})
