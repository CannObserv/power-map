"""The vendored subset a skills-vendor/ bump commits through (#751, #755).

The SessionStart hook's daily pointer bump commits through pre-commit inside a
120 s timeout, and the full unit tier takes ~550 s. ``.skills/refresh-skip-hooks``
(gregoryfoster/skills#572) has the hook skip the ``pytest``, ``vitest`` and
``bats`` stages on its own commit; every commit made by hand still runs them.
What a bump can break still runs, as its own ``always_run`` hooks:
``pytest-vendored`` lists the modules marked ``pytest.mark.vendored``, and
``bats-vendored`` lists the bats files that read the vendored tree. A list that
misses a test lets a bump that breaks it reach ``main`` untested, so this sweeps
the test tree for anything that reads the vendored tree and requires it listed.

A text match, so it misses a test that reaches the vendored tree indirectly
(through a helper it does not name). Over-matching only makes the subset larger.
"""

import ast
import re
import shlex
from pathlib import Path

import pytest
import yaml

# Guards the subset, so it runs in it.
pytestmark = pytest.mark.vendored

ROOT = Path(__file__).resolve().parent.parent
TESTS = ROOT / "tests"
KNOB = ROOT / ".skills" / "refresh-skip-hooks"

# What reading the vendored tree looks like in a test: the submodules, the
# doctor and the other .skills/ knobs (in a path string or a pathlib join), the
# skill symlinks, and the helper that locates vendored drivers.
VENDOR_READ = re.compile(
    r'skills-vendor|\.skills/|"\.skills"|\.claude/skills|\bskills/|/ "skills"|vendor_skills'
)

# The marker itself, not a look-alike such as `vendored_slow`.
VENDORED_MARK = re.compile(r"\bpytest\.mark\.vendored\b")

# The stages the refresh hook's commit skips, each with its full command.
FULL = {
    "pytest": 'uv run --group seed pytest --no-cov -x -m "not integration and not browser"',
    "vitest": "npm run test:js",
    "bats": "npm run test:sh",
}


def _hooks() -> list[dict]:
    config = yaml.safe_load((ROOT / ".pre-commit-config.yaml").read_text())
    return [h for r in config["repos"] for h in r["hooks"]]


def _hook(hook_id: str) -> dict:
    (hook,) = [h for h in _hooks() if h["id"] == hook_id]
    return hook


def _argv(hook_id: str) -> list[str]:
    return shlex.split(_hook(hook_id)["entry"])


def _knob_ids() -> list[str]:
    """The knob as the vendored hook reads it: words, a line led by `#` skipped."""
    ids = []
    for line in KNOB.read_text().splitlines():
        words = line.split()
        if words and not words[0].startswith("#"):
            ids.extend(words)
    return ids


def _listed(hook_id: str, suffix: str) -> set[str]:
    return {word for word in _argv(hook_id) if word.endswith(suffix)}


def _is_marked_vendored(path: Path) -> bool:
    """True when the module's ``pytestmark`` carries ``pytest.mark.vendored``."""
    for node in ast.parse(path.read_text()).body:
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == "pytestmark" for t in node.targets
        ):
            return VENDORED_MARK.search(ast.unparse(node.value)) is not None
    return False


def _vendor_reading(paths: list[Path]) -> list[Path]:
    return [p for p in paths if VENDOR_READ.search(p.read_text())]


def _rel(paths: list[Path]) -> set[str]:
    return {str(p.relative_to(ROOT)) for p in paths}


@pytest.mark.parametrize(
    "spelling",
    [
        'ROOT / "skills-vendor" / "gregoryfoster-skills"',
        '"$ROOT/skills-vendor/gregoryfoster-skills"',
        'REPO_ROOT / ".skills" / "doc-sections"',
        '".skills/doctor.sh"',
        'REPO_ROOT / ".claude" / "skills"',
        'REPO_ROOT / "skills"',
        "from tests import vendor_skills",
    ],
)
def test_vendor_read_matches_each_spelling_this_repo_uses(spelling: str):
    """A test reaching the vendored tree by a spelling the sweep misses escapes the subset."""
    assert VENDOR_READ.search(spelling)


