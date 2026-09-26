"""Every skill Claude Code can discover here is listed in `docs/SKILLS.md` (#564).

A vendored skill is wired in by hand: `managing-skills` Step 2/2b symlinks it
into `skills/` and `.claude/skills/`, and the daily auto-refresh hook bumps the
submodule pointer but never adds a link. The `## Available Skills` table is the
only place a reader learns which skills exist and what triggers them, so a link
that lands without its row is a skill nobody knows to ask for — and a row whose
link was removed advertises one that no longer loads.

Names only: `os.listdir` sees a symlink whether or not the submodule behind it
is initialised, so this guard runs in a fresh worktree too. Dangling links are
`.skills/doctor.sh`'s job.
"""

import os
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DISCOVERY_DIR = REPO_ROOT / ".claude" / "skills"
SKILLS_DOC = REPO_ROOT / "docs" / "SKILLS.md"

_TABLE_ROW = re.compile(r"^\| `([a-z0-9-]+)` \|", re.MULTILINE)


def _discoverable() -> set[str]:
    """Skill names linked into Claude Code's discovery path."""
    return set(os.listdir(DISCOVERY_DIR))


def _listed() -> set[str]:
    """Skill names in the `## Available Skills` table of `docs/SKILLS.md`."""
    text = SKILLS_DOC.read_text(encoding="utf-8")
    section = text.split("## Available Skills", 1)[1].split("\n## ", 1)[0]
    return set(_TABLE_ROW.findall(section))


class TestSkillsListing:
    def test_the_table_parses(self) -> None:
        assert len(_listed()) >= 10, "the Available Skills table no longer parses"

    def test_every_discoverable_skill_is_listed(self) -> None:
        missing = sorted(_discoverable() - _listed())
        assert not missing, (
            "linked in .claude/skills/ but absent from docs/SKILLS.md "
            f"§ Available Skills: {missing}"
        )

    def test_every_listed_skill_is_discoverable(self) -> None:
        stale = sorted(_listed() - _discoverable())
        assert not stale, (
            "listed in docs/SKILLS.md § Available Skills but not linked "
            f"in .claude/skills/: {stale}"
        )
