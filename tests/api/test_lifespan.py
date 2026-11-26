"""Tests for the FastAPI app lifespan (#286 shutdown drain ordering)."""

import os
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


async def _lifespan_with_fake_pool(monkeypatch, calls):
    """Run the lifespan with DATABASE_URL set but no real pool or registry."""
    monkeypatch.setenv("DATABASE_URL", "postgresql://fake/db")

    class _Conn:
        async def __aenter__(self):
            return object()

        async def __aexit__(self, *exc):
            return False

    class _Pool:
        def acquire(self):
            return _Conn()

    async def record_stop(self):
        calls.append("drainer_stop")

    def record_start(self):
        calls.append("drainer_start")

    async def record_drain(*_a, **_k):
        calls.append("drain")

    async def record_close():
        calls.append("close_pool")

    with (
        patch.object(db, "create_pool", AsyncMock(return_value=_Pool())),
        patch.object(main.EmbeddingRegistry, "load", AsyncMock(return_value=None)),
        patch.object(main.Drainer, "start", record_start),
        patch.object(main.Drainer, "stop", record_stop),
        patch.object(main, "drain_pending_writes", AsyncMock(side_effect=record_drain)),
        patch.object(db, "close_pool", AsyncMock(side_effect=record_close)),
    ):
        async with main.lifespan(main.app):
            calls.append("serving")


async def test_lifespan_runs_the_classification_drainer(monkeypatch):
    """#877: a worker with a pool runs a drainer, stopped before the pool closes."""
    monkeypatch.delenv("CLASSIFICATION_DRAINER", raising=False)
    calls: list[str] = []
    await _lifespan_with_fake_pool(monkeypatch, calls)
    assert calls == ["drainer_start", "serving", "drainer_stop", "drain", "close_pool"]


async def test_classification_drainer_off_switch(monkeypatch):
    monkeypatch.setenv("CLASSIFICATION_DRAINER", "off")
    calls: list[str] = []
    await _lifespan_with_fake_pool(monkeypatch, calls)
    assert calls == ["serving", "drain", "close_pool"]


def test_tests_run_with_the_drainer_off():
    assert os.environ.get("CLASSIFICATION_DRAINER") == "off"