@pytest.mark.parametrize(
    ("assignment", "marked"),
    [
        ("pytestmark = pytest.mark.vendored", True),
        ("pytestmark = [pytest.mark.vendored, pytest.mark.skipif(True, reason='x')]", True),
        ("pytestmark = pytest.mark.vendored_slow", False),
        ("pytestmark = pytest.mark.integration", False),
    ],
)
def test_is_marked_vendored_reads_the_marker_itself(tmp_path: Path, assignment: str, marked: bool):
    """A look-alike marker would pass the sweep yet stay out of the list."""
    module = tmp_path / "test_x.py"
    module.write_text(f"import pytest\n\n{assignment}\n")
    assert _is_marked_vendored(module) is marked


def test_the_vendored_marker_is_registered():
    """An unregistered marker is a typo pytest only warns about."""
    text = (ROOT / "pyproject.toml").read_text()
    assert re.search(r'^\s*"vendored: ', text, re.M)


def test_the_knob_skips_exactly_the_three_always_run_test_stages():
    """pytest, vitest and bats are always_run, so all three meet the hook's commit."""
    assert sorted(_knob_ids()) == sorted(FULL)
    for hook_id in FULL:
        assert _hook(hook_id).get("always_run") is True


def test_each_skipped_stage_runs_its_full_command_plainly():
    """The knob, not a wrapper, decides what the refresh's commit runs: the
    path-sniffing gate (#752) copied the hook's COMMIT_PATHS and is retired."""
    for hook_id, full in FULL.items():
        assert _argv(hook_id) == shlex.split(full), f"{hook_id}'s entry drifted"
    assert not any(":::" in h["entry"] for h in _hooks())


def test_every_knob_id_is_a_defined_hook_and_holds_no_comma():
    """pre-commit ignores an unknown SKIP id and splits on commas: either one silently
    puts a nine-minute stage back into the refresh's commit."""
    defined = {h["id"] for h in _hooks()} | {h["alias"] for h in _hooks() if "alias" in h}
    for word in _knob_ids():
        assert "," not in word and not word.startswith("#"), word
        assert word in defined, f"{word} is not a hook id in .pre-commit-config.yaml"


@pytest.mark.parametrize("hook_id", ["pytest-vendored", "bats-vendored"])
def test_the_subset_hooks_run_on_every_commit(hook_id: str):
    """The refresh's commit stages no test file, so a files: filter would skip them."""
    hook = _hook(hook_id)
    assert hook.get("always_run") is True
    assert hook.get("pass_filenames") is False
    assert hook_id not in _knob_ids()


def test_pytest_vendored_lists_files_not_the_marker():
    """`-m vendored` collects the whole suite first: 18-36 s against ~7 s (#755)."""
    argv = _argv("pytest-vendored")
    assert argv[:2] == ["uv", "run"] and "pytest" in argv
    assert "-m" not in argv
    assert "--no-cov" in argv


def test_every_vendor_reading_test_module_is_marked_vendored():
    modules = _vendor_reading(sorted(TESTS.rglob("test_*.py")))
    assert modules, "the sweep matched nothing: VENDOR_READ is broken"
    unmarked = sorted(_rel([p for p in modules if not _is_marked_vendored(p)]))
    assert not unmarked, f"reads the vendored tree but lacks pytest.mark.vendored: {unmarked}"


def test_pytest_vendored_lists_exactly_the_marked_modules():
    marked = _rel([p for p in sorted(TESTS.rglob("test_*.py")) if _is_marked_vendored(p)])
    listed = _listed("pytest-vendored", ".py")
    assert listed == marked, (
        f"missing from pytest-vendored: {sorted(marked - listed)}; "
        f"listed but not marked vendored: {sorted(listed - marked)}"
    )


def test_every_vendor_reading_bats_file_is_in_bats_vendored():
    files = _vendor_reading(sorted((TESTS / "sh").glob("*.bats")))
    assert files, "the sweep matched nothing: VENDOR_READ is broken"
    listed = _listed("bats-vendored", ".bats")
    missing = sorted(_rel(files) - listed)
    assert not missing, f"reads the vendored tree but is not in bats-vendored: {missing}"
    absent = sorted(p for p in listed if not (ROOT / p).is_file())
    assert not absent, f"bats-vendored lists files that do not exist: {absent}"


def test_no_js_test_reads_the_vendored_tree():
    """Why vitest has no subset hook."""
    assert not _vendor_reading(sorted((TESTS / "js").rglob("*.js")))
