"""Integration tests for the admin Person-detail voice-embeddings section (#284).

Read-only surface: list active embeddings, toggle archived, copy full vector,
soft-archive (delete), restore, and hard-delete (requires archived first).
"""

import hashlib
import os

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient

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


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


@pytest_asyncio.fixture(loop_scope="session")
async def api_key_id(db_pool):
    """Seed an app_user + api_key to satisfy created_by_key_id (NOT NULL FK)."""
    uid = generate_id()
    kid = generate_id()
    raw = "pm_" + os.urandom(16).hex()
    khash = hashlib.sha256(raw.encode()).hexdigest()
    async with db_pool.acquire() as conn:
        await conn.execute("INSERT INTO app_users (id, email) VALUES ($1,$2)", uid, "e284@test.com")
        await conn.execute(
            "INSERT INTO api_keys (id, user_id, label, key_prefix, key_hash)"
            " VALUES ($1,$2,$3,$4,$5)",
            kid,
            uid,
            "Embed Test Key",
            raw[:8],
            khash,
        )
    yield kid
    async with db_pool.acquire() as conn:
        await conn.execute("DELETE FROM api_keys WHERE id=$1", kid)
        await conn.execute("DELETE FROM app_users WHERE id=$1", uid)


async def _insert_embedding(db_pool, person_id, key_id, *, archived=False, job_id="job_ABC"):
    eid = generate_id()
    async with db_pool.acquire() as conn:
        await conn.execute(
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
async def person_id(db_pool, api_key_id):
    # Depend on api_key_id purely for teardown ordering: this fixture must
    # delete its embeddings (which FK the key) before api_key_id drops the key.
    pid = generate_id()
    async with db_pool.acquire() as conn:
        await conn.execute("INSERT INTO people (id) VALUES ($1)", pid)
        await conn.execute(
            "INSERT INTO person_names (id, person_id, name, is_canonical)"
            " VALUES ($1,$2,'Embed Subject',TRUE)",
            generate_id(),
            pid,
        )
    yield pid
    async with db_pool.acquire() as conn:
        await conn.execute(f"DELETE FROM {_TABLE} WHERE person_id=$1", pid)
        await conn.execute("DELETE FROM person_names WHERE person_id=$1", pid)
        await conn.execute("DELETE FROM people WHERE id=$1", pid)


async def _archived_at(db_pool, eid):
    async with db_pool.acquire() as conn:
        return await conn.fetchval(f"SELECT archived_at FROM {_TABLE} WHERE id=$1", eid)


async def _exists(db_pool, eid):
    async with db_pool.acquire() as conn:
        return await conn.fetchval(f"SELECT 1 FROM {_TABLE} WHERE id=$1", eid) is not None


# ---------------------------------------------------------------------------
# Section rendering
# ---------------------------------------------------------------------------


async def test_detail_shows_embeddings_section(client, db_pool, person_id, api_key_id):
    await _insert_embedding(db_pool, person_id, api_key_id)
    r = client.get(f"/admin/people/{person_id}/", headers=AUTH_HEADERS)
    assert r.status_code == 200
    assert "Voice Embeddings" in r.text
    assert _MODEL_ID in r.text
    assert "observo" in r.text
    assert "job_ABC" in r.text


async def test_active_only_by_default(client, db_pool, person_id, api_key_id):
    active = await _insert_embedding(db_pool, person_id, api_key_id, job_id="job_ACTIVE")
    await _insert_embedding(db_pool, person_id, api_key_id, archived=True, job_id="job_ARCH")
    r = client.get(f"/admin/people/{person_id}/", headers=AUTH_HEADERS)
    assert "job_ACTIVE" in r.text
    assert "job_ARCH" not in r.text
    assert active  # sanity


async def test_show_archived_toggle_reveals_archived(client, db_pool, person_id, api_key_id):
    await _insert_embedding(db_pool, person_id, api_key_id, archived=True, job_id="job_ARCH")
    r = client.get(f"/admin/people/{person_id}/?show_archived_embeddings=1", headers=AUTH_HEADERS)
    assert "job_ARCH" in r.text
    assert "Hide archived" in r.text


# ---------------------------------------------------------------------------
# Copy
# ---------------------------------------------------------------------------


async def test_copy_endpoint_returns_full_vector(client, db_pool, person_id, api_key_id):
    eid = await _insert_embedding(db_pool, person_id, api_key_id)
    r = client.get(
        f"/admin/people/{person_id}/embeddings/{_MODEL_ID}/{eid}/vector/", headers=AUTH_HEADERS
    )
    assert r.status_code == 200
    body = r.text.strip()
    assert body.startswith("[") and body.endswith("]")
    assert body.count(",") == _DIM - 1


async def test_copy_unknown_model_404(client, db_pool, person_id, api_key_id):
    eid = await _insert_embedding(db_pool, person_id, api_key_id)
    r = client.get(
        f"/admin/people/{person_id}/embeddings/nope-model/{eid}/vector/", headers=AUTH_HEADERS
    )
    assert r.status_code == 404


async def test_copy_missing_row_404(client, person_id):
    r = client.get(
        f"/admin/people/{person_id}/embeddings/{_MODEL_ID}/{generate_id()}/vector/",
        headers=AUTH_HEADERS,
    )
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# Archive (soft delete)
# ---------------------------------------------------------------------------


async def test_delete_archives(client, db_pool, person_id, api_key_id):
    eid = await _insert_embedding(db_pool, person_id, api_key_id)
    r = client.delete(
        f"/admin/people/{person_id}/embeddings/{_MODEL_ID}/{eid}/", headers=HTMX_HEADERS
    )
    assert r.status_code == 200
    assert await _archived_at(db_pool, eid) is not None


async def test_delete_already_archived_409(client, db_pool, person_id, api_key_id):
    eid = await _insert_embedding(db_pool, person_id, api_key_id, archived=True)
    r = client.delete(
        f"/admin/people/{person_id}/embeddings/{_MODEL_ID}/{eid}/", headers=HTMX_HEADERS
    )
    assert r.status_code == 409


# ---------------------------------------------------------------------------
# Restore
# ---------------------------------------------------------------------------


async def test_restore_unarchives(client, db_pool, person_id, api_key_id):
    eid = await _insert_embedding(db_pool, person_id, api_key_id, archived=True)
    r = client.post(
        f"/admin/people/{person_id}/embeddings/{_MODEL_ID}/{eid}/restore/", headers=HTMX_HEADERS
    )
    assert r.status_code == 200
    assert await _archived_at(db_pool, eid) is None


async def test_restore_active_409(client, db_pool, person_id, api_key_id):
    eid = await _insert_embedding(db_pool, person_id, api_key_id)
    r = client.post(
        f"/admin/people/{person_id}/embeddings/{_MODEL_ID}/{eid}/restore/", headers=HTMX_HEADERS
    )
    assert r.status_code == 409


# ---------------------------------------------------------------------------
# Hard delete (requires archived)
# ---------------------------------------------------------------------------


async def test_hard_delete_active_409(client, db_pool, person_id, api_key_id):
    eid = await _insert_embedding(db_pool, person_id, api_key_id)
    r = client.delete(
        f"/admin/people/{person_id}/embeddings/{_MODEL_ID}/{eid}/permanent/", headers=HTMX_HEADERS
    )
    assert r.status_code == 409
    assert await _exists(db_pool, eid)


async def test_hard_delete_archived_removes_row(client, db_pool, person_id, api_key_id):
    eid = await _insert_embedding(db_pool, person_id, api_key_id, archived=True)
    r = client.delete(
        f"/admin/people/{person_id}/embeddings/{_MODEL_ID}/{eid}/permanent/", headers=HTMX_HEADERS
    )
    assert r.status_code == 200
    assert not await _exists(db_pool, eid)


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------


def test_vector_requires_admin_auth(client):
    r = client.get(
        f"/admin/people/{generate_id()}/embeddings/{_MODEL_ID}/{generate_id()}/vector/",
        follow_redirects=False,
    )
    assert r.status_code == 307
