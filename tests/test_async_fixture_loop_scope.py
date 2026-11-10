"""The suite runs with deprecations as errors, and async fixtures share the session loop (#786).

pytest-asyncio warns in ``pytest_configure`` when
``asyncio_default_fixture_loop_scope`` is unset. The warning is a
``PytestDeprecationWarning``, a ``DeprecationWarning`` subclass, so
``pytest -W error::DeprecationWarning`` died before collecting anything
(INTERNALERROR, exit 3). The #782 runs worked around it with
``-W default::pytest.PytestDeprecationWarning``, which also let every other
pytest deprecation through.

``pyproject.toml`` sets the default to ``"session"``, the same loop
``asyncio_default_test_loop_scope`` gives the tests and that ``db_pool`` is
bound to. Every async fixture also names ``loop_scope=`` explicitly, so the
default only reaches fixtures added later. The sweep keeps it that way: a
future pytest-asyncio is going to change the default to ``"function"``.

Three guards:

- the applied ini value, read through the running session's config;
- a strict-warnings ``--collect-only`` subprocess run, which is how the setting
  is actually used, and which also fails on any other pytest config deprecation;
- an AST sweep: every async fixture under ``tests/`` sets ``loop_scope=``.
"""

import ast
import subprocess
import sys
from pathlib import Path

import pytest

TESTS_DIR = Path(__file__).resolve().parent
REPO_ROOT = TESTS_DIR.parent


def test_async_fixtures_default_to_the_session_loop(pytestconfig: pytest.Config) -> None:
    assert pytestconfig.getini("asyncio_default_fixture_loop_scope") == "session"


def test_collection_survives_deprecations_as_errors() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-W",
            "error::DeprecationWarning",
            "--collect-only",
            "-q",
            "-p",
            "no:cacheprovider",
            str(Path(__file__).relative_to(REPO_ROOT)),
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=120,
    )
    output = result.stdout + result.stderr
    assert result.returncode == 0 and "INTERNALERROR" not in output, (
        f"pytest -W error::DeprecationWarning exited {result.returncode}:\n{output[-3000:]}"
    )


def _fixtures_without_loop_scope(source: str) -> list[int]:
    """Line numbers of async fixtures whose decorator doesn't set ``loop_scope=``."""
    hits = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.AsyncFunctionDef):
            continue
        for deco in node.decorator_list:
            target = deco.func if isinstance(deco, ast.Call) else deco
            if not ast.unparse(target).endswith("fixture"):
                continue
            keywords = {kw.arg for kw in deco.keywords} if isinstance(deco, ast.Call) else set()
            if "loop_scope" not in keywords:
                hits.append(node.lineno)
    return hits


def test_the_detector_is_live() -> None:
    assert _fixtures_without_loop_scope("@pytest_asyncio.fixture\nasync def f(): ...") == [2]
    assert _fixtures_without_loop_scope("@pytest_asyncio.fixture()\nasync def f(): ...") == [2]
    assert _fixtures_without_loop_scope('@fixture(scope="module")\nasync def f(): ...') == [2]
    assert _fixtures_without_loop_scope("@pytest.fixture\nasync def f(): ...") == [2]
    assert (
        _fixtures_without_loop_scope(
            '@pytest_asyncio.fixture(loop_scope="session")\nasync def f(): ...'
        )
        == []
    )
    assert _fixtures_without_loop_scope("@pytest.fixture\ndef f(): ...") == []


def test_every_async_fixture_sets_loop_scope() -> None:
    offenders = [
        f"{path.relative_to(REPO_ROOT)}:{line}"
        for path in sorted(TESTS_DIR.rglob("*.py"))
        for line in _fixtures_without_loop_scope(path.read_text())
    ]
    assert not offenders, (
        'every async fixture names its loop: @pytest_asyncio.fixture(loop_scope="session"), '
        f"the loop db_pool is bound to: {', '.join(offenders)}"
    )
