"""Regression tests for the root conftest.

Locks the integration-skip message contract: it must lead with the
diagnosis (`TEST_DATABASE_URL`) and point at the remediation doc
(`docs/COMMANDS.md`). See issue #149.
"""

import pytest
from _pytest.outcomes import Exit

from tests import conftest, optional_groups
from tests.conftest import INTEGRATION_SKIP_REASON


def test_skip_reason_names_the_missing_env_var():
    assert "TEST_DATABASE_URL" in INTEGRATION_SKIP_REASON


def test_skip_reason_points_at_remediation_doc():
    assert "docs/COMMANDS.md" in INTEGRATION_SKIP_REASON


@pytest.mark.integration
async def test_db_pool_uses_test_safe_connection_sizes(db_pool):
    """Pool sizes must stay well below DO DB connection caps.

    Without explicit limits asyncpg defaults to min=10/max=10, which
    exhausts the available slots when a full integration suite runs and
    triggers TooManyConnectionsError (issue #226).
    """
    assert db_pool.get_min_size() <= 2
    assert db_pool.get_max_size() <= 2


# --- optional-group guards (#450) -------------------------------------------


class _FakeOption:
    def __init__(self, markexpr=""):
        self.markexpr = markexpr


class _FakeConfig:
    def __init__(self, markexpr=""):
        self.option = _FakeOption(markexpr)


class _FakeReporter:
    def __init__(self):
        self.seps = []

    def write_sep(self, char, title, **kwargs):
        self.seps.append((char, title, kwargs))


def test_configure_aborts_when_browser_requested_without_playwright(monkeypatch):
    """A vacuous `-m browser` run must exit 2, not collect nothing and pass."""
    monkeypatch.setattr(optional_groups, "_has_module", lambda name: False)
    with pytest.raises(Exit) as exc:
        conftest.pytest_configure(_FakeConfig("browser"))
    assert exc.value.returncode == 2
    assert "playwright" in str(exc.value)


def test_configure_does_not_abort_on_a_default_run(monkeypatch):
    monkeypatch.setattr(optional_groups, "_has_module", lambda name: False)
    conftest.pytest_configure(_FakeConfig("not integration and not browser"))


def test_terminal_summary_warns_about_absent_groups(monkeypatch):
    monkeypatch.setattr(optional_groups, "_has_module", lambda name: False)
    reporter = _FakeReporter()
    conftest.pytest_terminal_summary(reporter, 0, _FakeConfig())
    assert reporter.seps, "a missing tier must be announced, not left silent"
    _, title, kwargs = reporter.seps[0]
    assert "NOT RUN" in title
    assert kwargs.get("red") is True


def test_terminal_summary_is_quiet_when_every_group_is_installed(monkeypatch):
    monkeypatch.setattr(optional_groups, "_has_module", lambda name: True)
    reporter = _FakeReporter()
    conftest.pytest_terminal_summary(reporter, 0, _FakeConfig())
    assert reporter.seps == []
