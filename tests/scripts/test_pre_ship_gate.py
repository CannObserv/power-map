"""Guards `scripts/pre-ship.sh`, this repo's ship gate (#539).

`shipping-work-python-fastapi` Step 1 resolves `scripts/<name>.sh` from the repo
root **before** the skill directory, so a project-local copy is the sanctioned
tailoring point. This repo needs one because the vendored gate runs:

    uv run pytest $PYTEST_COV_FLAG -x -m "not integration"

and that `-m` **replaces** our `addopts` default of
`-m 'not integration and not browser'`. The browser tier is then *requested*,
and `tests/optional_groups.py` is right to refuse: with Playwright absent the
tier collects 0 tests and exits green, which is the vacuous pass #433 exists to
prevent. In a provisioned worktree the same invocation is worse than an exit 2 —
Playwright *is* installed there, so ~200 browser tests that need a live database,
a server and Chromium are pulled into the ship gate. The marker is hardcoded and
there is no env knob, so no amount of wrapping fixes it from outside.

**Why this is a copy and not a delegating wrapper.** The vendored script documents
a wrapper pattern, and it is the right shape for env loading — but it cannot reach
the pytest stage. The one seam that skips that stage is the per-SHA stamp, and it
requires a clean working tree (`-z "$WORKING_TREE_DIRTY"`), while Step 1 runs
*before* Step 2 ("ensure a clean working tree"). So on the run that matters the
delegate always re-runs pytest with its own marker.

A copy drifts, which is the cost the issue names. These assertions are what makes
it not drift silently:

* `test_every_vendored_stage_is_covered` — the vendored stage list is read back
  and each stage must appear here. A stage upstream adds fails this test rather
  than quietly not running on ship day.
* `test_the_divergence_is_still_necessary` — the vendored pytest line must still
  hardcode the marker. The day upstream takes a marker override, this fails and
  the right fix is to **delete** our copy and delegate. A local divergence should
  retire itself (#463), not outlive its reason.
* `test_our_invocation_matches_the_pre_commit_hook` — the ship gate and the
  pre-commit gate must run the same suite. The second divergence #539 names is
  that the vendored script omits `--group seed`, so it ran a narrower suite than
  our own hook even before the marker.
"""

import re
import subprocess
from pathlib import Path

import pytest

from tests import vendor_skills

REPO_ROOT = Path(__file__).parents[2]
LOCAL_GATE = REPO_ROOT / "scripts" / "pre-ship.sh"
PRE_COMMIT = REPO_ROOT / ".pre-commit-config.yaml"

VENDORED_GATE = (
    vendor_skills.VENDOR_ROOT
    / "skills"
    / "shipping-work-python-fastapi"
    / "scripts"
    / "pre-ship.sh"
)

# `echo "=== Lint (ruff) ==="` — the stage banners the vendored gate prints.
STAGE_BANNER = re.compile(r'echo\s+"===\s*(.+?)\s*==="')

RETIRE_HINT = (
    'The vendored pre-ship.sh no longer hardcodes `-m "not integration"`. That was the '
    "only reason scripts/pre-ship.sh exists: DELETE it and let Step 1 resolve the vendored "
    "gate again, rather than maintaining a copy whose reason has retired (#539, #463)."
)


@pytest.fixture(scope="module")
def local_source() -> str:
    return LOCAL_GATE.read_text()


@pytest.fixture(scope="module")
def vendored_source() -> str:
    if not VENDORED_GATE.is_file():
        pytest.skip(vendor_skills.SKIP_REASON)
    return VENDORED_GATE.read_text()


def test_the_local_gate_exists() -> None:
    """Without it Step 1 resolves the vendored gate, which exits 2 on this repo."""
    assert LOCAL_GATE.is_file(), f"missing {LOCAL_GATE.relative_to(REPO_ROOT)}"


def test_the_local_gate_is_executable_bash() -> None:
    """Step 1 invokes it as `bash <path>`, but a gate nobody can run directly is a trap."""
    source = LOCAL_GATE.read_text()
    assert source.startswith("#!/usr/bin/env bash"), "missing or wrong shebang"
    assert "set -euo pipefail" in source, "a ship gate must not continue past a failed stage"


def test_the_divergence_is_still_necessary(vendored_source: str) -> None:
    """The load-bearing ratchet: our reason to diverge must still be true.

    Asserted on the vendored *pytest invocation* rather than anywhere in the file,
    so an upstream comment mentioning the marker cannot keep this green.
    """
    # The real invocation, not the `echo` of it inside usage() — that one carries
    # the marker backslash-escaped and would keep this green on its own.
    invocation = next(
        (line for line in vendored_source.splitlines() if re.match(r"\s*uv run pytest\b", line)),
        None,
    )
    assert invocation is not None, (
        "the vendored gate no longer runs pytest at all — re-read it before trusting this copy"
    )
    assert '-m "not integration"' in invocation, RETIRE_HINT


def test_every_vendored_stage_is_covered(local_source: str, vendored_source: str) -> None:
    """A stage upstream adds must not silently stop running on ship day."""
    vendored_stages = set(STAGE_BANNER.findall(vendored_source))
    assert vendored_stages, "found no stage banners upstream — re-anchor STAGE_BANNER"
    local_stages = set(STAGE_BANNER.findall(local_source))
    missing = vendored_stages - local_stages
    assert not missing, (
        f"the vendored gate runs stages this copy does not: {sorted(missing)}. Add them here, "
        "or (if upstream dropped the reason for this copy) delete the copy."
    )


def test_our_invocation_matches_the_pre_commit_hook(local_source: str) -> None:
    """One suite definition, two gates. #539's second divergence was `--group seed`."""
    hook = next(
        (
            line.strip()
            for line in PRE_COMMIT.read_text().splitlines()
            if "pytest" in line and "entry:" in line
        ),
        None,
    )
    assert hook is not None, "no pytest entry found in .pre-commit-config.yaml"
    assert "--group seed" in hook, "the pre-commit hook no longer passes --group seed"
    assert "--group seed" in local_source, (
        "scripts/pre-ship.sh must pass --group seed, matching the pre-commit `pytest (unit)` hook; "
        "the vendored gate omits it and so ran a narrower suite than our own gate"
    )


def test_it_does_not_override_the_marker(local_source: str) -> None:
    """The fix itself: `addopts` supplies the marker, so the gate must not pass `-m`.

    Any `-m` on the command line replaces the ini default wholesale — that is the
    entire defect. Re-adding one here, even the full expression, re-creates the
    class of bug (the two would then have to be kept in sync by memory).
    """
    # Executable lines only. This file quotes the vendored invocation in its own
    # comments and in --help, precisely to explain the divergence.
    pytest_lines = [
        line for line in local_source.splitlines() if re.match(r"\s*uv run .*\bpytest\b", line)
    ]
    assert pytest_lines, "the gate does not run pytest"
    for line in pytest_lines:
        assert " -m " not in line, (
            f"{line.strip()!r} passes -m, which replaces the addopts marker wholesale. "
            "Let pyproject.toml's addopts supply it."
        )


def test_the_help_flag_works() -> None:
    """A gate that cannot explain itself gets invoked wrongly under pressure."""
    proc = subprocess.run(
        ["bash", str(LOCAL_GATE), "--help"],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
    )
    assert proc.returncode == 0, f"--help exited {proc.returncode}: {proc.stderr}"
    assert "pre-ship" in proc.stdout.lower()
    assert "Exit codes:" in proc.stdout
