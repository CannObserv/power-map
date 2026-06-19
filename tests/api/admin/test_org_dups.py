"""Unit tests for org-duplicate detection logic (DB cache, count, dep)."""

import logging
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

from src.api.admin.org_dups import (
    count_org_duplicates,
    get_org_dup_count,
    invalidate_dup_count_cache,
)


def _make_db_miss(similarity_count: int = 5) -> MagicMock:
    """DB mock: cache row absent; fetchval returns similarity_count."""
    db = MagicMock()
    db.fetchrow = AsyncMock(return_value=None)
    db.fetchval = AsyncMock(return_value=similarity_count)
    db.execute = AsyncMock()
    return db


def _make_db_hit(cached_count: int = 3) -> MagicMock:
    """DB mock: valid (non-expired) cache row present."""
    db = MagicMock()
    db.fetchrow = AsyncMock(
        return_value={"count": cached_count, "expires_at": datetime.now(UTC) + timedelta(hours=1)}
    )
    db.fetchval = AsyncMock()
    db.execute = AsyncMock()
    return db


def _make_db_expired(similarity_count: int = 7) -> MagicMock:
    """DB mock: cache row present but expired; fetchval returns similarity_count."""
    db = MagicMock()
    db.fetchrow = AsyncMock(
        return_value={"count": 99, "expires_at": datetime.now(UTC) - timedelta(hours=1)}
    )
    db.fetchval = AsyncMock(return_value=similarity_count)
    db.execute = AsyncMock()
    return db


class TestCountOrgDuplicates:
    async def test_cache_miss_queries_db(self):
        db = _make_db_miss(5)
        result = await count_org_duplicates(db)
        assert result == 5
        db.fetchval.assert_awaited_once()

    async def test_cache_hit_skips_similarity_query(self):
        db = _make_db_hit(3)
        result = await count_org_duplicates(db)
        assert result == 3
        db.fetchval.assert_not_awaited()

    async def test_expired_cache_re_queries(self):
        db = _make_db_expired(7)
        result = await count_org_duplicates(db)
        assert result == 7
        db.fetchval.assert_awaited_once()

    async def test_cache_miss_upserts_result(self):
        db = _make_db_miss(4)
        await count_org_duplicates(db)
        db.execute.assert_awaited_once()

    async def test_expired_cache_upserts_fresh_result(self):
        db = _make_db_expired(6)
        await count_org_duplicates(db)
        db.execute.assert_awaited_once()

    async def test_cache_hit_does_not_upsert(self):
        db = _make_db_hit(3)
        await count_org_duplicates(db)
        db.execute.assert_not_awaited()

    async def test_returns_zero_count(self):
        db = _make_db_miss(0)
        assert await count_org_duplicates(db) == 0

    async def test_upsert_passes_organization_entity_type(self):
        db = _make_db_miss(2)
        await count_org_duplicates(db)
        call_args = db.execute.await_args
        assert "organization" in str(call_args)

    async def test_count_query_uses_distinct_for_pair_deduplication(self):
        """SQL must wrap DISTINCT a.id, b.id to avoid count inflation from multi-name joins."""
        db = _make_db_miss(1)
        await count_org_duplicates(db)
        sql = db.fetchval.await_args[0][0]
        assert "DISTINCT" in sql.upper()
        assert "a.id" in sql
        assert "b.id" in sql


class TestInvalidateDupCountCache:
    async def test_executes_db_update(self):
        db = MagicMock()
        db.execute = AsyncMock()
        await invalidate_dup_count_cache(db)
        db.execute.assert_awaited_once()

    async def test_targets_organization_entity_type(self):
        db = MagicMock()
        db.execute = AsyncMock()
        await invalidate_dup_count_cache(db)
        assert "organization" in str(db.execute.await_args)


class TestGetOrgDupCount:
    async def test_returns_count_on_success(self):
        db = _make_db_miss(4)
        result = await get_org_dup_count(db=db)
        assert result == 4

    async def test_returns_cached_count(self):
        db = _make_db_hit(6)
        result = await get_org_dup_count(db=db)
        assert result == 6
        db.fetchval.assert_not_awaited()

    async def test_returns_zero_on_fetchrow_error(self):
        db = MagicMock()
        db.fetchrow = AsyncMock(side_effect=Exception("pg_trgm not installed"))
        result = await get_org_dup_count(db=db)
        assert result == 0

    async def test_returns_zero_on_fetchval_error(self):
        """Cache miss followed by failing similarity query returns 0, does not upsert."""
        db = MagicMock()
        db.fetchrow = AsyncMock(return_value=None)
        db.fetchval = AsyncMock(side_effect=Exception("similarity() unavailable"))
        db.execute = AsyncMock()
        result = await get_org_dup_count(db=db)
        assert result == 0
        db.execute.assert_not_awaited()

    async def test_fetchrow_error_does_not_upsert(self):
        db = MagicMock()
        db.fetchrow = AsyncMock(side_effect=Exception("oops"))
        db.execute = AsyncMock()
        await get_org_dup_count(db=db)
        db.execute.assert_not_awaited()

    async def test_logs_warning_on_exception(self, caplog):
        db = MagicMock()
        db.fetchrow = AsyncMock(side_effect=RuntimeError("boom"))
        with caplog.at_level(logging.WARNING, logger="src.api.admin.org_dups"):
            result = await get_org_dup_count(db=db)
        assert result == 0
        assert any(r.levelno == logging.WARNING and r.exc_info is not None for r in caplog.records)
