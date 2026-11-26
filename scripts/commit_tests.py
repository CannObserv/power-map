"""The pre-commit `pytest` hook's router (#890): run the unit tests a commit can break.

The unit tier is ~12 min here, 87% of it dbt-duckdb builds (`pytest.mark.dbt`).
This reads the staged set and runs one pytest:

- nothing staged (a manual ``pre-commit run``) → the whole unit tier;
- only docs (``docs/**`` or ``*.md``) → only the test modules that read docs;
- otherwise the unit tier without ``dbt``, or with it when a staged path is in
  the dbt tier's reach: the dbt modules' import closure, anything under
  ``src/core/ingestion/`` or ``tests/core/ingestion/``, a dependency file, or the
  root ``tests/conftest.py``.

``scripts/pre-ship.sh`` runs the whole unit tier before every push whatever this
decides, so routing a test out moves its failure from commit to ship, never
past it. Over-selecting only costs time: a doc reader is found by text, and a
comment naming a doc selects its module. docs/TESTING.md § Commit hook.

The staged set is the index the hook sees (``GIT_INDEX_FILE`` included, so a
``git commit -- <paths>`` reads its temporary index), both sides of a rename and
deletions included.

Usage (the hook's entry): uv run --group seed python -m scripts.commit_tests
"""

import ast
import os
import re
import subprocess
import sys
from collections.abc import Iterable, Mapping
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

UNIT = "not integration and not browser"
FAST = f"{UNIT} and not dbt"
PYTEST = ["--no-cov", "-x"]

# Changes that reach every test, dbt ones included.
GLOBAL_FILES = {"pyproject.toml", "uv.lock", "tests/conftest.py"}
DBT_PREFIXES = ("src/core/ingestion/", "tests/core/ingestion/")

# What reading a doc looks like in a test: a docs/ path, a "docs" join, a .md name.
DOC_READ = re.compile(r"""docs/|["']docs["']|\.md\b""")
# A module-level `pytestmark = …` or a decorator: not the words in a string.
DBT_MARK = re.compile(r"^(?:pytestmark\s*=.*|\s*@)pytest\.mark\.dbt\b", re.M)
INTEGRATION_MARK = re.compile(r"^pytestmark\s*=.*pytest\.mark\.integration\b", re.M)
LOCAL_PACKAGES = ("src", "scripts", "tests")


def is_doc(path: str) -> bool:
    """A path whose change no code reads: under docs/, or a Markdown file."""
    return path.startswith("docs/") or path.endswith(".md")


def _test_modules(root: Path) -> list[Path]:
    return sorted((root / "tests").rglob("test_*.py"))


def _rel(root: Path, path: Path) -> str:
    return path.relative_to(root).as_posix()


def _module_files(root: Path, dotted: str) -> list[Path]:
    """The repo files importing ``dotted`` executes: its package inits and itself.

    Both spellings of every prefix, present or not: a deleted module is staged
    too, and its path must still be in reach. A name that is an attribute, not a
    module, adds two paths nothing will ever stage.
    """
    parts = dotted.split(".")
    if parts[0] not in LOCAL_PACKAGES:
        return []
    files = []
    for i in range(1, len(parts) + 1):
        base = root.joinpath(*parts[:i])
        files += [base / "__init__.py", base.with_suffix(".py")]
    return files


def _imports(root: Path, path: Path) -> set[Path]:
    """Repo files ``path`` imports directly (absolute and relative)."""
    try:
        tree = ast.parse(path.read_text())
    except (OSError, SyntaxError, UnicodeDecodeError):
        return set()
    package = path.relative_to(root).with_suffix("").parts[:-1]
    found: set[Path] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            if node.level:
                anchor = package[: len(package) - node.level + 1]
                base = ".".join([*anchor, base] if base else anchor)
            names = [base, *(f"{base}.{alias.name}" for alias in node.names)]
        else:
            continue
        for name in names:
            found.update(_module_files(root, name))
    return found


def dbt_modules(root: Path = ROOT, *, unit_only: bool = False) -> list[str]:
    """Test modules that carry ``pytest.mark.dbt``; ``unit_only`` drops integration ones."""
    found = []
    for path in _test_modules(root):
        text = path.read_text()
        if DBT_MARK.search(text) and not (unit_only and INTEGRATION_MARK.search(text)):
            found.append(_rel(root, path))
    return found


def _conftests_above(root: Path, module: Path) -> list[Path]:
    """Conftests between ``module`` and tests/, the root one excluded: it is a
    trigger of its own, and its imports load in every test the fast tier runs."""
    found = []
    for parent in module.parents:
        if parent == root / "tests":
            break
        conftest = parent / "conftest.py"
        if conftest.is_file():
            found.append(conftest)
    return found


def dbt_reach(root: Path = ROOT) -> set[str]:
    """Every repo file the unit-tier dbt modules import, transitively, with their
    conftests. Integration modules are left out: the hook never runs them."""
    queue = [root / m for m in dbt_modules(root, unit_only=True)]
    queue += [c for m in list(queue) for c in _conftests_above(root, m)]
    seen: set[Path] = set()
    while queue:
        path = queue.pop()
        if path in seen:
            continue
        seen.add(path)
        queue.extend(_imports(root, path) - seen)
    return {_rel(root, p) for p in seen}


def doc_readers(root: Path = ROOT) -> list[str]:
    """Test modules that read docs: by text, or through a tests/ helper that does."""
    helpers = {
        p
        for p in (root / "tests").rglob("*.py")
        if not p.name.startswith("test_") and p.name != "conftest.py"
        if DOC_READ.search(p.read_text())
    }
    readers = []
    for module in _test_modules(root):
        if DOC_READ.search(module.read_text()) or _imports(root, module) & helpers:
            readers.append(_rel(root, module))
    return readers


def _reaches_dbt(paths: Iterable[str], root: Path) -> bool:
    reach: set[str] | None = None
    for path in paths:
        if path in GLOBAL_FILES or path.startswith(DBT_PREFIXES):
            return True
        if path.endswith(".py"):
            reach = dbt_reach(root) if reach is None else reach
            if path in reach:
                return True
    return False


def plan(staged: list[str], root: Path = ROOT) -> list[str] | None:
    """The pytest arguments for ``staged`` (after `PYTEST`); None when nothing applies."""
    if not staged:
        return ["-m", UNIT]
    if all(is_doc(p) for p in staged):
        readers = doc_readers(root)
        return ["-m", FAST, *readers] if readers else None
    return ["-m", UNIT if _reaches_dbt(staged, root) else FAST]


def staged_paths(root: Path = ROOT, env: Mapping[str, str] | None = None) -> list[str]:
    """Paths staged in the index the hook sees, deletions and both rename sides."""
    out = subprocess.run(
        ["git", "diff", "--cached", "--name-only", "--no-renames", "-z"],
        cwd=root,
        env=env,
        check=True,
        capture_output=True,
    ).stdout
    return [p.decode() for p in out.split(b"\0") if p]


def main() -> int:
    """Run the routed pytest; its exit code is the hook's."""
    staged = staged_paths()
    args = plan(staged)
    if args is None:
        print("commit_tests: docs-only change and no test reads docs; nothing to run")
        return 0
    what = "whole unit tier" if not staged else f"{len(staged)} staged path(s)"
    print(f"commit_tests: {what} → pytest {' '.join(args[:2])} (+{len(args) - 2} modules)")
    sys.stdout.flush()
    os.execv(sys.executable, [sys.executable, "-m", "pytest", *PYTEST, *args])
    return 1  # unreachable: execv replaces the process


if __name__ == "__main__":
    sys.exit(main())
