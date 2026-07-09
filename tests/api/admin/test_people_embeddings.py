"""Integration tests for the admin Person-detail voice-embeddings section (#284).

Read-only surface: list active embeddings, toggle archived, copy full vector,
soft-archive (delete), restore, and hard-delete (requires archived first).
"""

import hashlib
import os

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from src.api.admin.deps import get_db
from src.api.main import app
from src.core.db import generate_id

pytestmark = [pytest.mark.integration]

AUTH_HEADERS = {"X-ExeDev-UserID": "usr_test", "X-ExeDev-Email": "admin@test.com"}
HTMX_HEADERS = {**AUTH_HEADERS, "HX-Request": "true"}

_MODEL_ID = "pyannote-community-1-embed"
_TABLE = "person_embeddings_pyannote_community_1_embed"
_DIM = 256


def _vec_literal(fill: float = 0.125) -> str:
    return "[" + ",".join(str(fill) for _ in range(_DIM)) + "]"


@pytest_asyncio.fixture(loop_scope="session")
async def db(db_pool):
    """Pool-acquired connection wrapped in a rolled-back transaction."""
    async with db_pool.acquire() as conn:
        tr = conn.transaction()
        await tr.start()
        try:
            yield conn
        finally:
            await tr.rollback()


@pytest_asyncio.fixture(loop_scope="session")
async def client(db):
    """AsyncClient with app, overriding get_db to use the test connection."""

    async def _get_db_override():
        yield db

    app.dependency_overrides[get_db] = _get_db_override
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test", follow_redirects=True
    ) as c:
        yield c
    app.dependency_overrides.pop(get_db, None)


@pytest_asyncio.fixture(loop_scope="session")
async def api_key_id(db):
    """Seed an app_user + api_key to satisfy created_by_key_id (NOT NULL FK)."""
    uid = generate_id()
    kid = generate_id()
    raw = "pm_" + os.urandom(16).hex()
    khash = hashlib.sha256(raw.encode()).hexdigest()
    await db.execute("INSERT INTO app_users (id, email) VALUES ($1,$2)", uid, "e284@test.com")
    await db.execute(
        "INSERT INTO api_keys (id, user_id, label, key_prefix, key_hash) VALUES ($1,$2,$3,$4,$5)",
        kid,
        uid,
        "Embed Test Key",
        raw[:8],
        khash,
    )
    yield kid


async def _insert_embedding(db, person_id, key_id, *, archived=False, job_id="job_ABC"):
    eid = generate_id()
    await db.execute(
        f"""
        INSERT INTO {_TABLE}
            (id, person_id, embedding, embedding_dim, activity_ms,
             audio_sample_rate_hz, source_service, source_job_id,
             source_segment, recorded_at, created_by_key_id, archived_at)
        VALUES ($1,$2,$3::vector,$4,$5,$6,$7,$8,$9, now(), $10,
                CASE WHEN $11 THEN now() ELSE NULL END)
        """,
        eid,
        person_id,
        _vec_literal(),
        _DIM,
        1000,
        16000,
        "observo",
        job_id,
        3,
        key_id,
        archived,
    )
    return eid


@pytest_asyncio.fixture(loop_scope="session")
async def person_id(db, api_key_id):
    # Depend on api_key_id purely for teardown ordering: this fixture must
    # delete its embeddings (which FK the key) before api_key_id drops the key.
    pid = generate_id()
    await db.execute("INSERT INTO people (id) VALUES ($1)", pid)
    await db.execute(
        "INSERT INTO person_names (id, person_id, name, is_canonical)"
        " VALUES ($1,$2,'Embed Subject',TRUE)",
        generate_id(),
        pid,
    )
    yield pid


async def _archived_at(db, eid):
    return await db.fetchval(f"SELECT archived_at FROM {_TABLE} WHERE id=$1", eid)


async def _exists(db, eid):
    return await db.fetchval(f"SELECT 1 FROM {_TABLE} WHERE id=$1", eid) is not None


# ---------------------------------------------------------------------------
# Section rendering
# ---------------------------------------------------------------------------


async def test_detail_shows_embeddings_section(client, db, person_id, api_key_id):
    await _insert_embedding(db, person_id, api_key_id)
    r = await client.get(f"/admin/people/{person_id}/", headers=AUTH_HEADERS)
    assert r.status_code == 200
    assert "Voice Embeddings" in r.text
    assert _MODEL_ID in r.text
    assert "observo" in r.text
    assert "job_ABC" in r.text


async def test_active_only_by_default(client, db, person_id, api_key_id):
    active = await _insert_embedding(db, person_id, api_key_id, job_id="job_ACTIVE")
    await _insert_embedding(db, person_id, api_key_id, archived=True, job_id="job_ARCH")
    r = await client.get(f"/admin/people/{person_id}/", headers=AUTH_HEADERS)
    assert "job_ACTIVE" in r.text
    assert "job_ARCH" not in r.text
    assert active  # sanity


