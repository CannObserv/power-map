"""Guards for the optional dependency groups (#450).

`uv sync` is *exact*: it prunes every group that isn't requested, so a bare
`uv sync` (prod's `power-map.service` ExecStartPre, or a habitual dev
invocation) silently removes `browser` and `seed` from the environment. The
test modules for those groups `importorskip` at module scope, so the loss
shows up as a couple of skips and a green suite — a pass that proves strictly
less than it did, with nothing to point at.

These tests lock the two guards that make that loss loud: an explicit
`-m browser` run with Playwright absent must abort, and any run missing an
optional group must say so in the terminal summary.
"""

import tomllib
from pathlib import Path

import pytest

from tests import optional_groups as og

PYPROJECT = Path(__file__).resolve().parents[1] / "pyproject.toml"


# --- registry drift ---------------------------------------------------------


def test_registry_covers_every_non_default_dependency_group():
    """A new opt-in group must register an import probe, or it can vanish silently."""
    declared = set(tomllib.loads(PYPROJECT.read_text())["dependency-groups"])
    assert set(og.OPTIONAL_GROUPS) == declared - {"dev"}


def test_every_registered_group_probes_at_least_one_module():
    assert all(modules for modules in og.OPTIONAL_GROUPS.values())


# --- marker-expression reading ----------------------------------------------


@pytest.mark.parametrize(
    "markexpr,expected",
    [
        ("", False),
        ("   ", False),
        ("not integration and not browser", False),  # the default addopts
        ("integration", False),
        ("browser", True),
        ("browser and not integration", True),
        ("browser or integration", True),
        ("not browser", False),
    ],
)
def test_browser_tier_requested(markexpr, expected):
    assert og.browser_tier_requested(markexpr) is expected


def test_unparseable_marker_expression_does_not_request_the_tier():
    """A malformed -m is pytest's error to report, not ours to abort on."""
    assert og.browser_tier_requested("browser and and") is False


# --- the abort guard --------------------------------------------------------


def test_guard_aborts_when_browser_requested_and_playwright_absent():
    reason = og.browser_guard_reason("browser", has_module=lambda name: False)
    assert reason is not None
    assert "playwright" in reason
    assert "--group browser" in reason


def test_guard_is_silent_when_playwright_is_installed():
    assert og.browser_guard_reason("browser", has_module=lambda name: True) is None


def test_guard_is_silent_when_the_tier_was_not_requested():
    assert (
        og.browser_guard_reason("not integration and not browser", has_module=lambda name: False)
        is None
    )


# --- the terminal-summary banner --------------------------------------------


def test_missing_optional_groups_reports_each_absent_group():
    missing = og.missing_optional_groups(has_module=lambda name: False)
    assert set(missing) == set(og.OPTIONAL_GROUPS)


def test_missing_optional_groups_is_empty_when_all_present():
    assert og.missing_optional_groups(has_module=lambda name: True) == {}


def test_banner_names_the_groups_and_the_remedy():
    banner = og.missing_groups_banner({"browser": ["playwright"]})
    assert "browser" in banner
    assert "--group browser" in banner
    assert "NOT RUN" in banner


def test_banner_lists_every_missing_group_in_one_line():
    banner = og.missing_groups_banner({"browser": ["playwright"], "seed": ["langcodes"]})
    assert "browser" in banner and "seed" in banner
    assert "\n" not in banner
