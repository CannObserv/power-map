"""Regression tests for the root conftest.

Locks the integration-skip message contract: it must lead with the
diagnosis (`TEST_DATABASE_URL`) and point at the remediation doc
(`docs/COMMANDS.md`). See issue #149.
"""

import pytest

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
    assert db_pool.get_max_size() <= 3
