"""Unit tests for org-duplicate detection logic (cache, count, dep)."""

import logging
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.api.admin.org_dups import (
    count_org_duplicates,
    get_org_dup_count,
    invalidate_dup_count_cache,
)


def _make_db(fetchval_return: int) -> MagicMock:
    db = MagicMock()
    db.fetchval = AsyncMock(return_value=fetchval_return)
    return db


@pytest.fixture(autouse=True)
def clear_cache():
    """Ensure a clean TTL cache state for every test."""
    invalidate_dup_count_cache()
    yield
    invalidate_dup_count_cache()


class TestCountOrgDuplicates:
    async def test_cache_miss_queries_db(self):
        db = _make_db(5)
        result = await count_org_duplicates(db)
        assert result == 5
        db.fetchval.assert_awaited_once()

    async def test_cache_hit_skips_db(self):
        db = _make_db(3)
        await count_org_duplicates(db)       # prime cache
        db2 = _make_db(99)
        result = await count_org_duplicates(db2)  # should hit cache
        assert result == 3
        db2.fetchval.assert_not_awaited()

    async def test_invalidate_forces_refresh(self):
        db = _make_db(2)
        await count_org_duplicates(db)       # prime cache with 2
        invalidate_dup_count_cache()
        db2 = _make_db(7)
        result = await count_org_duplicates(db2)  # cache cleared → re-query
        assert result == 7
        db2.fetchval.assert_awaited_once()

    async def test_returns_zero_count(self):
        db = _make_db(0)
        assert await count_org_duplicates(db) == 0


class TestGetOrgDupCount:
    async def test_returns_count_on_success(self):
        db = _make_db(4)
        result = await get_org_dup_count(db=db)
        assert result == 4

    async def test_returns_zero_on_db_error(self):
        db = MagicMock()
        db.fetchval = AsyncMock(side_effect=Exception("pg_trgm not installed"))
        result = await get_org_dup_count(db=db)
        assert result == 0

    async def test_uses_cached_value(self):
        db = _make_db(6)
        await get_org_dup_count(db=db)        # prime cache
        db2 = _make_db(99)
        result = await get_org_dup_count(db=db2)
        assert result == 6
        db2.fetchval.assert_not_awaited()

    async def test_error_does_not_poison_cache(self):
        """A failed query should not cache 0; next call re-queries."""
        db_bad = MagicMock()
        db_bad.fetchval = AsyncMock(side_effect=Exception("oops"))
        await get_org_dup_count(db=db_bad)   # fails → returns 0, cache not updated
        db_good = _make_db(3)
        result = await get_org_dup_count(db=db_good)
        assert result == 3
        db_good.fetchval.assert_awaited_once()

    async def test_logs_warning_on_exception(self, caplog):
        """Exception in get_org_dup_count should emit a WARNING with exc_info."""
        db = MagicMock()
        db.fetchval = AsyncMock(side_effect=RuntimeError("boom"))
        with caplog.at_level(logging.WARNING, logger="src.api.admin.org_dups"):
            result = await get_org_dup_count(db=db)
        assert result == 0
        assert any(
            r.levelno == logging.WARNING and r.exc_info is not None
            for r in caplog.records
        )
