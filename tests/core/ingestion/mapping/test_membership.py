"""membership_type → role_assignments.membership_type (#671).

usa-wa's `assignments` 1.8.0 tells a span's holder in its own right (`principal`)
from one serving under a principal (`substitute`, and the declared-but-empty
`ex_officio` / `acting`). `desired_role_assignment_membership` claims it per
span, keyed like the dates; a curator's pin wins by presence (#498).
"""

import pytest

pytest.importorskip("dbt.adapters.duckdb")

from tests.core.ingestion.mapping.conftest import (  # noqa: E402
    DEFAULT_OVERLAY,
    RA1,
    RA2,
    SPAN_CLOSED,
    SPAN_NEW,
    SPAN_OPEN,
    SPAN_OUT,
    fixture_csv,
    overlay_row,
)

MEMBERSHIP = "desired_role_assignment_membership"
# The closed span's line ends in its 1.8.0 columns; swapping the last one makes it
# a substitute without restating the row.
CLOSED_TAIL = f"{SPAN_CLOSED},dated,dated,2017-01-09,2020-12-31,principal"


def _membership(built) -> dict[str, tuple]:
    return {r[1]: r for r in built.rows(MEMBERSHIP)}


def _as(value: str) -> dict[str, str]:
    tail = CLOSED_TAIL.rsplit(",", 1)[0] + f",{value}"
    return {"assignments": fixture_csv("assignments", replace={CLOSED_TAIL: tail})}


def _pinned(*pins):
    return [*DEFAULT_OVERLAY, *(overlay_row("assignment", *p) for p in pins)]


def test_staging_carries_membership_type(build):
    b = build()

    assert "membership_type" in b.columns("stg_usa_wa__assignments")
    assert {r[0] for r in b.rows("stg_usa_wa__assignments", order_by="span_key")} >= {
        SPAN_OPEN,
        SPAN_CLOSED,
    }


def test_each_span_in_scope_claims_its_membership(build):
    """Keyed like the dates: a create carries no pm_id, an out-of-scope span is absent."""
    b = build()

    assert b.columns(MEMBERSHIP) == ["pm_id", "producer_id", "membership_type"]
    assert _membership(b) == {
        SPAN_OPEN: (RA1, SPAN_OPEN, "principal"),
        SPAN_CLOSED: (RA2, SPAN_CLOSED, "principal"),
        SPAN_NEW: (None, SPAN_NEW, "principal"),
    }
    assert SPAN_OUT not in _membership(b)


def test_a_substitute_is_claimed_as_one(build):
    b = build(datasets=_as("substitute"))

    assert _membership(b)[SPAN_CLOSED][2] == "substitute"


def test_an_unknown_membership_type_fails_the_build_rather_than_being_ignored(build):
    """A fifth value is a contract change PM has not mapped: halt, never guess."""
    b = build(datasets=_as("deputy"), must_succeed=False)

    assert any(
        f.startswith("accepted_values_stg_usa_wa__assignments_membership_type") for f in b.failures
    )


def test_a_pinned_membership_type_wins(build):
    b = build(overlay=_pinned((RA2, "membership_type", "substitute")))

    assert _membership(b)[SPAN_CLOSED][2] == "substitute"
    assert "overlay_field_unmapped" not in b.warnings


def test_a_null_pin_withdraws_the_claim(build):
    """Presence wins even when null: the column is NOT NULL, so null claims nothing."""
    b = build(overlay=_pinned((RA2, "membership_type", None)))

    assert SPAN_CLOSED not in _membership(b)


def test_a_malformed_pin_is_named_and_the_producers_value_stands(build):
    b = build(overlay=_pinned((RA2, "membership_type", "Substitute")))

    assert "overlay_value_malformed" in b.warnings
    assert _membership(b)[SPAN_CLOSED][2] == "principal"
