"""Tests for scripts/archive_legacy_legislator_roles.py (#265).

Unit tests cover the pure title/URL parsers. Integration tests (require
TEST_DATABASE_URL + schema-applied DB) cover the validator/archiver flow.

Run via:
    uv run pytest tests/scripts/test_archive_legacy_legislator_roles.py
"""

import pytest

from scripts.archive_legacy_legislator_roles import (
    SeatTitle,
    filer_id_from_url,
    parse_legacy_title,
)

# ---------------------------------------------------------------------------
# parse_legacy_title
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        # District-bearing variants observed in production (#265 audit)
        ("Senator, District 19", SeatTitle("senate", 19)),
        ("Senator, District 1", SeatTitle("senate", 1)),
        ("Representative, District 1", SeatTitle("house", 1)),
        ("Representative, District 47", SeatTitle("house", 47)),
        ("District 47 Representative", SeatTitle("house", 47)),
        ("34th District State Representative", SeatTitle("house", 34)),
        ("1st District State Representative", SeatTitle("house", 1)),
        ("2nd District State Representative", SeatTitle("house", 2)),
        ("3rd District State Representative", SeatTitle("house", 3)),
        # Generic no-district variants
        ("Senator", SeatTitle("senate", None)),
        ("Representative", SeatTitle("house", None)),
        # Whitespace tolerance
        ("  Senator, District 5  ", SeatTitle("senate", 5)),
    ],
)
def test_parse_legacy_title_seat_shaped(title: str, expected: SeatTitle) -> None:
    assert parse_legacy_title(title) == expected


@pytest.mark.parametrize(
    "title",
    [
        # Staff/leadership rows observed in production — must be excluded,
        # even when they mention "Senator"/"Rep." (anchoring matters).
        "Legislative Aide, Senator June Robinson",
        "Legislative Assistant to Senator Saldaña",
        "Legislative Assistant, Rep. Shelley Kloba",
        "Office of Program Research Director",
        "Secretary of the Senate",
        "Senate Committee Services Director",
        "Senior Policy Analyst",
        "Senior Policy Analyst, WA House COG",
        "Speaker of the House (2021-23)",
        # Adversarial near-misses
        "Senator, District",
        "Representative, District X",
        "State Senator of District 5",
        "",
    ],
)
def test_parse_legacy_title_excluded(title: str) -> None:
    assert parse_legacy_title(title) is None


# ---------------------------------------------------------------------------
# filer_id_from_url
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        # campaign-explorer candidate URL, double-space filer key
        (
            "https://www.pdc.wa.gov/browse/campaign-explorer/candidate"
            "?filer_id=CODYE%20%20126&election_year=2018",
            "CODYE  126",
        ),
        # contributions_download variant (Sam Hunt)
        (
            "https://www.pdc.wa.gov/reports/contributions_download"
            "?filer_id=HUNTS%20%20506&election_year=2020",
            "HUNTS  506",
        ),
        # bare numeric (person_wa_pdc style) — not a URL, no rescue
        ("463", None),
        # URL without filer_id param
        ("https://www.pdc.wa.gov/browse/campaign-explorer/candidate?election_year=2020", None),
        ("", None),
    ],
)
def test_filer_id_from_url(value: str, expected: str | None) -> None:
    assert filer_id_from_url(value) == expected
