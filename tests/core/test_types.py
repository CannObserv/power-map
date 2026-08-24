"""Schema-vs-Python parity for the enum tuples in `src.core.types`.

The DB CHECK constraints in `src/core/schema.sql` are the authoritative
source for which name_type values are accepted. The Python tuples
(`PERSON_NAME_TYPES`, `ORG_NAME_TYPES`) and Literal aliases
(`PersonNameType`, `OrgNameType`) exist so admin templates, ingestion
code, and tests can iterate / type-check one place. These tests fail
loudly when any of the three drift — which is the bug that hid
`variant` from the person-name dropdown after issue #135 expanded the
CHECK without updating the (then-hardcoded) template list.

`EntityType` / `VALID_ENTITY_TYPES` joined them in #459: that pair is now
published through OpenAPI by `GET /api/v1/entity-identifier-types`, so a CHECK
value the Literal does not know about does not degrade one row — it raises
inside the response model and 500s the whole catalog.
"""

import re
from pathlib import Path
from typing import get_args

from src.core.types import (
    ORG_NAME_TYPES,
    PERSON_NAME_TYPES,
    VALID_ENTITY_TYPES,
    EntityType,
    OrgNameType,
    PersonNameType,
)

SCHEMA = Path("src/core/schema.sql").read_text()


def _extract_check_values(table: str) -> set[str]:
    """Extract the value set from every ``name_type`` CHECK near *table*.

    Strategy: find each ``CHECK ( name_type IN (`` opener after a
    mention of *table*, then walk forward tracking paren depth until
    the matching close paren. Collect single-quoted identifiers inside.
    Robust against:

    - nested parens (e.g. function calls, type casts)
    - whitespace / newlines / SQL comments inside the IN list
    - multiple CHECK occurrences (bootstrap CREATE TABLE + later
      ``ALTER TABLE … ADD CONSTRAINT`` migration block)

    Returns the union of all occurrences after asserting they agree.
    """
    occurrences: list[set[str]] = []

    # Find every "name_type IN (" — with arbitrary whitespace.
    opener_re = re.compile(r"name_type\s+IN\s*\(", re.IGNORECASE)
    for opener in opener_re.finditer(SCHEMA):
        # Restrict to CHECKs near *table*: walk back ~600 chars, look
        # for the table name. Skip if not present (unrelated CHECK).
        window_start = max(0, opener.start() - 600)
        if table not in SCHEMA[window_start : opener.start()]:
            continue

        # Walk forward from after the opening "(", balancing parens.
        i = opener.end()
        depth = 1
        while i < len(SCHEMA) and depth > 0:
            ch = SCHEMA[i]
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
                if depth == 0:
                    break
            i += 1
        assert depth == 0, f"unbalanced parens after name_type IN at offset {opener.end()}"
        body = SCHEMA[opener.end() : i]

        # Strip line comments (-- to end-of-line) and quoted strings
        # are simple single-quotes here (no embedded quotes used in
        # this schema). Capture every single-quoted lowercase token.
        body_no_comments = re.sub(r"--[^\n]*", "", body)
        quoted = re.findall(r"'([a-z_]+)'", body_no_comments)
        assert quoted, (
            f"name_type IN block near {table!r} contained no quoted values; "
            f"extractor likely needs updating. Block: {body!r}"
        )
        occurrences.append(set(quoted))

    assert occurrences, f"no name_type CHECK found near {table!r} in schema"
    # Bootstrap CHECK and migration CHECK must agree value-for-value.
    for s in occurrences[1:]:
        assert s == occurrences[0], (
            f"{table!r}: name_type CHECK lists disagree across schema occurrences "
            f"{occurrences[0]} vs {s}"
        )
    return occurrences[0]


