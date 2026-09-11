"""The curation overlay's vocabulary (#497, CR 15 / CR 26).

docs/SCHEMA.md promises that an override naming a field no model maps is never
silently ignored. That has to hold per entity type — a `parent_id` on a person
is as unmapped as a made-up field — and for the entity types no model reads
yet (role, assignment: #500).

It is reported, not fatal (CR 26): the row was never going to be applied, and
halting would withhold every other table for it. Measured before the change:
one such row skipped 23 nodes and wrote no desired state at all.
"""

import re

import pytest

pytest.importorskip("dbt.adapters.duckdb")

from src.api.admin.overlay_slots import SLOTS  # noqa: E402
from src.core.ingestion.mapping import PROJECT_DIR, load_manifest  # noqa: E402
from tests.core.ingestion.mapping.conftest import (  # noqa: E402
    DEFAULT_OVERLAY,
    EXPECTED_WARNINGS,
    MO1,
    MO4,
    NOW,
    P1,
    PM1,
    overlay_row,
)

UNMAPPED = [
    overlay_row("person", PM1, "parent_id", MO1),  # a field mapped for another type
    overlay_row("organization", MO4, "name", "X"),  # persons say name; orgs say legal_name
    overlay_row("person", PM1, "pronouns", "they"),  # not producer-owned at all
    overlay_row("role", "01ROLE00000000000000000000A", "name", "X"),  # no model maps roles yet
]
IDS = ["person.parent_id", "organization.name", "person.pronouns", "role.name"]


@pytest.mark.parametrize("row", UNMAPPED, ids=IDS)
def test_an_overlay_row_naming_an_unmapped_field_is_named_and_dropped(build, row):
    b = build(overlay=[*DEFAULT_OVERLAY, row])

    assert b.result.success
    assert "overlay_field_unmapped" in b.warnings
    assert b.failures == []


def test_an_unmapped_row_costs_nothing_else(build):
    """Every mart still lands, and the mapped override beside the bad one still wins."""
    b = build(overlay=[*DEFAULT_OVERLAY, UNMAPPED[2]])
    names = {r[1]: r[2] for r in b.rows("desired_person_names")}

    assert names[P1] == "Curated One"
    assert b.rows("desired_organization_parents")
    assert b.rows("desired_organization_acronyms")
    assert sorted(set(b.warnings) - {"overlay_field_unmapped"}) == EXPECTED_WARNINGS


def test_the_mapped_pairs_build_without_a_warning(build):
    """The default overlay carries a person name and an organization parent — both mapped."""
    b = build()

    assert b.result.success
    assert "overlay_field_unmapped" not in b.warnings


def test_an_archived_pin_is_applied_nowhere(build):
    """#498: unpin archives. The row stays as history, and the producer's value is the
    one the desired state carries again — the model reads active pins only."""
    live = build()
    unpinned = build(overlay=[overlay_row("person", PM1, "name", "Curated One", archived_at=NOW)])
    names = {r[1]: r[2] for r in unpinned.rows("desired_person_names")}

    assert {r[1]: r[2] for r in live.rows("desired_person_names")}[P1] == "Curated One"
    assert names[P1] != "Curated One"
    assert "overlay_field_unmapped" not in unpinned.warnings


# --- #498: one vocabulary, three places ---------------------------------------

VOCABULARY_SQL = PROJECT_DIR / "tests" / "overlay_field_unmapped.sql"
_PAIR = re.compile(
    r"\(entity_type\s*=\s*'(\w+)'\s+and\s+field\s*(?:=\s*'(\w+)'|in\s*\(([^)]*)\))\)", re.S
)


def _vocabulary_pairs() -> set[tuple[str, str]]:
    """The (entity_type, field) pairs the dbt vocabulary test accepts."""
    pairs = set()
    for entity_type, one, many in _PAIR.findall(VOCABULARY_SQL.read_text()):
        fields = [one] if one else re.findall(r"'(\w+)'", many)
        pairs |= {(entity_type, f) for f in fields}
    return pairs


def test_the_manifest_the_models_and_the_admin_agree_on_the_pinnable_fields():
    """#498: the manifest names each owned slot's overlay field, the models accept
    exactly those pairs, and the admin offers exactly those. A slot in one list and
    not another is a pin that is refused, ignored, or never offered."""
    manifest = {(s.entity, s.overlay) for s in load_manifest().tables.values() if s.overlay}

    assert _vocabulary_pairs() == manifest
    assert set(SLOTS) == manifest


def test_the_vocabulary_parser_reads_every_pair_the_sql_names():
    """The sync test above must not pass by parsing nothing."""
    assert len(_vocabulary_pairs()) == 5
