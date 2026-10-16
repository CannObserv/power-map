"""The suite's own basetemp retention, which keeps the root disk off ENOSPC (#665).

`/`, `/home` and `/tmp` share one 19G volume on the production VM. One full
unit run leaves ~480M in `/tmp/pytest-of-<user>/pytest-N`, most of it the
mapping tier's cached dbt builds, and pytest's defaults keep three runs. Two or
three commits in a session were enough to reach 98%.

`count = 1` keeps one run, and `policy = "failed"` drops a green run's whole
basetemp at session finish, so only a failing run leaves anything behind.
pytest prunes in an `atexit` hook and skips a dir holding a live `.lock`, so a
concurrent session's run survives. Read through the running session's config,
not the file, so this pins what pytest actually applied.
"""

import pytest


def test_the_suite_keeps_one_run_of_basetemps(pytestconfig: pytest.Config) -> None:
    assert int(pytestconfig.getini("tmp_path_retention_count")) == 1


def test_a_green_run_leaves_no_basetemp(pytestconfig: pytest.Config) -> None:
    assert pytestconfig.getini("tmp_path_retention_policy") == "failed"
