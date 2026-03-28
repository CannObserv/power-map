"""Root test configuration.

Redirects DATABASE_URL to TEST_DATABASE_URL when the latter is set, so that
integration tests never touch the production database when run with the
standard `env` file loaded.

If TEST_DATABASE_URL is absent, all integration-marked tests are skipped rather
than falling through to the production DATABASE_URL.
"""

import os

import pytest


def pytest_configure(config):
    """Swap DATABASE_URL → TEST_DATABASE_URL before any test collection."""
    test_url = os.environ.get("TEST_DATABASE_URL")
    if test_url:
        os.environ["DATABASE_URL"] = test_url


def pytest_collection_modifyitems(config, items):
    """Skip integration tests when TEST_DATABASE_URL is not set."""
    if os.environ.get("TEST_DATABASE_URL"):
        return
    skip = pytest.mark.skip(
        reason="TEST_DATABASE_URL not set — refusing to run integration tests against production DB"
    )
    for item in items:
        if "integration" in item.keywords:
            item.add_marker(skip)
