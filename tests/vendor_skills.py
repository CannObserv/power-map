"""Visibility guard for the vendored skill submodules (#461 CR).

`skills-vendor/` is a git submodule, so it is **empty until someone initialises
it**. That is the default state of a freshly created worktree:
`scripts/worktree-setup.sh` builds the venv and links `.env`, and deliberately
leaves submodules alone. Tests that read vendored files therefore meet a routine
absence, not drift.

`tests/sh/claude_hooks.bats` met the same state and answered it with a
conditional `.skills/doctor.sh` heal plus a written rationale. The pytest tier
answers it the way #450 taught the repo to answer a vanished tier: skip the
tests that cannot run, then say so in red in the terminal summary. A silent skip
would let a green run overstate what it verified — which is the very shape of
bug the parity guard exists to catch, one level up.

Keep this module dependency-free and import-safe: `tests/conftest.py` imports it
at collection time, exactly as it imports `optional_groups`.
"""

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
VENDOR_ROOT = REPO_ROOT / "skills-vendor" / "gregoryfoster-skills"

# The probe is the file the parity guard actually reads, not the directory: an
# initialised-but-empty submodule directory exists and proves nothing.
DRIVER_PATH = VENDOR_ROOT / "skills" / "init-socraticode" / "scripts" / "mcp-driver.mjs"

INIT_HINT = "git submodule update --init skills-vendor/"

SKIP_REASON = (
    "skills-vendor/gregoryfoster-skills is uninitialised — the vendored-driver guards were NOT RUN"
)


def vendor_skills_present() -> bool:
    """True when the vendored `gregoryfoster/skills` submodule is checked out."""
    return DRIVER_PATH.is_file()


def absent_banner() -> str:
    """One-line terminal-summary banner naming the gap and the fix."""
    return f"{SKIP_REASON}; initialise with: {INIT_HINT}"
