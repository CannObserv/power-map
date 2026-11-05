"""Guards this repo's ship gate: the vendored `pre-ship.sh`, tailored by two files (#549, #739),
behind the repo-root a11y wrapper `scripts/pre-ship.sh` (#740).

`shipping-work` Step 1 resolves `scripts/<name>.sh` from the repo
root **before** the skill directory. #539 put a copy there because the vendored
gate ran `uv run pytest … -m "not integration"` — that `-m` **replaced** our
`addopts` default of `-m 'not integration and not browser'` and so requested the
browser tier — and because it omitted the `--group seed` our pre-commit hook
passes. Upstream fixed both (skills#304): integration-marked items are now
deselected by a collection plugin, leaving `addopts` alone, and
`.skills/pre-ship-uv-args` puts the project's arguments after `uv run` in every
uv call. The copy's own ratchet fired on the bump that brought that in, and it
was deleted (#549, #463: a divergence retires itself).

What keeps the vendored gate right for this repo:

* `test_the_local_gate_wraps_and_does_not_fork` — `scripts/pre-ship.sh` wins
  Step 1, so it must stay a wrapper (#740: the a11y tiers on an admin diff), not
  a copy that stops receiving upstream fixes without saying so.
* `test_step_1_resolves_pre_ship_to_the_wrapper` — runs the vendored Step 1
  loop itself: the wrapper wins, and only for `pre-ship.sh` (skills#301).
* The `scripts/pre-ship-a11y-paths` tests — every entry hits a tracked file, and
  the entries cover every module the two tiers import, transitively.
* `test_the_vendored_gate_reads_our_uv_args_file` — the knob we depend on must
  still exist, or `.skills/pre-ship-uv-args` is inert and the gate runs a
  narrower suite than the hook.
* `test_the_vendored_gate_leaves_the_marker_alone` — the #539 defect, watched
  upstream now: the day a vendored pytest line passes `-m` again, this fails
  rather than the ship gate silently acquiring the browser tier.
* `test_our_uv_args_match_the_pre_commit_hook` — the ship gate and the pre-commit
  `pytest (unit)` hook run one suite definition.
* `test_the_gate_imports_the_module_the_service_boots` — the unified gate's
  import check (#739) guesses a package from the `[project]` name, and
  `power_map` is none here, so without `.skills/import-targets` it skips. The
  target is the ASGI module `power-map.service` runs, so an import-time error
  in any module the app reaches fails the gate as its own labelled check. pytest
  collection imports the app too; this line names the failure ahead of it.
* `test_the_vendored_scripts_read_our_import_targets_file` — the knob that
  carries it must still exist upstream, in code rather than a comment or help
  line: the resolver reads it, and the gate runs a named target that is no
  directory (`src.api.main`) only because that file exists.

`tests/sh/pre_ship.bats` runs the vendored gate against our real args and
import-targets files; `tests/sh/pre_ship_wrapper.bats` runs the wrapper.
"""

import ast
import re
import shlex
import subprocess
from pathlib import Path

import pytest
import yaml

from tests import vendor_skills

# Reads the vendored tree: runs on a skills-vendor/ bump's commit too (#751).
pytestmark = pytest.mark.vendored

REPO_ROOT = Path(__file__).parents[2]
LOCAL_GATE = REPO_ROOT / "scripts" / "pre-ship.sh"
A11Y_PATHS_FILE = REPO_ROOT / "scripts" / "pre-ship-a11y-paths"
UV_ARGS_FILE = REPO_ROOT / ".skills" / "pre-ship-uv-args"
PRE_COMMIT = REPO_ROOT / ".pre-commit-config.yaml"
IMPORT_TARGETS_FILE = REPO_ROOT / ".skills" / "import-targets"
SERVICE_UNIT = REPO_ROOT / "infra" / "power-map.service"

VENDORED_GATE = vendor_skills.VENDOR_ROOT / "skills" / "shipping-work" / "scripts" / "pre-ship.sh"
VENDORED_IMPORT_RESOLVER = VENDORED_GATE.with_name("detect-import-targets.sh")
VENDORED_SKILL = VENDORED_GATE.parents[1] / "SKILL.md"
# The path the wrapper delegates through: the symlink, never skills-vendor/.
DELEGATE = "skills/shipping-work/scripts/pre-ship.sh"

