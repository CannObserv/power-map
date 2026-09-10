"""The curation overlay's vocabulary (#497, CR 15 / CR 26).

docs/SCHEMA.md promises that an override naming a field no model maps is never
silently ignored. That has to hold per entity type — a `parent_id` on a person
is as unmapped as a made-up field — and for the entity types no model reads
yet (role, assignment: #500).

It is reported, not fatal (CR 26): the row was never going to be applied, and
halting would withhold every other table for it. Measured before the change:
one such row skipped 23 nodes and wrote no desired state at all.
"""

import pytest

pytest.importorskip("dbt.adapters.duckdb")

from tests.core.ingestion.mapping.conftest import (  # noqa: E402
    DEFAULT_OVERLAY,
    EXPECTED_WARNINGS,
    MO1,
    MO4,
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
