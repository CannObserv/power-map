"""Nothing live names a skill its vendor has retired (#739).

Upstream folds skills and says so in a `retired-skills` map at the vendor's
root (gregoryfoster/skills#537): `shipping-work` replaced the per-stack
`shipping-work-*` variants. `.skills/doctor.sh` reads that map and repoints the
`skills/` and `.claude/skills/` links, but it holds the link only while a
tracked file names the old **slash path** (`skills/<old>/`). Two tests here
built that path from parts (`VENDOR_ROOT / "skills" / "<old>" / …`), which the
doctor's hold cannot see. Release 1 keeps the deprecated variants in the vendor
tree, so such a reference passes; release 2 deletes them, and the reference
then skips or errors at the next bump instead of here.

So the sweep matches the bare **name**, in every regular file git tracks (or
would track), this one included, with two exemptions:

- **History.** `docs/plans/` and `docs/archive/` record what was decided and
  done. Rewriting them would falsify the record.
- **The doctor.** `.skills/doctor.sh` is a vendored copy, synced byte for byte,
  and the doctor's own hold excludes it for the same reason.

`RETIRED` is spelled here rather than read from the map, so the sweep runs in a
worktree whose submodule is uninitialised. `test_retired_covers_the_vendor_map`
keeps the copy honest: an upstream retirement this list lacks reds, which is
the moment to repoint its references and add it.
"""

import os
import re
import subprocess
from pathlib import Path

import pytest

from tests import vendor_skills

# Reads the vendored tree: runs on a skills-vendor/ bump's commit too (#751).
pytestmark = pytest.mark.vendored

REPO_ROOT = Path(__file__).resolve().parent.parent
RETIRED_MAP = vendor_skills.VENDOR_ROOT / "retired-skills"
DISCOVERY_DIRS = (REPO_ROOT / "skills", REPO_ROOT / ".claude" / "skills")

#: Assembled at runtime so this file's own source never spells a name whole —
#: which is what lets the sweep scan this file like any other.
RETIRED = frozenset("shipping-work-" + stack for stack in ("python-fastapi", "python-click", "php"))

#: A retired name as a whole skill name: not a prefix of a longer one.
_RETIRED_RE = re.compile(
    "|".join(rf"(?<![\w-]){re.escape(name)}(?![\w-])" for name in sorted(RETIRED))
)

EXEMPT_PREFIXES = ("docs/plans/", "docs/archive/")
EXEMPT_FILES = frozenset({".skills/doctor.sh"})


def _git_paths() -> list[str]:
    """Repo-relative paths git tracks or would track."""
    out = subprocess.run(
        ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
        cwd=REPO_ROOT,
        capture_output=True,
        check=True,
    )
    return sorted({p for p in out.stdout.decode().split("\0") if p})


def _candidate_files() -> list[str]:
    """Regular files among `_git_paths()` (submodules and symlinks excluded)."""
    return [
        r for r in _git_paths() if (REPO_ROOT / r).is_file() and not (REPO_ROOT / r).is_symlink()
    ]


def _exempt(rel: str) -> bool:
    return rel in EXEMPT_FILES or rel.startswith(EXEMPT_PREFIXES)


def offending_lines(text: str) -> list[int]:
    """1-based numbers of the lines that name a retired skill."""
    return [n for n, line in enumerate(text.splitlines(), 1) if _RETIRED_RE.search(line)]


def _symlinks() -> list[str]:
    """The symlinks among `_git_paths()`."""
    return [r for r in _git_paths() if (REPO_ROOT / r).is_symlink()]


def parse_retired_map(text: str) -> dict[str, str]:
    """The `<old> <new>` pairs of a `retired-skills` map; `#` starts a comment."""
    pairs = {}
    for n, line in enumerate(text.splitlines(), 1):
        fields = line.split("#", 1)[0].split()
        if not fields:
            continue
        if len(fields) != 2:
            raise ValueError(f"line {n} is not an '<old> <new>' pair: {line!r}")
        old, new = fields
        pairs[old] = new
    return pairs


def test_no_live_reference_names_a_retired_skill():
    """No regular file outside the exemptions names a retired skill."""
    hits = {}
    for rel in _candidate_files():
        if _exempt(rel):
            continue
        try:
            text = (REPO_ROOT / rel).read_text()
        except UnicodeDecodeError:
            continue
        if lines := offending_lines(text):
            hits[rel] = lines
    assert hits == {}, f"live references to a retired skill (#739): {hits}"


def test_no_discovery_entry_is_a_retired_skill():
    """The doctor's repoint landed: no `skills/<old>` or `.claude/skills/<old>` link."""
    stale = sorted(
        f"{d.relative_to(REPO_ROOT)}/{name}"
        for d in DISCOVERY_DIRS
        for name in os.listdir(d)
        if name in RETIRED
    )
    assert stale == [], f"retired skills still linked (run .skills/doctor.sh): {stale}"


def test_no_symlink_targets_a_retired_skill():
    """The content sweep skips links, so their targets are read here.

    A per-file link such as `.claude/hooks/<x>.sh -> ../../skills-vendor/…/skills/<old>/…`
    dangles the day release 2 deletes `<old>`, and neither the name sweep nor the
    discovery check above would see it.
    """
    stale = {
        rel: target
        for rel in _symlinks()
        if _RETIRED_RE.search(target := os.readlink(REPO_ROOT / rel))
    }
    assert stale == {}, f"symlinks into a retired skill (#739): {stale}"


@pytest.mark.skipif(
    not vendor_skills.vendor_skills_present(),
    reason=vendor_skills.SKIP_REASON,
)
def test_retired_covers_the_vendor_map():
    """Every retirement upstream's map declares is one `RETIRED` sweeps for."""
    mapped = set(parse_retired_map(RETIRED_MAP.read_text()))
    assert mapped, f"{RETIRED_MAP.name} parsed to nothing; re-read its grammar"
    assert mapped <= RETIRED, (
        f"upstream retired {sorted(mapped - RETIRED)}: repoint their references, "
        "then add them to RETIRED"
    )


# --- detector self-tests ------------------------------------------------------


def test_the_walk_reaches_the_tree():
    """The walk sees the repo, this file included, and the link walk sees links."""
    files = _candidate_files()
    assert "AGENTS.md" in files and "tests/test_retired_skill_refs.py" in files
    assert len(files) > 500
    assert "skills/shipping-work" in _symlinks()


def test_the_detector_is_live():
    """A positive control, built at runtime so this file never holds one."""
    name = "shipping-work-" + "python-fastapi"
    assert offending_lines(f"ok\nVENDOR / {name!r} / 'scripts'\n") == [2]
    assert offending_lines(f"bash skills/{name}/scripts/pre-ship.sh") == [1]
    assert offending_lines("bash skills/shipping-work/scripts/pre-ship.sh") == []
    assert offending_lines(f"{name}-next is another skill") == []


def test_the_map_parser_reads_pairs_and_drops_comments():
    """Pairs parse; comments and blank lines drop; a malformed line raises, naming it."""
    text = "# header\n\nold-a  new  # trailing\nold-b new\n"
    assert parse_retired_map(text) == {"old-a": "new", "old-b": "new"}
    with pytest.raises(ValueError, match="line 2"):
        parse_retired_map("old-a new\nold-b new extra\n")
