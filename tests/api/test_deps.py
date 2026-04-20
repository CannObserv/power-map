"""Tests for shared API dependencies (src.api.deps)."""

import asyncio

import pytest

import src.core.db as db_module
from src.api.deps import get_db


def test_get_db_raises_runtime_error_when_pool_not_initialised():
    """get_db must propagate RuntimeError from db.get_pool() when pool is None."""
    original = db_module._pool
    db_module._pool = None
    try:
        gen = get_db()
        with pytest.raises(RuntimeError, match="not initialised"):
            asyncio.run(gen.__anext__())
    finally:
        db_module._pool = original
