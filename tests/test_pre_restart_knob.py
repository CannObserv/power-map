"""`.skills/pre_restart` names #748's check for `shipping-work` Step 8 (#760).

gregoryfoster/skills#557 added the knob: when set, Step 8 runs the named check
through `run-pre-restart.sh` before offering any row that restarts the
service, and a non-zero exit offers none. That puts
`scripts/pre-restart-check.sh` on the skill's own restart path instead of on
the agent's memory of the runbook.

The skill reads the knob from the **primary** checkout, so from a worktree
`run-pre-restart.sh --check` answers about `main`, not this branch. The seam
test below runs the vendored reader against a throwaway repo carrying this
branch's knob and script instead, so a grammar change upstream or a knob that
stops parsing reds here rather than at the next restart.
"""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from tests import vendor_skills

# Reads the vendored tree: runs on a skills-vendor/ bump's commit too (#751).
pytestmark = pytest.mark.vendored

REPO_ROOT = Path(__file__).resolve().parent.parent
KNOB = REPO_ROOT / ".skills" / "pre_restart"
CHECK_SCRIPT = "scripts/pre-restart-check.sh"
RUNNER = REPO_ROOT / "skills" / "shipping-work" / "scripts" / "run-pre-restart.sh"

# `#557 feat: .skills/pre_restart runs a reachability check before Step 8's
# restart rows` — the commit that made the knob exist.
KNOB_COMMIT = "04c4bc87"

vendor_only = pytest.mark.skipif(
    not vendor_skills.vendor_skills_present(),
    reason=vendor_skills.SKIP_REASON,
)


def _git_env() -> dict[str, str]:
    """The environment with `GIT_*` scrubbed: pre-commit's `GIT_DIR` beats `-C`."""
    return {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}


def _git(*args: str, cwd: Path) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, check=True, env=_git_env()
    ).stdout


def test_the_knob_names_the_pre_restart_check() -> None:
    """One command line, the repo script by its path, no flags.

    The no-flag form fails closed: its exit 3 (database unreachable) and exit 1
    (probe could not run) both become a refusal. `--force` stays a human's call.
    """
    assert KNOB.is_file(), f"missing {KNOB.relative_to(REPO_ROOT)}"
    lines = [line for line in KNOB.read_text().splitlines() if line.strip()]
    assert lines == [CHECK_SCRIPT], f".skills/pre_restart should be exactly {CHECK_SCRIPT!r}"


def test_the_named_script_is_tracked_executable() -> None:
    """The knob's grammar requires an executable repo script, and git's mode is what ships."""
    staged = _git("ls-files", "-s", "--", CHECK_SCRIPT, cwd=REPO_ROOT).split()
    assert staged, f"{CHECK_SCRIPT} is not tracked"
    assert staged[0] == "100755", f"{CHECK_SCRIPT} is tracked as {staged[0]}, not 100755"


@vendor_only
def test_the_pin_carries_the_knob() -> None:
    """Without skills#557 the knob is an inert file, and nothing reds."""
    contains = vendor_skills.contains_commit(KNOB_COMMIT)
    if contains is None:
        pytest.skip(f"git cannot resolve {KNOB_COMMIT}; ancestry unverifiable")
    assert contains, (
        f"the vendored gregoryfoster/skills pin does not contain {KNOB_COMMIT} "
        "(skills#557), so shipping-work never reads .skills/pre_restart. Bump "
        "skills-vendor/gregoryfoster-skills forward."
    )


@vendor_only
def test_the_vendored_reader_accepts_the_knob(tmp_path: Path) -> None:
    """`run-pre-restart.sh --check` parses this branch's knob and prints its line.

    A throwaway primary checkout, because the reader resolves the first
    `git worktree list` entry, and from a worktree that is `main`.
    """
    (tmp_path / ".skills").mkdir()
    (tmp_path / "scripts").mkdir()
    shutil.copy2(KNOB, tmp_path / ".skills" / "pre_restart")
    shutil.copy2(REPO_ROOT / CHECK_SCRIPT, tmp_path / CHECK_SCRIPT)
    _git("init", "-q", cwd=tmp_path)

    proc = subprocess.run(
        ["bash", str(RUNNER), "--check"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        env=_git_env(),
        stdin=subprocess.DEVNULL,
    )
    assert proc.returncode == 0, f"exit {proc.returncode}\n{proc.stdout}{proc.stderr}"
    assert CHECK_SCRIPT in proc.stdout, proc.stdout