# The modules each a11y tier collects (the tiers' own command lines are in the
# wrapper); the scope must also cover everything they import from `tests`.
A11Y_TIER_ROOTS = [
    "tests/conftest.py",
    "tests/api/admin/conftest.py",
    "tests/api/admin/test_a11y_render.py",
]
# Read at run time, not imported: the axe-core pin.
A11Y_DATA_FILES = ["tests/vendor/"]
# What the tiers render or serve: every tracked file below these. main.py mounts
# the static dir and the admin router and injects the admin templates' globals.
A11Y_SURFACE = ["src/api/main.py", "src/api/admin/", "src/templates/admin/", "src/static/"]

# The ASGI module on the unit's `uvicorn <module>:<app>` command line.
UVICORN_MODULE = re.compile(r"^ExecStart=.*\buvicorn\s+([\w.]+):\w+", re.MULTILINE)

# An executable pytest run through uv, either spelling: `uv run … pytest` or the
# vendored `uv_run pytest`. `\bpytest\b` does not match the `pytest_cov` probe.
PYTEST_RUN = re.compile(r"^\s*(?:[A-Z_]+=\S+\s+)*(?:uv run\b|uv_run\b).*\bpytest\b")


@pytest.fixture(scope="module")
def vendored_source() -> str:
    if not VENDORED_GATE.is_file():
        pytest.skip(vendor_skills.SKIP_REASON)
    return VENDORED_GATE.read_text()


def _uv_args_of(entry: str) -> list[str]:
    """The arguments between `uv run` and `pytest` in a hook's command line."""
    words = shlex.split(entry)
    start = words.index("run") + 1
    return words[start : words.index("pytest")]


def _uv_args_file() -> list[str]:
    """`.skills/pre-ship-uv-args` as the vendored gate reads it: words, `#` lines skipped."""
    return [
        word
        for line in UV_ARGS_FILE.read_text().splitlines()
        if line.strip() and not line.lstrip().startswith("#")
        for word in line.split()
    ]


def test_uv_args_of_reads_the_hook_entry():
    """The parser: everything between `uv run` and `pytest`, nothing after."""
    entry = 'uv run --group seed pytest --no-cov -x -m "not integration and not browser"'
    assert _uv_args_of(entry) == ["--group", "seed"]
    assert _uv_args_of("uv run pytest -x") == []


def test_the_local_gate_wraps_and_does_not_fork() -> None:
    """Step 1 probes `scripts/` first: the wrapper must hand off, not copy (#539, #549, #740)."""
    assert LOCAL_GATE.is_file(), "scripts/pre-ship.sh is gone: the a11y tiers no longer gate a ship"
    code = _code_lines(LOCAL_GATE.read_text())
    assert f'DELEGATE="{DELEGATE}"' in code, f"the wrapper must delegate through {DELEGATE}"
    assert re.search(r'^\s*exec bash "\$DELEGATE" "\$@"', code, re.MULTILINE), (
        "the wrapper must exec the vendored gate, so its exit code is the ship's"
    )
    assert "skills-vendor/" not in code, "delegate through the symlink, not the vendor layout"
    for fork_marker in ("ruff", "npm run", "gate-stamp"):
        assert fork_marker not in code, (
            f"the wrapper runs {fork_marker!r}: that is the vendored gate's job. Tailor it "
            "through .skills/ instead of copying a check here."
        )


def _step_1_block(skill_text: str) -> str:
    """SKILL.md Step 1's script-resolution block, minus the doctor and the final gate run."""
    marker = skill_text.index("<!-- skill:required id=skill-scripts -->")
    start = skill_text.index("```bash\n", marker) + len("```bash\n")
    block = skill_text[start : skill_text.index("```", start)]
    lines = block.splitlines()
    assert lines[1].startswith("{ [ ! -x .skills/doctor.sh ]"), "re-anchor: doctor line moved"
    assert lines[-1] == 'bash "${SD:?}/$S"', "re-anchor: the gate run is no longer last"
    return "\n".join([lines[0], *lines[2:-1]]) + "\n"


