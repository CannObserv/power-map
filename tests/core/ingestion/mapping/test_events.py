"""desired_entity_events (#497 step 6): `dissolved` is the only owned type.

The year rule and the current-biennium rule were measured before this was
written: all 152 of PM's dissolved events on anchored orgs equal
int(last_biennium[:4]) + 1, and the dataset's newest biennium holds 34 orgs
that are still live.
"""

import pytest

pytest.importorskip("dbt.adapters.duckdb")

from tests.core.ingestion.mapping.conftest import (  # noqa: E402
    DEFAULT_OVERLAY,
    MO4,
    MO5,
    MO10,
    O4,
    O5,
    fixture_csv,
    overlay_row,
)


def test_dissolved_year_is_the_end_of_the_last_biennium(build):
    b = build()
    events = {r[0]: r for r in b.rows("desired_entity_events")}

    assert b.columns("desired_entity_events") == [
        "pm_id",
        "producer_id",
        "entity_type",
        "event_type",
        "event_year",
    ]
    assert events[MO5] == (MO5, O5, "organization", "dissolved", 2020)  # 2019-20


def test_the_century_wrap_biennium_resolves_to_the_right_year(build):
    """'1999-00' ends in 2000, not 1900 and not 1999+1 read from the suffix."""
    b = build()
    events = {r[0]: r for r in b.rows("desired_entity_events")}

    assert events[MO10][4] == 2000


def test_an_org_at_the_current_biennium_is_still_live(build):
    """Current = the dataset's newest biennium; no clock, so a lagging publish under-claims."""
    b = build()

    assert MO4 not in {r[0] for r in b.rows("desired_entity_events")}


def test_orgs_without_bienniums_get_no_event(build):
    b = build()

    assert {r[0] for r in b.rows("desired_entity_events")} == {MO5, MO10}


def test_only_dissolved_is_ever_asserted(build):
    b = build()

    assert {r[3] for r in b.rows("desired_entity_events")} == {"dissolved"}


def _o5_last_biennium(value: str) -> dict[str, str]:
    """The fixture with O5's last_biennium (2019-20) replaced."""
    edited = fixture_csv("organizations", replace={"1991-92,2019-20": f"1991-92,{value}"})
    return {"organizations": edited}


def test_a_malformed_biennium_is_reported_and_claims_no_event(build):
    """CR 18: `cast(substr(...))` on 'unknown' would error the model and halt the build.
    Staging keeps only YYYY-YY; the reject is named by a warn test and claims nothing."""
    b = build(datasets=_o5_last_biennium("unknown"))
    events = {r[0]: r for r in b.rows("desired_entity_events")}

    assert b.result.success
    assert MO5 not in events
    assert events[MO10][4] == 2000
    assert "malformed_bienniums" in b.warnings


def test_a_stray_high_biennium_cannot_dissolve_the_living(build):
    """current = max(last_biennium) as text. One '9999' would make every real biennium
    "past" and dissolve every live org — a mass write from one bad row."""
    b = build(datasets=_o5_last_biennium("9999"))
    events = {r[0]: r for r in b.rows("desired_entity_events")}

    assert MO4 not in events  # 2025-26 is still the current biennium
    assert MO5 not in events
    assert events[MO10][4] == 2000


# --- #498: organization.dissolved_year, the fifth overlay slot -----------------


def _pinned(year: str | None, org=MO5):
    return [*DEFAULT_OVERLAY, overlay_row("organization", org, "dissolved_year", year)]


def _years(b) -> dict[str, int]:
    return {r[0]: r[4] for r in b.rows("desired_entity_events")}


def test_a_pinned_year_replaces_the_producers(build):
    b = build(overlay=_pinned("2018"))

    assert _years(b)[MO5] == 2018
    assert "overlay_field_unmapped" not in b.warnings


def test_a_pinned_year_dissolves_an_org_the_producer_holds_live(build):
    """Presence wins: the curator asserts a dissolution the producer does not publish."""
    b = build(overlay=_pinned("2023", org=MO4))
    events = {r[0]: r for r in b.rows("desired_entity_events")}

    assert events[MO4] == (MO4, O4, "organization", "dissolved", 2023)


def test_a_null_pin_withdraws_the_producers_dissolution(build):
    """The curator says "not dissolved": the row drops rather than falling back."""
    b = build(overlay=_pinned(None))

    assert MO5 not in _years(b)
    assert _years(b)[MO10] == 2000  # the pin is scoped to its entity


def test_a_year_that_is_not_an_integer_is_named_and_applied_nowhere(build):
    """Never silently ignored, never read as a withdrawal: the producer's year stands
    and the test names the row."""
    b = build(overlay=_pinned("twenty-eighteen"))

    assert _years(b)[MO5] == 2020
    assert "overlay_value_malformed" in b.warnings
    assert b.failures == []
