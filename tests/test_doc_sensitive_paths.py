"""Guards `.skills/doc-sensitive-paths`, this repo's sensitive-path list (#488).

`shipping-work-python-fastapi`'s `doc-check.sh` (Step 1.5) flags branch changes
that touch files whose existence, names or structure the docs inventory, so the
matching doc sections get a look before shipping. Upstream skills#252 changed it
in two ways that make a committed list worth having here:

1. Entries now match whole path **segments** rather than being anchored at the
   start of the path. The generosity is deliberate — it also reaches vendored,
   generated and test trees — because for a spot-check that exits 1 and asks a
   human to look, over-matching is the cheap failure and under-matching is the
   one that shipped as a clean green.
2. A list where **no** entry matches any tracked file now exits 2, and a list
   with *some* dead entries prints them by name on the green path.

Five of the twelve built-in defaults are dead in this tree: `CHANGELOG.md`,
`alembic/versions/`, `deploy/`, `src/models/` and `.env.example` describe a
layout this service does not have (no changelog, no Alembic — `src/core/schema.sql`
plus idempotent reconciliation blocks — no ORM model package, deployment under
`infra/`, environment files under `/etc/power-map/` and a gitignored `.env`).
`.skills/doc-sensitive-paths` replaces the defaults wholesale with the real
layout.

**The list is the thing under test, not the matcher.** `path_matches` below
mirrors the vendored bash so an entry can be checked against the tree the way
the gate will check it; the assertions are all about our list. The mirror is a
hand-copy, so `TestTheMirrorStillDescribesTheGate` reads the vendored source
back — ancestry proves the pin carries skills#252, not that the semantics have
held still since. Two failure modes
they close, both of which read as a pass in the gate itself:

- **A dead entry.** It cannot contribute to any verdict, so it is a line of the
  list that silently does nothing — exactly the state the issue asked us to look
  at, one directory rename away from returning.
- **A dropped tree.** Nothing about a green Step 1.5 says which trees it was
  watching, so a deleted entry costs coverage invisibly. `REPRESENTATIVE_PATHS`
  is the ratchet: one real file per documented tree, each of which must stay
  matched.
"""

import os
import subprocess
from pathlib import Path

import pytest

from tests import vendor_skills

REPO_ROOT = Path(__file__).resolve().parent.parent
LIST_PATH = REPO_ROOT / ".skills" / "doc-sensitive-paths"

# The upstream commit that shipped segment matching + the dead-list exit 2
# (gregoryfoster/skills#252). Below it, `doc-check.sh` never reads this file:
# our list is ignored in silence and the defaults run instead.
SEGMENT_MATCH_COMMIT = "662de71"

DOC_CHECK_PATH = (
    vendor_skills.VENDOR_ROOT
    / "skills"
    / "shipping-work-python-fastapi"
    / "scripts"
    / "doc-check.sh"
)

ROLLBACK_HINT = (
    f"The vendored gregoryfoster/skills pin does not contain {SEGMENT_MATCH_COMMIT} "
    "(skills#252), so doc-check.sh does not read .skills/doc-sensitive-paths at "
    "all — it silently runs its built-in defaults instead, five of which match "
    "nothing in this tree. Bump skills-vendor/gregoryfoster-skills forward."
)

# One tracked file per tree the docs inventory by name. A green Step 1.5 never
# says what it was watching, so coverage can only be asserted here.
REPRESENTATIVE_PATHS = (
    "AGENTS.md",  # the agent-context surface itself (docs/CONTEXT.md)
    "README.md",  # orientation + curated links
    "pyproject.toml",  # version-sync pair with package.json
    "package.json",
    "uv.lock",
    "src/core/schema.sql",  # the domain contract (docs/SCHEMA*.md)
    "src/api/public/people.py",  # route inventory (docs/API_ENTITIES.md)
    "src/core/db.py",
    "src/templates/admin/base.html",  # admin surface (docs/ADMIN*.md, docs/UI.md)
    "src/static/admin/admin.css",  # visual system (docs/STYLE.md)
    "scripts/worktree-setup.sh",  # operational scripts (docs/RUNBOOKS.md)
    "infra/power-map.service",  # deployment units (docs/COMMANDS.md)
    ".claude/hooks/socraticode-health.sh",  # hook inventory (docs/SKILLS.md)
    "skills/brainstorming/SKILL.md",  # skill inventory (docs/SKILLS.md)
    ".github/workflows/context-cadence.yml",  # weekly cadence (docs/CONTEXT.md, #471)
)


def parse_entries(text: str) -> list[str]:
    """The entries `doc-check.sh` would read: blanks and `#`-comments dropped, trimmed.

    Mirrors the vendored reader, which is the same grammar as
    `.skills/import-targets`.
    """
    entries = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        entries.append(stripped)
    return entries


def path_matches(file: str, entry: str) -> bool:
    """Mirror of the vendored `path_matches`: entries match whole path segments.

    A trailing-slash entry matches the directory at any depth; a slash-less
    entry names a file OR a directory, and every continuation requires a
    literal `/` after it — which is what keeps `pyproject.toml` from also
    claiming `pyproject.toml.bak`.
    """
    if entry.endswith("/"):
        return file.startswith(entry) or f"/{entry}" in file
    return (
        file == entry
        or file.endswith(f"/{entry}")
        or file.startswith(f"{entry}/")
        or f"/{entry}/" in file
    )


@pytest.fixture(scope="module")
def entries() -> list[str]:
    """The committed list, parsed."""
    return parse_entries(LIST_PATH.read_text())