def test_step_1_resolves_pre_ship_to_the_wrapper() -> None:
    """Run Step 1's own loop here: the wrapper wins for pre-ship.sh, and for nothing else."""
    if not VENDORED_SKILL.is_file():
        pytest.skip(vendor_skills.SKIP_REASON)
    result = subprocess.run(
        ["bash", "-c", _step_1_block(VENDORED_SKILL.read_text())],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    resolved = dict(re.findall(r"^<([\w.-]+)>=(.+)$", result.stdout, re.MULTILINE))
    assert resolved.get("pre-ship.sh") == "scripts/pre-ship.sh", resolved
    shadowed = sorted(n for n, p in resolved.items() if p.startswith("scripts/"))
    assert shadowed == ["pre-ship.sh"], f"scripts/ shadows more than the gate: {shadowed}"


def _a11y_paths() -> list[str]:
    """`scripts/pre-ship-a11y-paths` as the wrapper reads it: trimmed, `#` and blank lines out."""
    return [
        line.strip()
        for line in A11Y_PATHS_FILE.read_text().splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]


def _in_scope(path: str, entries: list[str]) -> bool:
    """The wrapper's match: an entry ending `/` is a directory prefix, any other an exact path."""
    return any(path.startswith(e) if e.endswith("/") else path == e for e in entries)


def _tracked() -> list[str]:
    out = subprocess.run(
        ["git", "ls-files"], cwd=REPO_ROOT, capture_output=True, text=True, check=True
    )
    return out.stdout.splitlines()


def _local_imports(path: Path) -> set[str]:
    """The `tests.*` modules a file imports, as repo-relative paths."""
    found: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text())):
        names: list[str] = []
        if isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names = [node.module] + [f"{node.module}.{a.name}" for a in node.names]
        elif isinstance(node, ast.Import):
            names = [a.name for a in node.names]
        for name in names:
            if not name.startswith("tests."):
                continue
            rel = name.replace(".", "/")
            for candidate in (f"{rel}.py", f"{rel}/__init__.py"):
                if (REPO_ROOT / candidate).is_file():
                    found.add(candidate)
    return found


def _a11y_tier_modules() -> set[str]:
    """Every module the two tiers load: their roots, browser-marked files, and imports."""
    browser = [
        p
        for p in _tracked()
        if p.startswith("tests/api/admin/") and p.endswith(".py")
        if "mark.browser" in (REPO_ROOT / p).read_text()
    ]
    assert browser, "found no browser-marked module — re-anchor the discovery"
    todo, seen = [*A11Y_TIER_ROOTS, *browser], set()
    while todo:
        path = todo.pop()
        if path in seen:
            continue
        seen.add(path)
        todo.extend(_local_imports(REPO_ROOT / path) - seen)
    return seen


def test_the_a11y_path_list_is_not_empty() -> None:
    assert A11Y_PATHS_FILE.is_file(), "missing scripts/pre-ship-a11y-paths"
    assert _a11y_paths(), "scripts/pre-ship-a11y-paths lists nothing"


def test_every_a11y_path_hits_a_tracked_file() -> None:
    """A dead entry reads as coverage and is none."""
    tracked = _tracked()
    dead = [e for e in _a11y_paths() if not _in_scope_any(e, tracked)]
    assert dead == [], f"scripts/pre-ship-a11y-paths entries match no tracked file: {dead}"


def _in_scope_any(entry: str, tracked: list[str]) -> bool:
    return any(_in_scope(path, [entry]) for path in tracked)


def test_the_a11y_paths_cover_every_module_the_tiers_load() -> None:
    """Change a helper the tiers import and the tiers must run on it."""
    entries = _a11y_paths()
    missed = sorted(m for m in _a11y_tier_modules() if not _in_scope(m, entries))
    assert missed == [], f"the a11y tiers load these, and no scope entry covers them: {missed}"


def test_the_a11y_paths_cover_the_admin_surface_and_the_axe_pin() -> None:
    entries = _a11y_paths()
    tracked = _tracked()
    for root in [*A11Y_SURFACE, *A11Y_DATA_FILES]:
        files = [p for p in tracked if p.startswith(root)]
        assert files, f"{root} holds no tracked file — re-anchor A11Y_SURFACE"
        missed = [p for p in files if not _in_scope(p, entries)]
        assert missed == [], f"{root}: not in scripts/pre-ship-a11y-paths: {missed[:5]}"


