"""Tests for src/core/classification_drainer.py — the in-process drainer (#877).

The drainer is driven with a fake pool and a fake drain; nothing here touches
the database or Jev. ``wake()`` must never fail and never block.
"""

import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

import pytest

from src.core import classification as cls
from src.core import classification_drainer as cd

ROOT = Path(__file__).resolve().parents[2]


class FakePool:
    @asynccontextmanager
    async def acquire(self):
        yield object()


def _recorder(calls, *, fail=False):
    async def fake_drain(conn, *, origin, limit):
        calls.append((origin, limit))
        if fail:
            raise RuntimeError("db went away")
        return cls.DrainResult(status="idle")

    return fake_drain


async def _settle(cond, timeout=2.0):
    for _ in range(int(timeout / 0.01)):
        if cond():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("condition never held")


def test_wake_without_a_drainer_is_a_no_op():
    cd.wake()  # no running drainer: nothing to wake, nothing raised


async def test_wake_runs_a_drain_after_the_debounce():
    calls: list = []
    d = cd.Drainer(FakePool(), tick=60, debounce=0.05, drain_fn=_recorder(calls))
    d.start()
    try:
        cd.wake()
        cd.wake()  # a burst is one drain
        await _settle(lambda: calls)
        await asyncio.sleep(0.1)
        assert calls == [("wake", cd.IN_PROCESS_LIMIT)]
    finally:
        await d.stop()


async def test_the_tick_runs_a_drain_unwoken():
    calls: list = []
    d = cd.Drainer(FakePool(), tick=0.05, debounce=0, drain_fn=_recorder(calls))
    d.start()
    try:
        await _settle(lambda: len(calls) >= 2)
        assert calls[0] == ("tick", cd.IN_PROCESS_LIMIT)
    finally:
        await d.stop()


async def test_a_failed_drain_logs_a_warning_and_the_loop_goes_on(caplog):
    caplog.set_level(logging.WARNING)
    calls: list = []
    d = cd.Drainer(FakePool(), tick=0.05, debounce=0, drain_fn=_recorder(calls, fail=True))
    d.start()
    try:
        await _settle(lambda: len(calls) >= 2)
    finally:
        await d.stop()
    assert "classification drain failed" in caplog.text


async def test_stop_ends_the_task_and_wake_goes_quiet():
    calls: list = []
    d = cd.Drainer(FakePool(), tick=60, debounce=0, drain_fn=_recorder(calls))
    d.start()
    await d.stop()
    cd.wake()
    await asyncio.sleep(0.05)
    assert calls == []


def test_every_src_org_insert_wakes_the_drainer():
    """Each in-process path that creates an org calls wake(); scripts are out of process."""
    creators = [
        p for p in (ROOT / "src").rglob("*.py") if "INSERT INTO organizations" in p.read_text()
    ]
    assert creators, "the sweep found no org-creating module"
    missing = [str(p.relative_to(ROOT)) for p in creators if "wake()" not in p.read_text()]
    assert missing == []


@pytest.mark.parametrize("name", ["observation.py", "ingestion/pipeline.py"])
def test_sweep_covers_the_known_creators(name):
    assert "INSERT INTO organizations" in (ROOT / "src/core" / name).read_text()
