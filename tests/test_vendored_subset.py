"""The vendored subset a skills-vendor/ bump commits through (#751).

The SessionStart hook's daily pointer bump commits through pre-commit inside a
120 s timeout, and the full unit tier takes ~550 s. ``scripts/precommit-vendor-gate.sh``
therefore runs, on a commit staging only skills-vendor/ pointers and
``.skills/doctor.sh``, just the tests that read the vendored tree: pytest's
``vendored`` marker and a named list of bats files. A subset that misses a test
lets a bump that breaks it reach ``main`` untested, so this sweeps the test tree
for anything that reads the vendored tree and requires it in the subset.

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
GATE = "scripts/precommit-vendor-gate.sh"

# What reading the vendored tree looks like in a test: the submodules, the
# doctor and the other .skills/ knobs (in a path string or a pathlib join), the
# skill symlinks, and the helper that locates vendored drivers.
VENDOR_READ = re.compile(
    r'skills-vendor|\.skills/|"\.skills"|\.claude/skills|\bskills/|/ "skills"|vendor_skills'
)

# The marker itself, not a look-alike such as `vendored_slow`.
VENDORED_MARK = re.compile(r"\bpytest\.mark\.vendored\b")

# The full commands before #751, which the gate must still run on every other
# commit unchanged.
FULL = {
    "pytest": 'uv run --group seed pytest --no-cov -x -m "not integration and not browser"',
    "vitest": "npm run test:js",
    "bats": "npm run test:sh",
}


def _hook(hook_id: str) -> dict:
    config = yaml.safe_load((ROOT / ".pre-commit-config.yaml").read_text())
    (hook,) = [h for r in config["repos"] for h in r["hooks"] if h["id"] == hook_id]
    return hook


def _gated(hook_id: str) -> tuple[list[str], list[str]]:
    """Split a gated entry into its (subset, full) argv."""
    argv = shlex.split(_hook(hook_id)["entry"])
    assert argv[:3] == ["bash", GATE, hook_id], f"{hook_id} entry is not gated: {argv}"
    sep = argv.index(":::")
    return argv[3:sep], argv[sep + 1 :]


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
    """A look-alike marker would pass the sweep yet stay out of `-m vendored`."""
    module = tmp_path / "test_x.py"
    module.write_text(f"import pytest\n\n{assignment}\n")
    assert _is_marked_vendored(module) is marked


def test_the_vendored_marker_is_registered():
    """An unregistered marker is a typo pytest only warns about."""
    text = (ROOT / "pyproject.toml").read_text()
    assert re.search(r'^\s*"vendored: ', text, re.M)


def test_each_always_run_test_stage_is_gated_and_keeps_its_full_command():
    """pytest, vitest and bats are always_run, so all three meet the hook's commit."""
    for hook_id, full in FULL.items():
        _, got = _gated(hook_id)
        assert got == shlex.split(full), f"{hook_id}'s full command drifted"
        assert _hook(hook_id).get("always_run") is True


def test_the_pytest_subset_selects_the_vendored_marker():
    subset, _ = _gated("pytest")
    expr = subset[subset.index("-m") + 1]
    assert expr == "vendored and not integration and not browser"


def test_every_vendor_reading_test_module_is_marked_vendored():
    modules = _vendor_reading(sorted(TESTS.rglob("test_*.py")))
    assert modules, "the sweep matched nothing: VENDOR_READ is broken"
    unmarked = [str(p.relative_to(ROOT)) for p in modules if not _is_marked_vendored(p)]
    assert not unmarked, f"reads the vendored tree but lacks pytest.mark.vendored: {unmarked}"


def test_every_vendor_reading_bats_file_is_in_the_bats_subset():
    subset, _ = _gated("bats")
    files = _vendor_reading(sorted((TESTS / "sh").glob("*.bats")))
    assert files, "the sweep matched nothing: VENDOR_READ is broken"
    missing = [str(p.relative_to(ROOT)) for p in files if str(p.relative_to(ROOT)) not in subset]
    assert not missing, f"reads the vendored tree but is not in the bats subset: {missing}"


def test_vitest_has_no_subset_because_no_js_test_reads_the_vendored_tree():
    subset, _ = _gated("vitest")
    assert subset == []
    assert not _vendor_reading(sorted((TESTS / "js").rglob("*.js")))
