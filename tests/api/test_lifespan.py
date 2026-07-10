"""Tests for the FastAPI app lifespan (#286 shutdown drain ordering)."""

from unittest.mock import AsyncMock, patch

import src.api.main as main
import src.core.db as db


async def test_lifespan_drains_capture_writes_before_closing_pool(monkeypatch):
    """Shutdown drains in-flight ``api_request_log`` writes before the pool closes.

    Ordering is load-bearing (#286): the fire-and-forget writes acquire pool
    connections, so draining after ``close_pool`` would guarantee they fail.
    """
    monkeypatch.delenv("DATABASE_URL", raising=False)  # skip real pool creation

    calls: list[str] = []

    async def record_drain(*_args, **_kwargs):
        calls.append("drain")

    async def record_close():
        calls.append("close_pool")

    with (
        patch.object(main, "drain_pending_writes", AsyncMock(side_effect=record_drain)),
        patch.object(db, "close_pool", AsyncMock(side_effect=record_close)),
    ):
        async with main.lifespan(main.app):
            pass

    assert calls == ["drain", "close_pool"]
