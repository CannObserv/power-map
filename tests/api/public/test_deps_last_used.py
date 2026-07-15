"""Unit tests: last_used_at UPDATE debounce in the auth deps (#292).

``api_keys.last_used_at`` is a display-only freshness stamp, yet it was
UPDATEd on every authenticated request (1.7M single-row updates in the week
audited). The dep now stamps at most once per debounce window per worker.
Direct-call unit tests with a mocked db — the rollback-client fixtures freeze
``NOW()``, so DB-level timing assertions can't distinguish anyway.
"""

from src.api.public import deps as deps_mod
from src.api.public.deps import _resolve_api_key


class _FakeDB:
    def __init__(self):
        self.executed = []

    async def fetchrow(self, query, *args):
        return {"id": "key-debounce", "user_id": "user-1"}

    async def execute(self, query, *args):
        self.executed.append((query, args))


async def test_first_request_stamps_last_used():
    db = _FakeDB()
    await _resolve_api_key("pm_x", db)
    assert len(db.executed) == 1
    assert "last_used_at" in db.executed[0][0]


async def test_repeat_request_within_window_skips_update():
    db = _FakeDB()
    await _resolve_api_key("pm_x", db)
    await _resolve_api_key("pm_x", db)
    assert len(db.executed) == 1


def test_should_stamp_after_window_elapses():
    assert deps_mod._should_stamp_last_used("k1", now_s=100.0)
    assert not deps_mod._should_stamp_last_used("k1", now_s=100.0 + 30)
    assert deps_mod._should_stamp_last_used("k1", now_s=100.0 + 61)


def test_should_stamp_isolated_per_key():
    assert deps_mod._should_stamp_last_used("k1", now_s=100.0)
    assert deps_mod._should_stamp_last_used("k2", now_s=100.0)


def test_zero_window_disables_debounce(monkeypatch):
    monkeypatch.setattr(deps_mod, "_LAST_USED_DEBOUNCE_S", 0.0)
    assert deps_mod._should_stamp_last_used("k1", now_s=100.0)
    assert deps_mod._should_stamp_last_used("k1", now_s=100.0)
