"""Guards for the optional dependency groups (#450).

`uv sync` is *exact*: it prunes every group that isn't requested, so a bare
`uv sync` (prod's `power-map.service` ExecStartPre, or a habitual dev
invocation) silently removes `browser` and `seed` from the environment. The
test modules for those groups `importorskip` at module scope, so the loss
shows up as a couple of skips and a green suite — a pass that proves strictly
less than it did, with nothing to point at.

These tests lock the two guards that make that loss loud: an explicit
`-m browser` run with Playwright absent must abort, and any run missing an
optional group must say so in the terminal summary. A third (#545) locks the
premise both rest on: that a pruned environment *skips* those modules at all,
rather than failing to collect them.
"""

import subprocess
import sys
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


@pytest.mark.parametrize(
    "markexpr,expected",
    [
        ("", False),
        ("not integration and not browser", False),
        ("browser", True),
        ("not browser", False),
        ("integration", False),
    ],
)
def test_fallback_reader_handles_the_shapes_this_repo_uses(markexpr, expected):
    """The fallback runs only if pytest moves its expression parser."""
    assert og._markexpr_mentions_browser(markexpr) is expected


def test_browser_tier_requested_uses_the_fallback_when_the_parser_is_gone(monkeypatch):
    monkeypatch.setattr(og, "Expression", None)
    assert og.browser_tier_requested("browser") is True
    assert og.browser_tier_requested("not integration and not browser") is False


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


def test_guard_survives_a_renamed_browser_group(monkeypatch):
    """The ratchet test cannot report a rename if collection never starts."""
    monkeypatch.setattr(og, "OPTIONAL_GROUPS", {"web": ["playwright"]})
    assert og.browser_guard_reason("browser", has_module=lambda name: False) is None


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


def test_install_hint_names_every_registered_group():
    """A group the hint omits is one the banner tells you to install incompletely."""
    for group in og.OPTIONAL_GROUPS:
        assert f"--group {group}" in og.INSTALL_HINT


# --- collection with every optional group absent (#545) ---------------------

# A `None` entry in sys.modules makes `import <name>` (and every submodule
# import under it) raise ImportError, exactly as an uninstalled package does —
# and `importlib.util.find_spec` reads it as absent, so the banner fires too.
_COLLECT_WITH_BLOCKED_PACKAGES = """
import sys
for name in sys.argv[1].split(","):
    sys.modules[name] = None
import pytest
sys.exit(pytest.main(["--collect-only", "-q", "--no-cov", "-p", "no:cacheprovider", "tests"]))
"""


def test_unit_suite_collects_with_every_optional_group_absent():
    """Prod's venv — pruned by ExecStartPre's exact `uv sync` — must still collect.

    A module that imports an optional group's package at module scope without
    `importorskip` is a collection *error*, not a skip, and the pre-commit unit
    hook (`-x`) stops on it: every commit made in the main checkout failed on
    `test_applier_merge_registry.py` importing dbt, while every worktree — whose
    venv carries every group — passed. Blocking each group's root package in a
    fresh interpreter reproduces the pruned environment wherever this runs.
    """
    roots = sorted(
        {probe.split(".")[0] for probes in og.OPTIONAL_GROUPS.values() for probe in probes}
    )
    result = subprocess.run(
        [sys.executable, "-c", _COLLECT_WITH_BLOCKED_PACKAGES, ",".join(roots)],
        cwd=PYPROJECT.parent,
        capture_output=True,
        text=True,
        timeout=300,
    )
    output = result.stdout + result.stderr

    assert "NOT RUN" in output, "the packages were not blocked — nothing was simulated"
    errors = [line for line in output.splitlines() if line.startswith("ERROR ")]
    assert errors == []
    assert result.returncode == 0, output[-3000:]
