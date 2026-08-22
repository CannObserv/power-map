"""Guards the vendored-submodule visibility helper (#461 CR).

`tests/vendor_skills.py` exists so a suite that cannot see `skills-vendor/`
*says so* instead of skipping quietly. The state is routine, not exotic: a
freshly created worktree has uninitialized submodules until someone runs
`git submodule update --init` (`scripts/worktree-setup.sh` does the venv and
`.env`, not the submodules), and `tests/sh/claude_hooks.bats` already carries a
written rationale for meeting the same state next door.

The failure this prevents is the one #450 taught the repo with dependency
groups: a tier that vanishes reads as a few skips against an otherwise green
run, and the green overstates what was verified. Same shape, same answer — skip
the tests, then name the gap in red in the terminal summary.
"""

from pathlib import Path

from tests import vendor_skills

REPO_ROOT = Path(__file__).resolve().parent.parent


def test_driver_path_points_into_the_vendored_skill() -> None:
    """The probe targets `mcp-driver.mjs`, the file the parity guard reads."""
    rel = vendor_skills.DRIVER_PATH.relative_to(REPO_ROOT).as_posix()
    assert rel == (
        "skills-vendor/gregoryfoster-skills/skills/init-socraticode/scripts/mcp-driver.mjs"
    ), f"probe points at {rel}, not the vendored driver"


def test_present_is_true_when_the_submodule_is_initialised() -> None:
    """In a working checkout the probe resolves, so nothing is skipped."""
    assert vendor_skills.DRIVER_PATH.is_file(), (
        "the vendored driver is missing — this test asserts the happy path, so "
        "run `git submodule update --init skills-vendor/` first"
    )
    assert vendor_skills.vendor_skills_present() is True


def test_absent_banner_names_the_gap_and_the_fix() -> None:
    """The banner says the tests did NOT run, and how to make them run."""
    banner = vendor_skills.absent_banner()

    assert "NOT RUN" in banner, (
        "the banner must say the tests did not run — a skip that reads as a "
        "pass is the whole failure mode this exists to prevent"
    )
    assert vendor_skills.INIT_HINT in banner, "the banner must carry the init command"
    assert "git submodule update --init" in vendor_skills.INIT_HINT


def test_skip_reason_is_the_same_sentence_as_the_banner() -> None:
    """One wording, whether it surfaces via `-rs` or the terminal summary."""
    assert vendor_skills.SKIP_REASON in vendor_skills.absent_banner()