async def test_show_archived_toggle_reveals_archived(client, db, person_id, api_key_id):
    await _insert_embedding(db, person_id, api_key_id, archived=True, job_id="job_ARCH")
    r = await client.get(
        f"/admin/people/{person_id}/?show_archived_embeddings=1", headers=AUTH_HEADERS
    )
    assert "job_ARCH" in r.text
    assert "Hide archived" in r.text


async def test_archived_row_renders_restore_and_hard_delete(client, db, person_id, api_key_id):
    await _insert_embedding(db, person_id, api_key_id, archived=True)
    r = await client.get(
        f"/admin/people/{person_id}/?show_archived_embeddings=1", headers=AUTH_HEADERS
    )
    assert "Restore" in r.text
    assert "Delete permanently" in r.text


# ---------------------------------------------------------------------------
# Copy
# ---------------------------------------------------------------------------


async def test_copy_endpoint_returns_full_vector(client, db, person_id, api_key_id):
    eid = await _insert_embedding(db, person_id, api_key_id)
    r = await client.get(
        f"/admin/people/{person_id}/embeddings/{_MODEL_ID}/{eid}/vector/", headers=AUTH_HEADERS
    )
    assert r.status_code == 200
    body = r.text.strip()
    assert body.startswith("[") and body.endswith("]")
    assert body.count(",") == _DIM - 1


async def test_copy_unknown_model_404(client, db, person_id, api_key_id):
    eid = await _insert_embedding(db, person_id, api_key_id)
    r = await client.get(
        f"/admin/people/{person_id}/embeddings/nope-model/{eid}/vector/", headers=AUTH_HEADERS
    )
    assert r.status_code == 404


async def test_copy_missing_row_404(client, person_id):
    r = await client.get(
        f"/admin/people/{person_id}/embeddings/{_MODEL_ID}/{generate_id()}/vector/",
        headers=AUTH_HEADERS,
    )
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# Archive (soft delete)
# ---------------------------------------------------------------------------


async def test_delete_archives(client, db, person_id, api_key_id):
    eid = await _insert_embedding(db, person_id, api_key_id)
    r = await client.delete(
        f"/admin/people/{person_id}/embeddings/{_MODEL_ID}/{eid}/", headers=HTMX_HEADERS
    )
    assert r.status_code == 200
    assert await _archived_at(db, eid) is not None


async def test_archive_response_refreshes_archived_count(client, db, person_id, api_key_id):
    # Archiving the only active row (0 archived → 1) must re-render the section
    # header so the "Show archived (N)" toggle appears with a fresh count (#284
    # CR item 1: whole-section swap, not tbody-only).
    eid = await _insert_embedding(db, person_id, api_key_id)
    r = await client.delete(
        f"/admin/people/{person_id}/embeddings/{_MODEL_ID}/{eid}/", headers=HTMX_HEADERS
    )
    assert r.status_code == 200
    assert 'id="person-embeddings-section"' in r.text
    assert "Show archived (1)" in r.text


async def test_delete_already_archived_409(client, db, person_id, api_key_id):
    eid = await _insert_embedding(db, person_id, api_key_id, archived=True)
    r = await client.delete(
        f"/admin/people/{person_id}/embeddings/{_MODEL_ID}/{eid}/", headers=HTMX_HEADERS
    )
    assert r.status_code == 409


# ---------------------------------------------------------------------------
# Restore
# ---------------------------------------------------------------------------


async def test_restore_unarchives(client, db, person_id, api_key_id):
    eid = await _insert_embedding(db, person_id, api_key_id, archived=True)
    r = await client.post(
        f"/admin/people/{person_id}/embeddings/{_MODEL_ID}/{eid}/restore/", headers=HTMX_HEADERS
    )
    assert r.status_code == 200
    assert await _archived_at(db, eid) is None


async def test_restore_active_409(client, db, person_id, api_key_id):
    eid = await _insert_embedding(db, person_id, api_key_id)
    r = await client.post(
        f"/admin/people/{person_id}/embeddings/{_MODEL_ID}/{eid}/restore/", headers=HTMX_HEADERS
    )
    assert r.status_code == 409


# ---------------------------------------------------------------------------
# Hard delete (requires archived)
# ---------------------------------------------------------------------------


async def test_hard_delete_active_409(client, db, person_id, api_key_id):
    eid = await _insert_embedding(db, person_id, api_key_id)
    r = await client.delete(
        f"/admin/people/{person_id}/embeddings/{_MODEL_ID}/{eid}/permanent/", headers=HTMX_HEADERS
    )
    assert r.status_code == 409
    assert await _exists(db, eid)


async def test_hard_delete_archived_removes_row(client, db, person_id, api_key_id):
    eid = await _insert_embedding(db, person_id, api_key_id, archived=True)
    r = await client.delete(
        f"/admin/people/{person_id}/embeddings/{_MODEL_ID}/{eid}/permanent/", headers=HTMX_HEADERS
    )
    assert r.status_code == 200
    assert not await _exists(db, eid)


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------


async def test_vector_requires_admin_auth(client):
    r = await client.get(
        f"/admin/people/{generate_id()}/embeddings/{_MODEL_ID}/{generate_id()}/vector/",
        follow_redirects=False,
    )
    assert r.status_code == 307
