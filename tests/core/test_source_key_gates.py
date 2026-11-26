"""#882: a ``source_key_id`` gate exists exactly where the observation API can
overwrite or remove a value — and who created a row is not who may overwrite it.

Every table carrying ``source_key_id`` is declared here with what the column
does on it: a **gate** (the same-or-NULL ``provenance_conflict`` refusal, on the
write it guards) or a **stamp** (provenance only — the API adds rows there and
never overwrites or removes one). A new column fails this test until it is
declared and documented in ``docs/OBSERVATIONS.md`` § Record ownership. People
and roles carry none: nothing on either row is overwritable through the API, so
neither has a gate, and the column must not arrive as creation provenance.
"""

import re
from pathlib import Path

DECLARED: dict[str, str] = {
    "role_assignments": "gate: id-addressed tenure update and retract (#311/#391)",
    "organizations": "gate: the parent, on an id-addressed reparent (#334)",
    "entity_events": "gate: id-addressed refine and retract (#321/#322)",
    "citations": "gate: id-addressed refine and retract (#319)",
    "role_assignment_relationships": "gate: id-addressed refine and retract (#301)",
    "person_names": "gate: name retract (#845)",
    "organization_names": "stamp: the API only adds org names (#162)",
}

_CREATE = re.compile(r"CREATE TABLE IF NOT EXISTS (\w+) \((.*?)\n\);", re.S)
_ADD = re.compile(r"ALTER TABLE (\w+)\s+ADD COLUMN (?:IF NOT EXISTS )?source_key_id\b")


def _tables_with_source_key(sql: str) -> set[str]:
    found = {m.group(1) for m in _ADD.finditer(sql)}
    for m in _CREATE.finditer(sql):
        if re.search(r"^\s*source_key_id\b", m.group(2), re.M):
            found.add(m.group(1))
    return found


def test_every_source_key_column_is_declared():
    sql = Path("src/core/schema.sql").read_text()
    assert _tables_with_source_key(sql) == set(DECLARED)


def test_people_and_roles_have_no_gate():
    assert not {"people", "roles"} & set(DECLARED)


def test_the_principle_is_written_down():
    doc = Path("docs/OBSERVATIONS.md").read_text()
    assert "## Record ownership" in doc
    for table in DECLARED:
        assert f"`{table}`" in doc, table


def test_the_parser_sees_both_forms():
    sql = (
        "CREATE TABLE IF NOT EXISTS a (\n    id TEXT,\n    source_key_id TEXT\n);\n"
        "ALTER TABLE b\n    ADD COLUMN IF NOT EXISTS source_key_id TEXT;\n"
        "ALTER TABLE d ADD COLUMN source_key_id TEXT;\n"
        "CREATE TABLE IF NOT EXISTS c (\n    id TEXT\n);\n"
    )
    assert _tables_with_source_key(sql) == {"a", "b", "d"}
