"""org_lineage → org link events (#592).

usa-wa's `org_lineage` publishes committee links — `succeeded_by`, `merged_with`,
`split_from` — keyed (subject, slug, linked). PM owns them on anchored pairs: a
link lands only when both ends are in scope, and one with an end that is not is
named by a warn test and lands the night after its create executes.
"""

import pytest

pytest.importorskip("dbt.adapters.duckdb")

from tests.core.ingestion.mapping.conftest import (  # noqa: E402
    O4,
    O5,
    O6,
    O7,
    O10,
    fixture_csv,
)

LINEAGE = "org_lineage"


def test_staging_carries_every_published_link_typed(build):
    b = build()

    assert b.columns("stg_usa_wa__org_lineage") == [
        "subject_entity_id",
        "slug",
        "linked_entity_id",
        "effective_year",
    ]
    assert b.rows("stg_usa_wa__org_lineage", order_by="subject_entity_id, slug") == [
        (O10, "merged_with", O4, 2001),
        (O5, "succeeded_by", O4, 2021),
        (O7, "split_from", O6, 2015),
    ]


def test_an_unknown_link_type_fails_the_build_rather_than_being_ignored(build):
    """A fourth slug is a contract change PM has not mapped: halt, never drop it."""
    b = build(
        datasets={LINEAGE: fixture_csv(LINEAGE, add=[f"{O6},absorbed_by,{O4},31000,31001,2023,,"])},
        must_succeed=False,
    )

    assert (
        "accepted_values_stg_usa_wa__org_lineage_slug__succeeded_by__merged_with__split_from"
        in b.failures
    )
