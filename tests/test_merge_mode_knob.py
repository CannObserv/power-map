"""`.skills/merge_mode` is `pr`: work reaches `main` only through a merged PR.

The operator's decision, 2026-11-09: no local merge into the main checkout,
which is production's `WorkingDirectory=`, so a local merge there is a deploy.
gregoryfoster/skills#342 added the knob. `shipping-work` and
`using-git-worktrees` read it to open a PR instead of merging, and the skills
refresh hook (#560) proposes its daily submodule bump as one PR from
`skills/submodule-refresh` instead of pushing to `main`.

The skills read the knob from the **primary** checkout, so from a worktree
`merge-mode.sh` answers about `main`, not this branch. The seam test runs the
vendored reader against a throwaway repo carrying this branch's knob, so a
grammar change upstream reds here rather than at the next ship.
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
KNOB = REPO_ROOT / ".skills" / "merge_mode"
READERS = [
    REPO_ROOT / "skills" / "shipping-work" / "scripts" / "merge-mode.sh",
    REPO_ROOT / "skills" / "using-git-worktrees" / "scripts" / "merge-mode.sh",
]

vendor_only = pytest.mark.skipif(
    not vendor_skills.vendor_skills_present(),
    reason=vendor_skills.SKIP_REASON,
)


def _git_env() -> dict[str, str]:
    """The environment with `GIT_*` scrubbed: pre-commit's `GIT_DIR` beats `-C`."""
    return {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}


def test_the_knob_is_pr() -> None:
    """One word, `pr`; blank lines and whole-line `#` comments are the grammar's only extras."""
    assert KNOB.is_file(), f"missing {KNOB.relative_to(REPO_ROOT)}"
    words = [
        word
        for line in KNOB.read_text().splitlines()
        if line.strip() and not line.lstrip().startswith("#")
        for word in line.split()
    ]
    assert words == ["pr"], f".skills/merge_mode should read exactly 'pr', got {words!r}"


@vendor_only
@pytest.mark.parametrize("reader", READERS, ids=lambda p: p.parts[-3])
def test_the_vendored_reader_answers_pr(reader: Path, tmp_path: Path) -> None:
    """Each skill's `merge-mode.sh` parses this branch's knob and prints `pr`.

    A throwaway primary checkout, because the reader resolves the first
    `git worktree list` entry, and from a worktree that is `main`.
    """
    (tmp_path / ".skills").mkdir()
    shutil.copy2(KNOB, tmp_path / ".skills" / "merge_mode")
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True, env=_git_env())

    proc = subprocess.run(
        ["bash", str(reader)],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        env=_git_env(),
        stdin=subprocess.DEVNULL,
    )
    assert proc.returncode == 0, f"exit {proc.returncode}\n{proc.stdout}{proc.stderr}"
    assert proc.stdout.strip() == "pr", proc.stdout
