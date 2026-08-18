"""Unit tests: canonical WA legislative role-title synthesis (#267).

Single source of truth for a role's curated title, shared by the seed generator
(scripts/generate_wa_roles.py) and the observation resolver (resolve_role) so PM
owns the title and it never drifts from an upstream-supplied label.
"""

import pytest

from src.core.role_title import (
    ld_number_from_slug,
    synthesize_role_title,
    wa_legislative_role_title,
)


@pytest.mark.parametrize(
    "slug,expected",
    [
        ("usa-wa-ld-1", 1),
        ("usa-wa-ld-42", 42),
        ("usa-wa-ld-49", 49),
        ("usa-wa-cd-1", None),  # congressional district, not legislative
        ("ld-1", None),
        ("usa-wa-ld-", None),
        ("", None),
        (None, None),
    ],
)
def test_ld_number_from_slug(slug, expected):
    assert ld_number_from_slug(slug) == expected


def test_senator_title_no_qualifier():
    assert wa_legislative_role_title("state_senator", 1, None) == "Washington State Senator, LD-1"


def test_representative_title_with_position():
    assert (
        wa_legislative_role_title("state_representative", 5, "Position 1")
        == "Washington State Representative, LD-5, Position 1"
    )
    assert (
        wa_legislative_role_title("state_representative", 5, "Position 2")
        == "Washington State Representative, LD-5, Position 2"
    )


def test_at_large_title_no_qualifier():
    """A pre-1965 at-large seat renders without a position (#302).

    The at-large era had no Position designation, so the qualifier is NULL and
    the label itself has to carry "at-large" — otherwise the title is
    indistinguishable from a positioned seat whose position went missing.
    """
    assert (
        wa_legislative_role_title("state_representative_at_large", 5, None)
        == "Washington State Representative (At-Large), LD-5"
    )


def test_synthesize_at_large_from_slug():
    """usa-wa reuses the current district rows, so the modern slug form parses."""
    assert (
        synthesize_role_title("state_representative_at_large", "usa-wa-ld-11", None)
        == "Washington State Representative (At-Large), LD-11"
    )


def test_empty_qualifier_adds_no_suffix():
    assert wa_legislative_role_title("state_senator", 3, "") == "Washington State Senator, LD-3"


def test_unknown_role_type_returns_none():
    assert wa_legislative_role_title("us_representative", 7, None) is None


def test_missing_ld_number_returns_none():
    assert wa_legislative_role_title("state_senator", None, None) is None


def test_synthesize_from_slug():
    assert (
        synthesize_role_title("state_senator", "usa-wa-ld-3", None)
        == "Washington State Senator, LD-3"
    )
    assert (
        synthesize_role_title("state_representative", "usa-wa-ld-3", "Position 2")
        == "Washington State Representative, LD-3, Position 2"
    )


def test_synthesize_bad_slug_returns_none():
    assert synthesize_role_title("state_senator", "usa-wa-cd-3", None) is None
    assert synthesize_role_title("state_senator", "", None) is None
