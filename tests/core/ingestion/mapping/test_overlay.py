"""The curation overlay's vocabulary (#497, CR 15).

docs/SCHEMA.md promises that an override naming a column no model maps fails
the build loudly rather than being ignored. That has to hold per entity type —
a `parent_id` on a person is as unmapped as a made-up field — and for the
entity types no model reads yet (role, assignment: #500).
"""

import pytest

pytest.importorskip("dbt.adapters.duckdb")

from tests.core.ingestion.mapping.conftest import (  # noqa: E402
    DEFAULT_OVERLAY,
    MO1,
    MO4,
    PM1,
    overlay_row,
)


@pytest.mark.parametrize(
    "row",
    [
        overlay_row("person", PM1, "parent_id", MO1),  # a field mapped for another type
        overlay_row("organization", MO4, "name", "X"),  # persons say name; orgs say legal_name
        overlay_row("person", PM1, "pronouns", "they"),  # not producer-owned at all
        overlay_row("role", "01ROLE00000000000000000000A", "name", "X"),  # no model maps roles yet
    ],
    ids=["person.parent_id", "organization.name", "person.pronouns", "role.name"],
)
def test_an_overlay_row_naming_an_unmapped_field_fails_the_build(build, row):
    b = build(overlay=[*DEFAULT_OVERLAY, row], must_succeed=False)

    assert not b.result.success
    assert "overlay_field_unmapped" in b.failures


def test_the_mapped_pairs_build(build):
    """The default overlay carries a person name and an organization parent — both mapped."""
    b = build()

    assert b.result.success
    assert "overlay_field_unmapped" not in b.failures
