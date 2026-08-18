"""Visibility guards for the opt-in dependency groups (#450).

`uv run` is *inexact* — it installs what the invocation asks for and leaves
everything else alone. `uv sync` is *exact* — it removes every group not
requested. So a bare `uv sync` anywhere (prod's `power-map.service`
ExecStartPre included) strips `browser` and `seed` from the environment, and
because their test modules `importorskip` at module scope, the loss reads as a
few skips against an otherwise green suite.

Two guards close that gap:

* `browser_guard_reason` — asking for the tier (`-m browser`) without
  Playwright installed is an error, not a `0 selected` no-op.
* `missing_optional_groups` / `missing_groups_banner` — any run missing a group
  says so in the terminal summary, so a pass never overstates its coverage.

Both are wired in `tests/conftest.py`. Keep them dependency-free: this module
is imported at `pytest_configure` time, before any optional package exists.
"""

import importlib.util

from _pytest.mark.expression import Expression

# group name → modules that prove it is installed. A group with no probe could
# vanish unnoticed, so `test_optional_groups.py` ratchets this against
# pyproject's `[dependency-groups]`.
OPTIONAL_GROUPS: dict[str, list[str]] = {
    "browser": ["playwright"],
    "seed": ["langcodes", "pycountry"],
}

INSTALL_HINT = "uv sync --group browser --group seed"


def _has_module(name: str) -> bool:
    """True when `name` is importable without importing it."""
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError):
        return False


def browser_tier_requested(markexpr: str) -> bool:
    """True when `markexpr` would select a test carrying only the browser marker.

    Evaluated with pytest's own expression parser rather than a substring
    match, so the default `not integration and not browser` reads as False and
    `browser or integration` as True. An unparseable expression is pytest's
    error to report — never ours to abort on.
    """
    if not markexpr.strip():
        return False
    try:
        return bool(Expression.compile(markexpr).evaluate(lambda name: name == "browser"))
    except Exception:
        return False


def browser_guard_reason(markexpr: str, has_module=None) -> str | None:
    """Abort reason when the browser tier is requested but uninstallable, else None.

    Without this, `-m browser` against a pruned environment collects nothing,
    skips the three modules and exits 0 — a vacuous pass. `run-a11y-sweep.sh`
    already holds this line for Chromium; this holds it for the wheel.
    """
    has_module = has_module or _has_module
    if not browser_tier_requested(markexpr):
        return None
    missing = [name for name in OPTIONAL_GROUPS["browser"] if not has_module(name)]
    if not missing:
        return None
    return (
        f"browser tier requested but {', '.join(missing)} is absent — "
        f"the tier would collect 0 tests and exit green. "
        f"Run: uv run --group browser --group seed pytest ... "
        f"(one-time: {INSTALL_HINT} && uv run --group browser playwright install chromium)"
    )


def missing_optional_groups(has_module=None) -> dict[str, list[str]]:
    """Map each absent optional group to the probe modules that are missing."""
    has_module = has_module or _has_module
    missing = {}
    for group, modules in OPTIONAL_GROUPS.items():
        absent = [name for name in modules if not has_module(name)]
        if absent:
            missing[group] = absent
    return missing


def missing_groups_banner(missing: dict[str, list[str]]) -> str:
    """One-line terminal-summary banner naming the tiers this run did not cover."""
    groups = ", ".join(sorted(missing))
    return (
        f"optional groups absent ({groups}) — those tests were NOT RUN; "
        f"install with: {INSTALL_HINT}"
    )
