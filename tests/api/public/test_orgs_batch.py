"""POST /api/v1/orgs/batch-get (#654): set-based, so a call's query count is flat in N.

The outcome contract is tested through the generated client in
``test_generated_client.py``; this module checks the property that the batch
exists for. One request per sweep is only worth having if the server does not
turn it back into one round trip per id.
"""

import hashlib
import os

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from src.api.deps import get_db
from src.api.main import app
from src.core.db import generate_id

pytestmark = pytest.mark.integration

_URL = "/api/v1/orgs/batch-get"


class _CountingConnection:
    """Delegates to an asyncpg connection, counting the statements it runs."""

    _COUNTED = frozenset({"fetch", "fetchrow", "fetchval", "execute", "executemany"})

    def __init__(self, conn):
        self._conn = conn
        self.statements = 0

    def __getattr__(self, name):
        attr = getattr(self._conn, name)
        if name in self._COUNTED:
            self.statements += 1
        return attr


@pytest_asyncio.fixture(loop_scope="session")
async def api_key(db):
    uid, kid = generate_id(), generate_id()
    raw_key = "pm_" + os.urandom(16).hex()
    await db.execute("INSERT INTO app_users (id, email) VALUES ($1,$2)", uid, "batch@test.com")
    await db.execute(
        "INSERT INTO api_keys (id, user_id, label, key_prefix, key_hash) VALUES ($1,$2,$3,$4,$5)",
        kid,
        uid,
        "Batch Test Key",
        raw_key[:8],
        hashlib.sha256(raw_key.encode()).hexdigest(),
    )
    return raw_key


async def _org_with_children(db, name: str) -> str:
    org_id = generate_id()
    await db.execute("INSERT INTO organizations (id) VALUES ($1)", org_id)
    await db.execute(
        "INSERT INTO organization_names (id, organization_id, name, name_type, is_canonical)"
        " VALUES ($1,$2,$3,'legal',TRUE)",
        generate_id(),
        org_id,
        name,
    )
    await db.execute(
        "INSERT INTO organization_acronyms (id, organization_id, acronym, is_canonical)"
        " VALUES ($1,$2,$3,TRUE)",
        generate_id(),
        org_id,
        "QBX",
    )
    return org_id


async def _statements_for(db, api_key: str, ids: list[str]) -> tuple[int, list[str]]:
    """Statements one batch call runs, and the outcomes it answered."""
    counting = _CountingConnection(db)

    async def _override():
        yield counting

    app.dependency_overrides[get_db] = _override
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.post(_URL, json={"ids": ids}, headers={"X-API-Key": api_key})
    finally:
        app.dependency_overrides.pop(get_db, None)
    assert r.status_code == 200, r.text
    return counting.statements, [x["outcome"] for x in r.json()["results"]]


@pytest_asyncio.fixture(loop_scope="session")
async def warm_key(db, api_key):
    """The key after one call: the first stamps ``last_used_at``, later ones are
    debounced, so only calls after this one have comparable statement counts."""
    await _statements_for(db, api_key, [generate_id()])
    return api_key


async def test_query_count_does_not_grow_with_ids(db, warm_key):
    api_key = warm_key
    ids = [await _org_with_children(db, f"Quillfeather Count Board {i}") for i in range(10)]
    one, outcomes_one = await _statements_for(db, api_key, ids[:1])
    ten, outcomes_ten = await _statements_for(db, api_key, ids)
    assert outcomes_one + outcomes_ten == ["ok"] * 11
    assert one == ten


async def test_misses_share_one_tombstone_query(db, warm_key):
    """Misses share one set-based tombstone read; only a merge walks per id."""
    api_key = warm_key
    org = await _org_with_children(db, "Quillfeather Count Anchor")
    one, _ = await _statements_for(db, api_key, [org, generate_id()])
    nine, outcomes = await _statements_for(db, api_key, [org] + [generate_id() for _ in range(9)])
    assert outcomes == ["ok"] + ["not_found"] * 9
    assert one == nine
