"""The commit hook's test router (#890): what a staged set runs.

`plan` is pure over a repo tree, so most of these build a small tree under
`tmp_path`; the last few read the real repo, and one stages files in a real git
repo, because the staged set comes from the index the hook sees.
"""

import os
import subprocess
from pathlib import Path

import pytest

from scripts import commit_tests as ct

ROOT = Path(__file__).resolve().parents[2]


def _tree(root: Path, files: dict[str, str]) -> Path:
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    return root


DBT_TEST = (
    "import pytest\nfrom src.core.ingestion.mapping import run_dbt\npytestmark = pytest.mark.dbt\n"
)

TREE = {
    "pyproject.toml": "",
    "src/__init__.py": "",
    "src/core/__init__.py": "",
    "src/core/logging.py": "",
    "src/core/names.py": "",
    "src/core/ingestion/__init__.py": "",
    "src/core/ingestion/mapping/__init__.py": "from src.core.logging import get_logger\n",
    "src/core/ingestion/mapping/models/a.sql": "select 1",
    "scripts/__init__.py": "",
    "scripts/build_desired_state.py": "from src.core.ingestion.mapping import run_dbt\n",
    "scripts/other.py": "",
    "tests/__init__.py": "",
    "tests/conftest.py": "",
    "tests/doc_helper.py": 'DOC = "docs/X.md"\n',
    "tests/test_docs_direct.py": 'TEXT = (ROOT / "docs").read_text()\n',
    "tests/test_agents.py": 'AGENTS = ROOT / "AGENTS.md"\n',
    "tests/test_via_helper.py": "from tests.doc_helper import DOC\n",
    "tests/test_plain.py": "def test_x():\n    pass\n",
    "tests/core/ingestion/mapping/test_build.py": DBT_TEST,
    "tests/scripts/test_build_desired_state.py": (
        "import pytest\nfrom scripts.build_desired_state import main\n"
        "pytestmark = pytest.mark.dbt\n"
    ),
}


@pytest.fixture
def repo(tmp_path):
    return _tree(tmp_path, TREE)


# --- what a staged set runs --------------------------------------------------


def test_nothing_staged_runs_the_whole_unit_tier(repo):
    assert ct.plan([], repo) == ["-m", ct.UNIT]


def test_a_code_change_outside_dbts_reach_runs_the_unit_tier_without_dbt(repo):
    assert ct.plan(["src/core/names.py"], repo) == ["-m", ct.FAST]


@pytest.mark.parametrize(
    "path",
    [
        "src/core/ingestion/mapping/models/a.sql",  # the dbt project itself
        "src/core/logging.py",  # imported by the mapping package
        "scripts/build_desired_state.py",  # imported by a dbt test module
        "tests/core/ingestion/mapping/test_build.py",
        "tests/core/ingestion/mapping/fixtures/store/x/v1/data.csv",
        "pyproject.toml",
        "uv.lock",
        "tests/conftest.py",
    ],
)
def test_a_path_dbt_can_reach_runs_the_whole_unit_tier(repo, path):
    assert ct.plan(["src/core/names.py", path], repo) == ["-m", ct.UNIT]


def test_a_conftest_outside_dbts_reach_does_not_pull_the_dbt_tier_in(repo):
    _tree(repo, {"tests/api/conftest.py": "from src.core.names import X\n"})
    assert ct.plan(["tests/api/conftest.py"], repo) == ["-m", ct.FAST]


def test_an_integration_dbt_modules_imports_are_not_in_reach(repo):
    """The hook runs the unit tier only; a seam test importing the app must not
    put the whole app in the dbt tier's reach."""
    _tree(
        repo,
        {
            "tests/test_seam.py": (
                "import pytest\nfrom src.core.names import X\n"
                "pytestmark = [pytest.mark.integration, pytest.mark.dbt]\n"
            )
        },
    )
    assert ct.plan(["src/core/names.py"], repo) == ["-m", ct.FAST]


def test_a_deleted_module_in_dbts_reach_still_counts(repo):
    """Deletions are staged paths too, and a deleted file can't be parsed."""
    (repo / "src/core/logging.py").unlink()
    assert ct.plan(["src/core/logging.py"], repo) == ["-m", ct.UNIT]


def test_a_docs_only_change_runs_only_the_modules_that_read_docs(repo):
    args = ct.plan(["docs/TESTING.md", "AGENTS.md", "docs/plans/x-design.md"], repo)
    assert args[:2] == ["-m", ct.FAST]
    assert sorted(args[2:]) == [
        "tests/test_agents.py",
        "tests/test_docs_direct.py",
        "tests/test_via_helper.py",  # through the helper it imports
    ]


def test_a_doc_with_a_code_change_runs_the_tier_not_the_subset(repo):
    assert ct.plan(["docs/TESTING.md", "scripts/other.py"], repo) == ["-m", ct.FAST]


def test_a_docs_only_change_with_no_doc_reader_runs_nothing(tmp_path):
    repo = _tree(tmp_path, {"tests/test_plain.py": "def test_x():\n    pass\n"})
    assert ct.plan(["docs/TESTING.md"], repo) is None


@pytest.mark.parametrize(
    "path,expected",
    [
        ("docs/TESTING.md", True),
        ("docs/plans/a-design.md", True),
        ("docs/diagram.png", True),
        ("README.md", True),
        ("src/static/vendor/README.md", True),
        ("docs.py", False),
        ("src/docs/x.py", False),
        ("tests/test_docs.py", False),
    ],
)
def test_what_counts_as_a_doc(path, expected):
    assert ct.is_doc(path) is expected


# --- against the real repo ---------------------------------------------------


def test_the_real_doc_readers_include_the_context_guards_and_no_dbt_module():
    readers = set(ct.doc_readers(ROOT))
    assert "tests/test_context_surface.py" in readers
    assert "tests/test_socraticode_doc_parity.py" in readers
    assert "tests/scripts/test_commit_tests.py" in readers  # it names docs/ itself
    dbt = set(ct.dbt_modules(ROOT))
    assert "tests/core/ingestion/mapping/test_roles.py" in dbt
    assert "tests/scripts/test_build_desired_state.py" in dbt


def test_the_real_dbt_reach_covers_the_mapping_package_and_not_the_admin():
    reach = ct.dbt_reach(ROOT)
    assert "src/core/ingestion/mapping/__init__.py" in reach
    assert "src/core/logging.py" in reach
    assert "scripts/build_desired_state.py" in reach
    assert "src/api/admin/people.py" not in reach


# --- the staged set, from a real index ---------------------------------------


def _git(repo: Path, *args: str) -> str:
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env |= {"GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}
    return subprocess.run(
        ["git", "-C", str(repo), *args], env=env, check=True, capture_output=True, text=True
    ).stdout


def test_staged_paths_reads_the_index_deletions_renames_and_odd_names(tmp_path):
    repo = tmp_path / "r"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "t@example.invalid")
    _git(repo, "config", "user.name", "t")
    _tree(repo, {"gone.py": "", "old.py": "x = 1\n", "kept.py": ""})
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "init")
    _git(repo, "rm", "-q", "gone.py")
    (repo / "docs").mkdir()
    _git(repo, "mv", "old.py", "docs/new name é.md")  # both sides count (--no-renames)
    (repo / "unstaged.py").write_text("")
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    paths = ct.staged_paths(repo, env=env)
    assert sorted(paths) == ["docs/new name é.md", "gone.py", "old.py"]