def test_the_a11y_paths_cover_the_wrapper_and_the_list() -> None:
    """A change to the gate itself exercises the gate."""
    entries = _a11y_paths()
    for path in ("scripts/pre-ship.sh", "scripts/pre-ship-a11y-paths"):
        assert _in_scope(path, entries), f"{path} is not in its own scope"


def test_the_vendored_gate_reads_our_uv_args_file(vendored_source: str) -> None:
    """Without the knob, our args file does nothing and the gate runs a narrower suite."""
    assert ".skills/pre-ship-uv-args" in vendored_source, (
        "the vendored gate no longer reads .skills/pre-ship-uv-args — --group seed is lost"
    )


def test_the_vendored_gate_leaves_the_marker_alone(vendored_source: str) -> None:
    """Any `-m` on a pytest command line replaces the `addopts` marker wholesale (#539)."""
    runs = [line for line in vendored_source.splitlines() if PYTEST_RUN.match(line)]
    assert runs, "found no pytest invocation in the vendored gate — re-anchor PYTEST_RUN"
    for line in runs:
        assert not re.search(r"\s-m\s", line), (
            f"{line.strip()!r} passes -m again, which requests the browser tier here. "
            "Restore a project-local gate (see the module docstring) until upstream fixes it."
        )


def test_our_uv_args_match_the_pre_commit_hook() -> None:
    """One suite definition, two gates: the ship gate runs what the hook runs.

    The hook's entry is gated (#751): its full command is the part after `:::`.
    """
    config = yaml.safe_load(PRE_COMMIT.read_text())
    entry = next(
        (h["entry"] for r in config["repos"] for h in r["hooks"] if h["id"] == "pytest"),
        None,
    )
    assert entry is not None, "no pytest entry found in .pre-commit-config.yaml"
    entry = entry.split(":::", 1)[-1]
    assert UV_ARGS_FILE.is_file(), f"missing {UV_ARGS_FILE.relative_to(REPO_ROOT)}"
    assert _uv_args_file() == _uv_args_of(entry), (
        f".skills/pre-ship-uv-args gives {_uv_args_file()}, the pre-commit pytest hook "
        f"{_uv_args_of(entry)}: the ship gate would run a different suite from the hook"
    )


def _import_targets() -> list[str]:
    """`.skills/import-targets` as the vendored resolver reads it: trimmed, `#` lines skipped."""
    return [
        line.strip()
        for line in IMPORT_TARGETS_FILE.read_text().splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]


def _code_lines(source: str) -> str:
    """A shell script without its comment and `echo` lines: what it does, not what it says."""
    return "\n".join(
        line for line in source.splitlines() if not line.lstrip().startswith(("#", "echo "))
    )


def test_the_gate_imports_the_module_the_service_boots() -> None:
    """The import check imports what production boots, not a guess from the project name."""
    match = UVICORN_MODULE.search(SERVICE_UNIT.read_text())
    assert match, f"no `uvicorn <module>:<app>` ExecStart in {SERVICE_UNIT.name}; re-anchor"
    assert IMPORT_TARGETS_FILE.is_file(), (
        f"missing {IMPORT_TARGETS_FILE.relative_to(REPO_ROOT)}: the gate's import check "
        "guesses `power_map` from the [project] name, finds no such package, and skips"
    )
    targets = _import_targets()
    assert match[1] in targets, (
        f".skills/import-targets lists {targets}, not {match[1]!r}, the module "
        f"{SERVICE_UNIT.name} runs"
    )


@pytest.mark.parametrize("script", [VENDORED_IMPORT_RESOLVER, VENDORED_GATE], ids=lambda p: p.name)
def test_the_vendored_scripts_read_our_import_targets_file(script: Path) -> None:
    """Without the knob, our list does nothing and the import check skips again.

    The resolver must read the file; the gate must key "always run a named
    target" on it, or `src.api.main` — no directory — is guessed away and skipped.
    Comments and help text are dropped first: both name the file too.
    """
    if not script.is_file():
        pytest.skip(vendor_skills.SKIP_REASON)
    assert ".skills/import-targets" in _code_lines(script.read_text()), (
        f"the vendored {script.name} no longer reads .skills/import-targets"
    )
