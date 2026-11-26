"""A test that runs dbt must carry `pytest.mark.dbt` (#890).

The commit hook leaves the dbt tier out unless a staged path can reach it
(`scripts/commit_tests.py`), so an unmarked dbt test would run at commit on a
change that cannot break it, and the hook would slow back down unnoticed. The
root conftest's autouse guard swaps the mapping package's `dbtRunner` for one
that fails the test, in every test without the marker.
"""

import sys
from pathlib import Path

import pytest

mapping = pytest.importorskip("src.core.ingestion.mapping")

from dbt.cli.main import dbtRunner  # noqa: E402

PYPROJECT = Path(__file__).resolve().parent.parent / "pyproject.toml"


def test_an_unmarked_test_that_runs_dbt_fails():
    with pytest.raises(pytest.fail.Exception, match="pytest.mark.dbt"):
        mapping.run_dbt(["parse"], snapshot_root="/nonexistent", duckdb_path=":memory:")


@pytest.mark.dbt
def test_a_marked_test_gets_the_real_runner():
    assert sys.modules["src.core.ingestion.mapping"].dbtRunner is dbtRunner


def test_the_marker_is_registered():
    """An unregistered marker is a typo pytest only warns about."""
    assert '"dbt: ' in PYPROJECT.read_text()