@pytest.fixture(scope="module")
def tracked_files() -> list[str]:
    """Every tracked path, as `doc-check.sh` sees them.

    `GIT_*` is scrubbed for the reason `tests/vendor_skills.py` scrubs it: the
    unit tier runs under pre-commit, where `GIT_DIR` is exported and beats
    `-C`, so an inherited environment answers about a different checkout.
    `core.quotePath=false` matches the gate, which sets it so a non-ASCII
    filename does not arrive C-quoted and defeat the matcher.
    """
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    proc = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "-c", "core.quotePath=false", "ls-files"],
        capture_output=True,
        text=True,
        check=True,
        env=env,
    )
    return proc.stdout.splitlines()


def test_the_list_exists() -> None:
    """Without the file the gate runs its defaults, five of which are dead here."""
    assert LIST_PATH.is_file(), f"missing {LIST_PATH.relative_to(REPO_ROOT)}"


def test_the_list_names_at_least_one_path(entries: list[str]) -> None:
    """A present-but-empty list is exit 2 — the gate refuses to guess."""
    assert entries, "the list exists but names no paths; delete it to fall back to the defaults"


def test_no_duplicate_entries(entries: list[str]) -> None:
    """A duplicate cannot change a verdict; it only misreports the list's shape."""
    duplicates = sorted({entry for entry in entries if entries.count(entry) > 1})
    assert not duplicates, f"duplicate entries: {duplicates}"


def test_every_entry_matches_a_tracked_file(entries: list[str], tracked_files: list[str]) -> None:
    """No dead entries — the exact drift #488 asked this repo to look at.

    A dead entry contributes to no verdict and prints as a note on every green
    run. Either the tree moved and the entry should follow it, or the entry
    describes a layout this service never had and should go.
    """
    dead = [e for e in entries if not any(path_matches(f, e) for f in tracked_files)]
    assert not dead, (
        f"these entries match no tracked file, so they can never contribute to a "
        f"verdict: {dead}. Point them at the real tree or drop them."
    )


@pytest.mark.parametrize("path", REPRESENTATIVE_PATHS)
def test_documented_trees_stay_covered(
    path: str, entries: list[str], tracked_files: list[str]
) -> None:
    """Each tree the docs inventory is still watched by some entry.

    The representative file is asserted to be tracked first: a renamed file
    would otherwise retire its own tree's coverage while staying green.
    """
    assert path in tracked_files, (
        f"{path} is no longer tracked — re-anchor this ratchet on a file that is, "
        "rather than deleting the row and losing the tree's coverage"
    )
    assert any(path_matches(path, e) for e in entries), (
        f"no entry covers {path}; the docs inventory that tree, so a change to it "
        "should reach Step 1.5"
    )


@pytest.mark.skipif(
    not vendor_skills.vendor_skills_present(),
    reason=vendor_skills.SKIP_REASON,
)
def test_the_pin_reads_the_project_list() -> None:
    """The load-bearing guard: the submodule is at or past skills#252.

    Ancestry, not a substring: the vendored script is a file this repo may not
    edit, and an upstream reflow must not read as a rollback.
    """
    contains = vendor_skills.contains_commit(SEGMENT_MATCH_COMMIT)
    if contains is None:
        pytest.skip(
            f"git cannot resolve {SEGMENT_MATCH_COMMIT} in "
            f"{vendor_skills.VENDOR_ROOT.name} (shallow clone or missing git) — "
            "ancestry unverifiable, so it is not reported either way"
        )
    assert contains, ROLLBACK_HINT


@pytest.mark.skipif(
    not vendor_skills.vendor_skills_present(),
    reason=vendor_skills.SKIP_REASON,
)
class TestTheMirrorStillDescribesTheGate:
    """Corroboration for `path_matches` — the mirror is a hand-copy of bash.

    The ancestry guard above proves the pin is at or past skills#252; it cannot
    prove the semantics have not moved *since*. A drifted mirror is the worst
    shape a guard here can take: every assertion in this file stays green while
    predicting a verdict the real gate no longer reaches. So the vendored source
    is read for the two `case` statements the mirror encodes.

    A miss here is upstream having refactored, not this repo having broken —
    re-anchor the mirror against the vendored script and do NOT edit
    `skills-vendor/` to make it pass.
    """

    REFACTOR_HINT = (
        "The vendored doc-check.sh no longer carries this matcher, so "
        "path_matches in this file may no longer describe the gate. Re-read "
        f"{DOC_CHECK_PATH.name} and re-anchor the mirror; treat it as an "
        "upstream change, not a rollback, and never edit skills-vendor/."
    )

    @pytest.fixture(scope="class")
    def source(self) -> str:
        """The vendored gate's source."""
        return DOC_CHECK_PATH.read_text()

    def test_the_trailing_slash_branch_is_unchanged(self, source: str) -> None:
        """Directory entries: prefix, or anywhere after a `/`."""
        assert 'case "$file" in "$entry"*|*"/$entry"*) return 0 ;; esac' in source, (
            self.REFACTOR_HINT
        )

    def test_the_slashless_branch_is_unchanged(self, source: str) -> None:
        """File-or-directory entries: exact, suffix, prefix-dir, interior-dir."""
        assert '"$entry"|*"/$entry"|"$entry"/*|*"/$entry"/*) return 0 ;;' in source, (
            self.REFACTOR_HINT
        )

    def test_the_gate_still_reads_this_project_list(self, source: str) -> None:
        """The override path is the one this repo committed to."""
        assert "if [[ -f .skills/doc-sensitive-paths ]]; then" in source, self.REFACTOR_HINT
