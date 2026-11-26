"""The stable code every observation refusal carries (#882).

An observation's ``reason`` is diagnostic text, free to change. ``reason_code``
is the contract a producer acts on: one ``ObservationReasonCode`` per refusal,
set on every ``rejected`` disposition and on nothing else.

A code is the slug at the head of the reason (the text before ``": "``), with
three exceptions:

- the same-or-NULL ``source_key_id`` gate is ``provenance_conflict`` wherever it
  refuses. Its older names, ``source_key_mismatch`` (assignments, org parent) and
  ``name_provenance_conflict`` (names), live on only in the text, for one release.
- free-text validation messages (an unknown link, contact or affiliation type,
  an address the normaliser refused, a parent lookup that matched other than
  once) are ``invalid``.
- an additional identifier of an unknown type is ``unknown_identifier_type``, as
  the top-level identifier's is.

Embedded events and citations re-raise their own slug, so ``EventRejectReason``
and ``CitationRejectReason`` values are codes too; ``tests/core/test_refusals.py``
holds them inside this enum, and ``tests/core/test_refusal_code_sweep.py`` holds
every refusal site under ``src/`` to a code.
"""

from enum import StrEnum

__all__ = ["ObservationReasonCode", "Refusal"]


class ObservationReasonCode(StrEnum):
    """Why an observation was refused: ``ObservationResponse.reason_code`` (#882).

    Transient (may clear on a later cycle, once what it names arrives):
    ``linked_entity_unresolved``, ``entity_unresolved``. The rest are terminal for
    the payload as sent. The table with each code's meaning is in
    ``docs/PRODUCER_CONTRACT.md`` § Reason codes.
    """

    # Resolving the observed entity
    UNKNOWN_IDENTIFIER_TYPE = "unknown_identifier_type"
    PM_ID_NOT_FOUND = "pm_id_not_found"
    PERSON_ARCHIVED = "person_archived"
    ORGANIZATION_ARCHIVED = "organization_archived"
    ROLE_ARCHIVED = "role_archived"
    JURISDICTION_ARCHIVED = "jurisdiction_archived"
    ROLE_ASSIGNMENT_ARCHIVED = "role_assignment_archived"
    ENTITY_TYPE_NOT_CREATABLE = "entity_type_not_creatable"
    ENTITY_TYPE_MISMATCH = "entity_type_mismatch"
    JURISDICTION_NEW_REQUIRES_CREATE_DATA = "jurisdiction_new_requires_create_data"
    UNKNOWN_JURISDICTION_TYPE = "unknown_jurisdiction_type"
    UNIQUE_VIOLATION = "unique_violation"
    # Roles
    ORG_NOT_FOUND = "org_not_found"
    ROLE_TYPE_NOT_FOUND = "role_type_not_found"
    JURISDICTION_NOT_FOUND = "jurisdiction_not_found"
    ROLE_TYPE_REQUIRED_FOR_JURISDICTION = "role_type_required_for_jurisdiction"
    QUALIFIER_REQUIRED = "qualifier_required"
    QUALIFIER_FORBIDDEN = "qualifier_forbidden"
    TITLE_REQUIRED = "title_required"
    # Assignments
    PERSON_NOT_FOUND = "person_not_found"
    ROLE_NOT_FOUND = "role_not_found"
    ASSIGNMENT_NOT_FOUND = "assignment_not_found"
    IS_CURRENT_END_DATE_CONFLICT = "is_current_end_date_conflict"
    START_AFTER_END_DATE = "start_after_end_date"
    START_DATE_CONFLICT = "start_date_conflict"
    # Assignments and org parent (#882): a subscribed dataset owns the row, and
    # its nightly apply would revert the write — correct it at the dataset.
    OWNED_BY_DATASET = "owned_by_dataset"
    # Org parent
    PARENT_NOT_FOUND = "parent_not_found"
    PARENT_CYCLE = "parent_cycle"
    ACTIVE_ON_ARCHIVED_ORG = "active_on_archived_org"
    # Identifiers
    IDENTIFIER_CONFLICT = "identifier_conflict"
    IDENTIFIER_TAKEN = "identifier_taken"
    IDENTIFIER_TYPE_MISMATCH = "identifier_type_mismatch"
    IDENTIFIER_LOCKED = "identifier_locked"
    REPLACES_UNSUPPORTED = "replaces_unsupported"
    REPLACES_NOT_HELD = "replaces_not_held"
    # Embedded events (EventRejectReason)
    LINKED_ENTITY_UNRESOLVED = "linked_entity_unresolved"  # transient
    EVENT_NOT_FOUND = "event_not_found"
    APPLIES_TO_MISMATCH = "applies_to_mismatch"
    UNKNOWN_EVENT_TYPE = "unknown_event_type"
    EVENT_OWNED_BY_DATASET = "event_owned_by_dataset"
    # Embedded citations (CitationRejectReason)
    ENTITY_UNRESOLVED = "entity_unresolved"  # transient
    CITATION_NOT_FOUND = "citation_not_found"
    CITABLE_FIELD_UNKNOWN = "citable_field_unknown"
    # Names (NameRejectReason)
    NAME_IS_CANONICAL = "name_is_canonical"
    NAME_IS_LAST = "name_is_last"
    NAME_HAS_READINGS = "name_has_readings"
    NAME_HAS_FOREIGN_CITATIONS = "name_has_foreign_citations"
    # Shared
    PROVENANCE_CONFLICT = "provenance_conflict"
    IDENTITY_IMMUTABLE = "identity_immutable"
    MISSING_REQUIRED_FIELD = "missing_required_field"
    DB_CONSTRAINT_VIOLATION = "db_constraint_violation"
    INVALID = "invalid"


class Refusal(str):
    """A resolver's refusal: reads as its diagnostic text and carries its code.

    Resolvers answer ``REJECTED`` with a reason rather than raise; a ``Refusal``
    is that reason, so callers comparing text keep working while the route reads
    ``.code``. The text defaults to the code itself.
    """

    code: ObservationReasonCode

    def __new__(cls, code: ObservationReasonCode, detail: str | None = None) -> "Refusal":
        """Build a refusal with ``code``; its text is ``detail``, or the code's value."""
        if not isinstance(code, ObservationReasonCode):
            raise TypeError(f"Refusal code must be an ObservationReasonCode, not {code!r}")
        obj = super().__new__(cls, code.value if detail is None else detail)
        obj.code = code
        return obj
