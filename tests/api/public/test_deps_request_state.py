"""Unit tests: auth deps stash request.state.api_key_id for the capture middleware (#260)."""

import pytest
from fastapi import HTTPException

from src.api.public.deps import require_api_key, require_key, require_scope


class _FakeState:
    pass


class _FakeRequest:
    def __init__(self):
        self.state = _FakeState()


class _FakeDB:
    """Minimal async db stub: returns a fixed api_keys row and a scope grant toggle."""

    def __init__(self, key_row, has_scope=True):
        self._key_row = key_row
        self._has_scope = has_scope
        self.executed = []

    async def fetchrow(self, query, *args):
        if "api_key_scopes" in query:
            return {"exists": 1} if self._has_scope else None
        return self._key_row

    async def execute(self, query, *args):
        self.executed.append((query, args))


_KEY_ROW = {"id": "key-123", "user_id": "user-abc"}


async def test_require_api_key_sets_state():
    req = _FakeRequest()
    db = _FakeDB(_KEY_ROW)
    await require_api_key(raw_key="pm_valid", db=db, request=req)
    assert req.state.api_key_id == "key-123"


async def test_require_key_sets_state():
    req = _FakeRequest()
    db = _FakeDB(_KEY_ROW)
    await require_key(raw_key="pm_valid", db=db, request=req)
    assert req.state.api_key_id == "key-123"


async def test_require_scope_sets_state():
    req = _FakeRequest()
    db = _FakeDB(_KEY_ROW, has_scope=True)
    check = require_scope("observations:write")
    await check(raw_key="pm_valid", db=db, request=req)
    assert req.state.api_key_id == "key-123"


async def test_auth_failure_leaves_state_unset():
    """Absent key raises 403 before resolution; api_key_id must not be set."""
    req = _FakeRequest()
    db = _FakeDB(_KEY_ROW)
    with pytest.raises(HTTPException) as exc:
        await require_key(raw_key=None, db=db, request=req)
    assert exc.value.status_code == 403
    assert not hasattr(req.state, "api_key_id")
