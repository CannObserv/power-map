"""Regression tests for the root conftest.

Locks the integration-skip message contract: it must lead with the
diagnosis (`TEST_DATABASE_URL`) and point at the remediation doc
(`docs/COMMANDS.md`). See issue #149.
"""

from tests.conftest import INTEGRATION_SKIP_REASON


def test_skip_reason_names_the_missing_env_var():
    assert "TEST_DATABASE_URL" in INTEGRATION_SKIP_REASON


def test_skip_reason_points_at_remediation_doc():
    assert "docs/COMMANDS.md" in INTEGRATION_SKIP_REASON
