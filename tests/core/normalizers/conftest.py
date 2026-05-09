"""Shared fixtures for normalizer tests."""

import os

import pytest


@pytest.fixture(autouse=True)
def _strip_address_validator_env(monkeypatch):
    """Strip ADDRESS_VALIDATOR_* env vars before each test.

    Prevents host-level config (e.g. /etc/power-map/.env setting
    ADDRESS_VALIDATOR_RUN_VALIDATION=true) from leaking into tests that
    use ``patch.dict(os.environ, {...}, clear=False)``.
    """
    for key in [k for k in os.environ if k.startswith("ADDRESS_VALIDATOR_")]:
        monkeypatch.delenv(key, raising=False)