def _extract_entity_identifier_type_check_values() -> set[str]:
    """Extract every ``entity_type`` CHECK that governs ``entity_identifier_types``.

    Two occurrences are authoritative and must agree: the inline one in
    ``CREATE TABLE`` (fresh DBs) and the ``ALTER TABLE … ADD CONSTRAINT``
    reconciliation block that widened it for jurisdiction rows (existing DBs,
    the #307/#312 convention). Checking only the first would miss the one that
    actually applies in production.

    Ownership is decided by the **nearest preceding table anchor**, not by a
    byte window: several other tables carry an ``entity_type IN (...)`` CHECK
    with a *different* member set (``citations`` and ``entity_events`` both
    include ``role``), and the ``identifiers`` table's comment names
    ``entity_identifier_types`` a few lines above one of them — close enough
    for a proximity window to adopt the wrong constraint and still look green.
    """
    anchor_re = re.compile(
        r"(?:CREATE TABLE IF NOT EXISTS|CREATE TABLE|ALTER TABLE)\s+(\w+)", re.IGNORECASE
    )
    occurrences: list[set[str]] = []

    for opener in re.finditer(r"entity_type\s+IN\s*\(", SCHEMA, re.IGNORECASE):
        anchors = anchor_re.findall(SCHEMA[: opener.start()])
        if not anchors or anchors[-1] != "entity_identifier_types":
            continue

        # Balance parens forward so a nested cast or call cannot truncate the list.
        i, depth = opener.end(), 1
        while i < len(SCHEMA) and depth > 0:
            if SCHEMA[i] == "(":
                depth += 1
            elif SCHEMA[i] == ")":
                depth -= 1
            i += 1
        assert depth == 0, "unbalanced parens in an entity_identifier_types entity_type CHECK"
        occurrences.append(set(re.findall(r"'([^']+)'", SCHEMA[opener.end() : i - 1])))

    assert len(occurrences) >= 2, (
        "expected both the CREATE TABLE and the reconciliation CHECK for "
        f"entity_identifier_types.entity_type; found {len(occurrences)}"
    )
    assert all(o == occurrences[0] for o in occurrences), (
        "the inline and reconciliation entity_type CHECKs disagree — an existing "
        f"DB would end up with a different vocabulary than a fresh one: {occurrences}"
    )
    return occurrences[0]


def test_entity_types_matches_schema_check():
    schema_values = _extract_entity_identifier_type_check_values()
    assert set(VALID_ENTITY_TYPES) == schema_values, (
        "VALID_ENTITY_TYPES drifted from the entity_identifier_types.entity_type "
        f"CHECK constraint. Python: {set(VALID_ENTITY_TYPES)} | Schema: {schema_values}"
    )


def test_entity_types_is_unique():
    assert len(VALID_ENTITY_TYPES) == len(set(VALID_ENTITY_TYPES))


def test_person_name_types_matches_schema_check():
    schema_values = _extract_check_values("person_names")
    assert set(PERSON_NAME_TYPES) == schema_values, (
        "PERSON_NAME_TYPES drifted from person_names CHECK constraint. "
        f"Python: {set(PERSON_NAME_TYPES)} | Schema: {schema_values}"
    )


def test_org_name_types_matches_schema_check():
    schema_values = _extract_check_values("organization_names")
    assert set(ORG_NAME_TYPES) == schema_values, (
        "ORG_NAME_TYPES drifted from organization_names CHECK constraint. "
        f"Python: {set(ORG_NAME_TYPES)} | Schema: {schema_values}"
    )


def test_person_name_types_includes_variant():
    """Issue #135 added 'variant'; guard against accidental removal."""
    assert "variant" in PERSON_NAME_TYPES


def test_person_name_types_is_unique():
    """Tuple is iterated to render a UI dropdown — duplicates would
    create duplicate <option>s."""
    assert len(PERSON_NAME_TYPES) == len(set(PERSON_NAME_TYPES))


def test_org_name_types_is_unique():
    assert len(ORG_NAME_TYPES) == len(set(ORG_NAME_TYPES))


# --- Tuple ↔ Literal parity ------------------------------------------------
#
# The Literal type (`PersonNameType`) and the runtime tuple
# (`PERSON_NAME_TYPES`) are two declarations of the same closed set. If
# someone updates one and forgets the other, mypy still type-checks but
# runtime validation accepts a value the static type rejects (or vice
# versa). These tests pin them together.


def test_person_name_types_tuple_matches_literal():
    assert set(PERSON_NAME_TYPES) == set(get_args(PersonNameType)), (
        "PERSON_NAME_TYPES drifted from PersonNameType Literal. "
        f"Tuple: {set(PERSON_NAME_TYPES)} | "
        f"Literal: {set(get_args(PersonNameType))}"
    )


def test_org_name_types_tuple_matches_literal():
    assert set(ORG_NAME_TYPES) == set(get_args(OrgNameType)), (
        "ORG_NAME_TYPES drifted from OrgNameType Literal. "
        f"Tuple: {set(ORG_NAME_TYPES)} | "
        f"Literal: {set(get_args(OrgNameType))}"
    )


def test_entity_types_tuple_matches_literal():
    assert set(VALID_ENTITY_TYPES) == set(get_args(EntityType)), (
        "VALID_ENTITY_TYPES drifted from EntityType Literal. "
        f"Tuple: {set(VALID_ENTITY_TYPES)} | "
        f"Literal: {set(get_args(EntityType))}"
    )
