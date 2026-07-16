"""Integration tests for src.core.anomaly — per-key request-volume surfacing (#294)."""

import hashlib
import os

import pytest
import pytest_asyncio

from src.core.anomaly import key_activity
from src.core.db import generate_id

pytestmark = [pytest.mark.integration]


@pytest_asyncio.fixture(loop_scope="session")
async def db(db_pool):
    """Pool-acquired connection wrapped in a rolled-back transaction.

    ``api_request_log`` accretes rows from other suite tests (capture middleware
    integration tests commit rows). Emptying it inside this rolled-back
    transaction isolates the per-key aggregates without durably deleting.
    """
    async with db_pool.acquire() as conn:
        tr = conn.transaction()
        await tr.start()
        try:
            await conn.execute("DELETE FROM api_request_log")
            yield conn
        finally:
            await tr.rollback()


async def _seed_key(conn, label):
    """Insert an app user + API key; return the key id."""
    uid, kid = generate_id(), generate_id()
    raw = "pm_" + os.urandom(8).hex()
    await conn.execute(
        "INSERT INTO app_users (id, email) VALUES ($1, $2)", uid, f"{uid}@anomaly.test"
    )
    await conn.execute(
        "INSERT INTO api_keys (id, user_id, label, key_prefix, key_hash) VALUES ($1,$2,$3,$4,$5)",
        kid,
        uid,
        label,
        raw[:8],
        hashlib.sha256(raw.encode()).hexdigest(),
    )
    return kid


async def _insert_log(conn, api_key_id, *, status_code=200, minutes_old=0):
    await conn.execute(
        "INSERT INTO api_request_log "
        "(api_key_id, method, path, route_group, status_code, latency_ms, occurred_at) "
        "VALUES ($1, 'GET', '/api/v1/changes', 'changes', $2, 1, "
        " NOW() - make_interval(mins => $3::int))",
        api_key_id,
        status_code,
        minutes_old,
    )


async def test_counts_per_key_within_window(db):
    kid_a = await _seed_key(db, "Key A")
    kid_b = await _seed_key(db, "Key B")
    for _ in range(3):
        await _insert_log(db, kid_a)
    for _ in range(5):
        await _insert_log(db, kid_b)
    # Outside the 1h window — must not count.
    await _insert_log(db, kid_a, minutes_old=90)

    rows = await key_activity(db, window_hours=1)
    counts = {r.api_key_id: r.request_count for r in rows}
    assert counts == {kid_a: 3, kid_b: 5}


async def test_orders_hottest_key_first(db):
    kid_cool = await _seed_key(db, "Cool")
    kid_hot = await _seed_key(db, "Hot")
    await _insert_log(db, kid_cool)
    for _ in range(4):
        await _insert_log(db, kid_hot)

    rows = await key_activity(db, window_hours=1)
    assert [r.api_key_id for r in rows] == [kid_hot, kid_cool]


async def test_throttled_count_counts_429s(db):
    kid = await _seed_key(db, "Bursty")
    await _insert_log(db, kid)
    await _insert_log(db, kid, status_code=429)
    await _insert_log(db, kid, status_code=429)

    rows = await key_activity(db, window_hours=1)
    assert rows[0].request_count == 3
    assert rows[0].throttled_count == 2


async def test_unauthenticated_rows_grouped_under_null_key(db):
    await _insert_log(db, None)
    await _insert_log(db, None, status_code=401)

    rows = await key_activity(db, window_hours=1)
    assert len(rows) == 1
    assert rows[0].api_key_id is None
    assert rows[0].key_label is None
    assert rows[0].request_count == 2


async def test_key_label_resolved(db):
    kid = await _seed_key(db, "USA-WA API Key")
    await _insert_log(db, kid)

    rows = await key_activity(db, window_hours=1)
    assert rows[0].key_label == "USA-WA API Key"


async def test_last_seen_is_most_recent(db):
    kid = await _seed_key(db, "Recent")
    await _insert_log(db, kid, minutes_old=30)
    await _insert_log(db, kid, minutes_old=5)

    rows = await key_activity(db, window_hours=1)
    latest = await db.fetchval(
        "SELECT MAX(occurred_at) FROM api_request_log WHERE api_key_id = $1", kid
    )
    assert rows[0].last_seen == latest


async def test_wider_window_includes_older_rows(db):
    kid = await _seed_key(db, "Old Traffic")
    await _insert_log(db, kid, minutes_old=90)

    assert await key_activity(db, window_hours=1) == []
    rows = await key_activity(db, window_hours=24)
    assert rows[0].request_count == 1
