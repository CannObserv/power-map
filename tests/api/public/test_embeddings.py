"""Tests for POST /api/v1/people/identify and POST /api/v1/people/{id}/embeddings."""

import hashlib
import os
import random

import pytest
import pytest_asyncio

from src.api.main import app
from src.core.db import generate_id
from src.core.embedding_registry import EmbeddingRegistry, ModelMeta

pytestmark = pytest.mark.integration

_MODEL_ID = "pyannote-community-1-embed"
_DIM = 256
_TABLE = "person_embeddings_pyannote_community_1_embed"

_FAKE_META = ModelMeta(
    model_id=_MODEL_ID,
    table_name=_TABLE,
    dimension=_DIM,
    metric="cosine",
    accepts_writes=True,
    is_queryable=True,
    operator="<=>",
)
_FAKE_REGISTRY = EmbeddingRegistry({_MODEL_ID: _FAKE_META})
_EMPTY_REGISTRY = EmbeddingRegistry({})


def _rand_embedding(dim: int = _DIM) -> list[float]:
    return [random.gauss(0, 1) for _ in range(dim)]


def _vec_str(v: list[float]) -> str:
    return "[" + ",".join(str(x) for x in v) + "]"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture(loop_scope="session")
async def write_key(db):
    """API key with voice_embeddings:write scope."""
    uid = generate_id()
    kid = generate_id()
    raw = "pm_" + os.urandom(16).hex()
    khash = hashlib.sha256(raw.encode()).hexdigest()
    await db.execute(
        "INSERT INTO app_users (id, email) VALUES ($1,$2)", uid, "embed_write@test.com"
    )
    await db.execute(
        "INSERT INTO api_keys (id, user_id, label, key_prefix, key_hash) VALUES ($1,$2,$3,$4,$5)",
        kid,
        uid,
        "Embed Write Key",
        raw[:8],
        khash,
    )
    await db.execute(
        "INSERT INTO api_key_scopes (api_key_id, scope_id) VALUES ($1,'voice_embeddings:write')",
        kid,
    )
    yield raw
    await db.execute("DELETE FROM api_keys WHERE id=$1", kid)
    await db.execute("DELETE FROM app_users WHERE id=$1", uid)


@pytest_asyncio.fixture(loop_scope="session")
async def read_key(db):
    """API key with voice_embeddings:read scope."""
    uid = generate_id()
    kid = generate_id()
    raw = "pm_" + os.urandom(16).hex()
    khash = hashlib.sha256(raw.encode()).hexdigest()
    await db.execute("INSERT INTO app_users (id, email) VALUES ($1,$2)", uid, "embed_read@test.com")
    await db.execute(
        "INSERT INTO api_keys (id, user_id, label, key_prefix, key_hash) VALUES ($1,$2,$3,$4,$5)",
        kid,
        uid,
        "Embed Read Key",
        raw[:8],
        khash,
    )
    await db.execute(
        "INSERT INTO api_key_scopes (api_key_id, scope_id) VALUES ($1,'voice_embeddings:read')",
        kid,
    )
    yield raw
    await db.execute("DELETE FROM api_keys WHERE id=$1", kid)
    await db.execute("DELETE FROM app_users WHERE id=$1", uid)


@pytest_asyncio.fixture(loop_scope="session")
async def unscoped_key(db):
    """API key with no embedding scopes."""
    uid = generate_id()
    kid = generate_id()
    raw = "pm_" + os.urandom(16).hex()
    khash = hashlib.sha256(raw.encode()).hexdigest()
    await db.execute("INSERT INTO app_users (id, email) VALUES ($1,$2)", uid, "embed_none@test.com")
    await db.execute(
        "INSERT INTO api_keys (id, user_id, label, key_prefix, key_hash) VALUES ($1,$2,$3,$4,$5)",
        kid,
        uid,
        "Embed No-Scope Key",
        raw[:8],
        khash,
    )
    yield raw
    await db.execute("DELETE FROM api_keys WHERE id=$1", kid)
    await db.execute("DELETE FROM app_users WHERE id=$1", uid)


@pytest_asyncio.fixture(loop_scope="session")
async def two_people(db):
    """Two active people for seeding embeddings."""
    ids = [generate_id(), generate_id()]
    for pid in ids:
        await db.execute("INSERT INTO people (id) VALUES ($1)", pid)
    yield ids
    for pid in ids:
        await db.execute("DELETE FROM people WHERE id=$1", pid)


# ---------------------------------------------------------------------------
# Auth / scope guards
# ---------------------------------------------------------------------------


def test_identify_requires_read_scope(client, unscoped_key):
    r = client.post(
        "/api/v1/people/identify",
        json={"model_id": _MODEL_ID, "embedding": _rand_embedding()},
        headers={"X-API-Key": unscoped_key},
    )
    assert r.status_code == 403


def test_identify_rejects_missing_key(unit_client):
    r = unit_client.post(
        "/api/v1/people/identify",
        json={"model_id": _MODEL_ID, "embedding": _rand_embedding()},
    )
    assert r.status_code == 403


def test_write_requires_write_scope(client, read_key):
    pid = generate_id()
    r = client.post(
        f"/api/v1/people/{pid}/embeddings",
        json={
            "model_id": _MODEL_ID,
            "embedding": _rand_embedding(),
            "activity_ms": 1000,
            "audio_sample_rate_hz": 16000,
            "source": {
                "service": "observo",
                "job_id": "j1",
                "segment": 0,
                "recorded_at": "2026-01-01T00:00:00Z",
            },
        },
        headers={"X-API-Key": read_key},
    )
    assert r.status_code == 403


def test_write_rejects_missing_key(unit_client):
    pid = generate_id()
    r = unit_client.post(
        f"/api/v1/people/{pid}/embeddings",
        json={
            "model_id": _MODEL_ID,
            "embedding": _rand_embedding(),
            "activity_ms": 500,
            "audio_sample_rate_hz": 16000,
            "source": {
                "service": "observo",
                "job_id": "j2",
                "segment": 0,
                "recorded_at": "2026-01-01T00:00:00Z",
            },
        },
    )
    assert r.status_code == 403


# ---------------------------------------------------------------------------
# identify — unit-style (real DB but known-empty state)
# ---------------------------------------------------------------------------


def test_identify_empty_returns_empty_matches(client, read_key):
    r = client.post(
        "/api/v1/people/identify",
        json={"model_id": _MODEL_ID, "embedding": _rand_embedding()},
        headers={"X-API-Key": read_key},
    )
    assert r.status_code == 200
    data = r.json()
    assert data["matches"] == []


def test_identify_unknown_model_returns_empty(client, read_key):
    r = client.post(
        "/api/v1/people/identify",
        json={"model_id": "no-such-model", "embedding": _rand_embedding()},
        headers={"X-API-Key": read_key},
    )
    assert r.status_code == 200
    assert r.json()["matches"] == []


def test_identify_dim_mismatch_422(client, read_key):
    r = client.post(
        "/api/v1/people/identify",
        json={"model_id": _MODEL_ID, "embedding": _rand_embedding(dim=64)},
        headers={"X-API-Key": read_key},
    )
    assert r.status_code == 422


def test_identify_top_k_clamped(client, read_key):
    """top_k > 25 is silently clamped; should not 422."""
    r = client.post(
        "/api/v1/people/identify",
        json={"model_id": _MODEL_ID, "embedding": _rand_embedding(), "top_k": 999},
        headers={"X-API-Key": read_key},
    )
    assert r.status_code == 200


# ---------------------------------------------------------------------------
# write — 404 / 422
# ---------------------------------------------------------------------------


def test_write_404_unknown_person(client, write_key):
    r = client.post(
        f"/api/v1/people/{generate_id()}/embeddings",
        json={
            "model_id": _MODEL_ID,
            "embedding": _rand_embedding(),
            "activity_ms": 500,
            "audio_sample_rate_hz": 16000,
            "source": {
                "service": "observo",
                "job_id": "j_404",
                "segment": 0,
                "recorded_at": "2026-01-01T00:00:00Z",
            },
        },
        headers={"X-API-Key": write_key},
    )
    assert r.status_code == 404


def test_write_422_dim_mismatch(client, write_key, two_people):
    pid = two_people[0]
    r = client.post(
        f"/api/v1/people/{pid}/embeddings",
        json={
            "model_id": _MODEL_ID,
            "embedding": _rand_embedding(dim=64),
            "activity_ms": 500,
            "audio_sample_rate_hz": 16000,
            "source": {
                "service": "observo",
                "job_id": "j_dim",
                "segment": 0,
                "recorded_at": "2026-01-01T00:00:00Z",
            },
        },
        headers={"X-API-Key": write_key},
    )
    assert r.status_code == 422


def test_write_422_unknown_model(client, write_key, two_people):
    pid = two_people[0]
    r = client.post(
        f"/api/v1/people/{pid}/embeddings",
        json={
            "model_id": "no-such-model",
            "embedding": _rand_embedding(),
            "activity_ms": 500,
            "audio_sample_rate_hz": 16000,
            "source": {
                "service": "observo",
                "job_id": "j_model",
                "segment": 0,
                "recorded_at": "2026-01-01T00:00:00Z",
            },
        },
        headers={"X-API-Key": write_key},
    )
    assert r.status_code == 422


# ---------------------------------------------------------------------------
# write success + idempotency + identify results
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture(loop_scope="session")
async def seeded_embeddings(db, two_people, write_key, client):
    """Seed 3 embeddings for person[0], 2 for person[1].

    Returns (person_ids, embedding_ids, query_embedding) where query_embedding
    is identical to embedding 0 of person[0] (similarity should be 1.0).
    """
    pid0, pid1 = two_people
    embedding_ids: list[str] = []
    query_vec = _rand_embedding()

    seedings = [
        (pid0, query_vec, "observo", "job_seed", 0),
        (pid0, _rand_embedding(), "observo", "job_seed", 1),
        (pid0, _rand_embedding(), "observo", "job_seed", 2),
        (pid1, _rand_embedding(), "observo", "job_seed", 3),
        (pid1, _rand_embedding(), "observo", "job_seed", 4),
    ]
    for pid, emb, svc, job, seg in seedings:
        r = client.post(
            f"/api/v1/people/{pid}/embeddings",
            json={
                "model_id": _MODEL_ID,
                "embedding": emb,
                "activity_ms": 500,
                "audio_sample_rate_hz": 16000,
                "source": {
                    "service": svc,
                    "job_id": job,
                    "segment": seg,
                    "recorded_at": "2026-06-01T00:00:00Z",
                },
            },
            headers={"X-API-Key": write_key},
        )
        assert r.status_code == 200, r.text
        embedding_ids.append(r.json()["embedding_id"])

    yield pid0, pid1, embedding_ids, query_vec

    await db.execute(f"DELETE FROM {_TABLE} WHERE source_job_id='job_seed'")


def test_write_returns_embedding_id(seeded_embeddings):
    _, _, embedding_ids, _ = seeded_embeddings
    assert len(embedding_ids) == 5
    for eid in embedding_ids:
        assert len(eid) == 26


def test_write_duplicate_returns_200_with_same_id(client, write_key, seeded_embeddings):
    pid0, _, embedding_ids, query_vec = seeded_embeddings
    r = client.post(
        f"/api/v1/people/{pid0}/embeddings",
        json={
            "model_id": _MODEL_ID,
            "embedding": query_vec,
            "activity_ms": 999,
            "audio_sample_rate_hz": 8000,
            "source": {
                "service": "observo",
                "job_id": "job_seed",
                "segment": 0,
                "recorded_at": "2026-01-01T00:00:00Z",
            },
        },
        headers={"X-API-Key": write_key},
    )
    assert r.status_code == 200
    assert r.json()["embedding_id"] == embedding_ids[0]


def test_identify_returns_top_k_matches(client, read_key, seeded_embeddings):
    _, _, _, query_vec = seeded_embeddings
    r = client.post(
        "/api/v1/people/identify",
        json={"model_id": _MODEL_ID, "embedding": query_vec, "top_k": 3},
        headers={"X-API-Key": read_key},
    )
    assert r.status_code == 200
    matches = r.json()["matches"]
    assert len(matches) == 3
    # First match should be the exact vector (similarity == 1.0)
    assert abs(matches[0]["similarity"] - 1.0) < 1e-4
    for m in matches:
        assert "person_id" in m
        assert "embedding_id" in m
        assert "similarity" in m
        assert "source_job_id" in m
        assert "recorded_at" in m


async def test_identify_excludes_archived(
    client, db, read_key, write_key, two_people, seeded_embeddings
):
    """Archived embeddings must not appear in identify results."""
    _, _, embedding_ids, query_vec = seeded_embeddings
    await db.execute(
        f"UPDATE {_TABLE} SET archived_at = now() WHERE id = ANY($1::text[])",
        embedding_ids,
    )
    try:
        r = client.post(
            "/api/v1/people/identify",
            json={"model_id": _MODEL_ID, "embedding": query_vec},
            headers={"X-API-Key": read_key},
        )
        assert r.status_code == 200
        assert r.json()["matches"] == []
    finally:
        await db.execute(
            f"UPDATE {_TABLE} SET archived_at = NULL WHERE id = ANY($1::text[])",
            embedding_ids,
        )


# ---------------------------------------------------------------------------
# GET /people/{id} — voice_embeddings_count
# ---------------------------------------------------------------------------


def test_person_detail_has_voice_embeddings_count(client, read_key, seeded_embeddings):
    pid0, _, _, _ = seeded_embeddings
    r = client.get(f"/api/v1/people/{pid0}", headers={"X-API-Key": read_key})
    assert r.status_code == 200
    data = r.json()
    assert "voice_embeddings_count" in data
    assert data["voice_embeddings_count"] == 3


def test_person_detail_voice_count_zero_for_no_embeddings(client, read_key, two_people):
    pid0 = two_people[0]
    # Before seeded_embeddings runs, count should be 0.
    # This test is order-sensitive; run it before seeding if possible.
    # As a sanity check we just assert the field exists and is an int.
    r = client.get(f"/api/v1/people/{pid0}", headers={"X-API-Key": read_key})
    assert r.status_code == 200
    assert isinstance(r.json()["voice_embeddings_count"], int)


def test_person_detail_voice_count_sums_across_models(client, read_key, seeded_embeddings):
    """voice_embeddings_count sums counts from all queryable models regardless of count."""
    pid0, _, _, _ = seeded_embeddings

    second_meta = ModelMeta(
        model_id="second-test-model",
        table_name=_TABLE,
        dimension=_DIM,
        metric="cosine",
        accepts_writes=False,
        is_queryable=True,
        operator="<=>",
    )
    dual = EmbeddingRegistry({_MODEL_ID: _FAKE_META, "second-test-model": second_meta})

    saved = app.state.embedding_registry
    app.state.embedding_registry = dual
    try:
        r = client.get(f"/api/v1/people/{pid0}", headers={"X-API-Key": read_key})
    finally:
        app.state.embedding_registry = saved

    assert r.status_code == 200
    # pid0 has 3 rows in _TABLE; dual registry queries the same table twice → 3+3=6
    assert r.json()["voice_embeddings_count"] == 6


# ---------------------------------------------------------------------------
# Archived person — write returns 404
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture(loop_scope="session")
async def archived_person(db):
    pid = generate_id()
    await db.execute("INSERT INTO people (id, archived_at) VALUES ($1, now())", pid)
    yield pid
    await db.execute("DELETE FROM people WHERE id=$1", pid)


def test_write_404_archived_person(client, write_key, archived_person):
    r = client.post(
        f"/api/v1/people/{archived_person}/embeddings",
        json={
            "model_id": _MODEL_ID,
            "embedding": _rand_embedding(),
            "activity_ms": 500,
            "audio_sample_rate_hz": 16000,
            "source": {
                "service": "observo",
                "job_id": "j_arch",
                "segment": 0,
                "recorded_at": "2026-01-01T00:00:00Z",
            },
        },
        headers={"X-API-Key": write_key},
    )
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# Fixtures for soft-delete / restore tests
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture(loop_scope="session")
async def archivable_embedding(db, two_people, write_key, client):
    """Single embedding for person[0] used by archive/restore tests.

    Kept separate from seeded_embeddings so those tests can't interfere with
    the identify / count tests.
    """
    pid = two_people[0]
    r = client.post(
        f"/api/v1/people/{pid}/embeddings",
        json={
            "model_id": _MODEL_ID,
            "embedding": _rand_embedding(),
            "activity_ms": 300,
            "audio_sample_rate_hz": 16000,
            "source": {
                "service": "observo",
                "job_id": "job_archivable",
                "segment": 99,
                "recorded_at": "2026-06-01T00:00:00Z",
            },
        },
        headers={"X-API-Key": write_key},
    )
    assert r.status_code == 200, r.text
    eid = r.json()["embedding_id"]
    yield pid, eid
    await db.execute(f"DELETE FROM {_TABLE} WHERE id=$1", eid)


@pytest_asyncio.fixture(loop_scope="session")
async def batch_job_embeddings(db, two_people, write_key, client):
    """Two embeddings sharing source_job_id='job_batch_test' for batch-delete tests."""
    pid = two_people[0]
    job_id = "job_batch_test"
    eids: list[str] = []
    for seg in range(2):
        r = client.post(
            f"/api/v1/people/{pid}/embeddings",
            json={
                "model_id": _MODEL_ID,
                "embedding": _rand_embedding(),
                "activity_ms": 400,
                "audio_sample_rate_hz": 16000,
                "source": {
                    "service": "observo",
                    "job_id": job_id,
                    "segment": seg,
                    "recorded_at": "2026-06-01T00:00:00Z",
                },
            },
            headers={"X-API-Key": write_key},
        )
        assert r.status_code == 200, r.text
        eids.append(r.json()["embedding_id"])
    yield pid, job_id, eids
    await db.execute(f"DELETE FROM {_TABLE} WHERE source_job_id=$1", job_id)


# ---------------------------------------------------------------------------
# DELETE /{embedding_id} — single soft-delete
# ---------------------------------------------------------------------------


def test_delete_single_requires_write_scope(client, read_key, two_people):
    pid = two_people[0]
    r = client.delete(
        f"/api/v1/people/{pid}/embeddings/{generate_id()}",
        params={"model_id": _MODEL_ID},
        headers={"X-API-Key": read_key},
    )
    assert r.status_code == 403


def test_delete_single_422_unknown_model(client, write_key, two_people):
    pid = two_people[0]
    r = client.delete(
        f"/api/v1/people/{pid}/embeddings/{generate_id()}",
        params={"model_id": "no-such-model"},
        headers={"X-API-Key": write_key},
    )
    assert r.status_code == 422


def test_delete_single_404_unknown_embedding(client, write_key, two_people):
    pid = two_people[0]
    r = client.delete(
        f"/api/v1/people/{pid}/embeddings/{generate_id()}",
        params={"model_id": _MODEL_ID},
        headers={"X-API-Key": write_key},
    )
    assert r.status_code == 404


async def test_delete_single_archives_embedding(client, db, write_key, archivable_embedding):
    pid, eid = archivable_embedding
    await db.execute(f"UPDATE {_TABLE} SET archived_at=NULL WHERE id=$1", eid)
    try:
        r = client.delete(
            f"/api/v1/people/{pid}/embeddings/{eid}",
            params={"model_id": _MODEL_ID},
            headers={"X-API-Key": write_key},
        )
        assert r.status_code == 200
        data = r.json()
        assert data["embedding_id"] == eid
        assert data["archived_at"] is not None
    finally:
        await db.execute(f"UPDATE {_TABLE} SET archived_at=NULL WHERE id=$1", eid)


async def test_delete_single_idempotent(client, db, write_key, archivable_embedding):
    """Re-deleting an already-archived embedding returns 200 with the same archived_at."""
    pid, eid = archivable_embedding
    await db.execute(f"UPDATE {_TABLE} SET archived_at=now() WHERE id=$1", eid)
    try:
        first = client.delete(
            f"/api/v1/people/{pid}/embeddings/{eid}",
            params={"model_id": _MODEL_ID},
            headers={"X-API-Key": write_key},
        )
        assert first.status_code == 200
        first_ts = first.json()["archived_at"]

        second = client.delete(
            f"/api/v1/people/{pid}/embeddings/{eid}",
            params={"model_id": _MODEL_ID},
            headers={"X-API-Key": write_key},
        )
        assert second.status_code == 200
        assert second.json()["archived_at"] == first_ts
    finally:
        await db.execute(f"UPDATE {_TABLE} SET archived_at=NULL WHERE id=$1", eid)


# ---------------------------------------------------------------------------
# Scope tests — batch delete and restore
# ---------------------------------------------------------------------------


def test_batch_delete_requires_write_scope(client, read_key, two_people):
    pid = two_people[0]
    r = client.delete(
        f"/api/v1/people/{pid}/embeddings",
        params={"model_id": _MODEL_ID, "source_job_id": "any-job"},
        headers={"X-API-Key": read_key},
    )
    assert r.status_code == 403


def test_batch_delete_422_unknown_model(client, write_key, two_people):
    pid = two_people[0]
    r = client.delete(
        f"/api/v1/people/{pid}/embeddings",
        params={"model_id": "no-such-model", "source_job_id": "any-job"},
        headers={"X-API-Key": write_key},
    )
    assert r.status_code == 422


def test_restore_requires_write_scope(client, read_key, two_people):
    pid = two_people[0]
    r = client.post(
        f"/api/v1/people/{pid}/embeddings/{generate_id()}/restore",
        params={"model_id": _MODEL_ID},
        headers={"X-API-Key": read_key},
    )
    assert r.status_code == 403


# ---------------------------------------------------------------------------
# DELETE — batch by source_job_id
# ---------------------------------------------------------------------------


async def test_batch_delete_archives_by_job(client, db, write_key, batch_job_embeddings):
    pid, job_id, eids = batch_job_embeddings
    await db.execute(f"UPDATE {_TABLE} SET archived_at=NULL WHERE source_job_id=$1", job_id)
    try:
        r = client.delete(
            f"/api/v1/people/{pid}/embeddings",
            params={"model_id": _MODEL_ID, "source_job_id": job_id},
            headers={"X-API-Key": write_key},
        )
        assert r.status_code == 200
        assert r.json()["archived_count"] == 2
        rows = await db.fetch(f"SELECT archived_at FROM {_TABLE} WHERE id=ANY($1::text[])", eids)
        assert all(row["archived_at"] is not None for row in rows)
    finally:
        await db.execute(f"UPDATE {_TABLE} SET archived_at=NULL WHERE source_job_id=$1", job_id)


def test_batch_delete_zero_when_no_matches(client, write_key, two_people):
    pid = two_people[0]
    r = client.delete(
        f"/api/v1/people/{pid}/embeddings",
        params={"model_id": _MODEL_ID, "source_job_id": "no-such-job-xyz"},
        headers={"X-API-Key": write_key},
    )
    assert r.status_code == 200
    assert r.json()["archived_count"] == 0


# ---------------------------------------------------------------------------
# POST /{embedding_id}/restore
# ---------------------------------------------------------------------------


def test_restore_404_unknown_embedding(client, write_key, two_people):
    pid = two_people[0]
    r = client.post(
        f"/api/v1/people/{pid}/embeddings/{generate_id()}/restore",
        params={"model_id": _MODEL_ID},
        headers={"X-API-Key": write_key},
    )
    assert r.status_code == 404


async def test_restore_reactivates_archived_embedding(client, db, write_key, archivable_embedding):
    pid, eid = archivable_embedding
    await db.execute(f"UPDATE {_TABLE} SET archived_at=now() WHERE id=$1", eid)
    try:
        r = client.post(
            f"/api/v1/people/{pid}/embeddings/{eid}/restore",
            params={"model_id": _MODEL_ID},
            headers={"X-API-Key": write_key},
        )
        assert r.status_code == 200
        data = r.json()
        assert data["embedding_id"] == eid
        assert data["archived_at"] is None
    finally:
        await db.execute(f"UPDATE {_TABLE} SET archived_at=NULL WHERE id=$1", eid)


async def test_restore_409_already_active(client, db, write_key, archivable_embedding):
    pid, eid = archivable_embedding
    await db.execute(f"UPDATE {_TABLE} SET archived_at=NULL WHERE id=$1", eid)
    r = client.post(
        f"/api/v1/people/{pid}/embeddings/{eid}/restore",
        params={"model_id": _MODEL_ID},
        headers={"X-API-Key": write_key},
    )
    assert r.status_code == 409


# ---------------------------------------------------------------------------
# GET /{person_id}/embeddings — listing
# ---------------------------------------------------------------------------


def test_list_requires_read_scope(client, unscoped_key, two_people):
    pid = two_people[0]
    r = client.get(
        f"/api/v1/people/{pid}/embeddings",
        params={"model_id": _MODEL_ID},
        headers={"X-API-Key": unscoped_key},
    )
    assert r.status_code == 403


def test_list_422_unknown_model(client, read_key, two_people):
    pid = two_people[0]
    r = client.get(
        f"/api/v1/people/{pid}/embeddings",
        params={"model_id": "no-such-model"},
        headers={"X-API-Key": read_key},
    )
    assert r.status_code == 422


def test_list_active_only_by_default(client, read_key, seeded_embeddings):
    pid0, _, _, _ = seeded_embeddings
    r = client.get(
        f"/api/v1/people/{pid0}/embeddings",
        params={"model_id": _MODEL_ID},
        headers={"X-API-Key": read_key},
    )
    assert r.status_code == 200
    body = r.json()
    assert "data" in body and "meta" in body
    assert all(item["archived_at"] is None for item in body["data"])
    assert body["meta"]["count"] >= 3


async def test_list_include_archived_flag(client, db, read_key, seeded_embeddings):
    pid0, _, embedding_ids, _ = seeded_embeddings
    eid = embedding_ids[0]
    await db.execute(f"UPDATE {_TABLE} SET archived_at=now() WHERE id=$1", eid)
    try:
        r_active = client.get(
            f"/api/v1/people/{pid0}/embeddings",
            params={"model_id": _MODEL_ID},
            headers={"X-API-Key": read_key},
        )
        r_all = client.get(
            f"/api/v1/people/{pid0}/embeddings",
            params={"model_id": _MODEL_ID, "include_archived": "true"},
            headers={"X-API-Key": read_key},
        )
        assert r_all.json()["meta"]["count"] > r_active.json()["meta"]["count"]
        archived = [item for item in r_all.json()["data"] if item["archived_at"] is not None]
        assert len(archived) >= 1
    finally:
        await db.execute(f"UPDATE {_TABLE} SET archived_at=NULL WHERE id=$1", eid)


# ---------------------------------------------------------------------------
# Person-existence guard — list and batch delete
# ---------------------------------------------------------------------------


def test_list_404_unknown_person(client, read_key):
    r = client.get(
        f"/api/v1/people/{generate_id()}/embeddings",
        params={"model_id": _MODEL_ID},
        headers={"X-API-Key": read_key},
    )
    assert r.status_code == 404


def test_list_404_archived_person(client, read_key, archived_person):
    r = client.get(
        f"/api/v1/people/{archived_person}/embeddings",
        params={"model_id": _MODEL_ID},
        headers={"X-API-Key": read_key},
    )
    assert r.status_code == 404


def test_batch_delete_404_unknown_person(client, write_key):
    r = client.delete(
        f"/api/v1/people/{generate_id()}/embeddings",
        params={"model_id": _MODEL_ID, "source_job_id": "any-job"},
        headers={"X-API-Key": write_key},
    )
    assert r.status_code == 404


def test_batch_delete_404_archived_person(client, write_key, archived_person):
    r = client.delete(
        f"/api/v1/people/{archived_person}/embeddings",
        params={"model_id": _MODEL_ID, "source_job_id": "any-job"},
        headers={"X-API-Key": write_key},
    )
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# Latent bug — POST on archived slot must not silently return archived row id
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture(loop_scope="session")
async def patchable_embedding(db, two_people, write_key, client):
    """Active embedding for PATCH metadata tests (audio_sample_rate_hz=44100 initially)."""
    pid = two_people[0]
    r = client.post(
        f"/api/v1/people/{pid}/embeddings",
        json={
            "model_id": _MODEL_ID,
            "embedding": _rand_embedding(),
            "activity_ms": 500,
            "audio_sample_rate_hz": 44100,
            "source": {
                "service": "observo",
                "job_id": "job_patchable",
                "segment": 77,
                "recorded_at": "2026-06-01T00:00:00.000000Z",
            },
        },
        headers={"X-API-Key": write_key},
    )
    assert r.status_code == 200, r.text
    eid = r.json()["embedding_id"]
    yield pid, eid
    await db.execute(f"DELETE FROM {_TABLE} WHERE id=$1", eid)


async def test_write_on_archived_slot_returns_409(client, db, write_key, patchable_embedding):
    """POST with same provenance key as an archived row must 409, not return the archived row."""
    pid, eid = patchable_embedding
    await db.execute(f"UPDATE {_TABLE} SET archived_at=now() WHERE id=$1", eid)
    try:
        r = client.post(
            f"/api/v1/people/{pid}/embeddings",
            json={
                "model_id": _MODEL_ID,
                "embedding": _rand_embedding(),
                "activity_ms": 500,
                "audio_sample_rate_hz": 48000,
                "source": {
                    "service": "observo",
                    "job_id": "job_patchable",
                    "segment": 77,
                    "recorded_at": "2026-06-01T00:00:00Z",
                },
            },
            headers={"X-API-Key": write_key},
        )
        assert r.status_code == 409
        assert "archived" in r.json()["detail"].lower()
    finally:
        await db.execute(f"UPDATE {_TABLE} SET archived_at=NULL WHERE id=$1", eid)


# ---------------------------------------------------------------------------
# PATCH /{person_id}/embeddings/{embedding_id} — metadata update
# ---------------------------------------------------------------------------


def test_patch_requires_write_scope(client, read_key, two_people):
    pid = two_people[0]
    r = client.patch(
        f"/api/v1/people/{pid}/embeddings/{generate_id()}",
        params={"model_id": _MODEL_ID},
        json={"audio_sample_rate_hz": 48000},
        headers={"X-API-Key": read_key},
    )
    assert r.status_code == 403


def test_patch_422_unknown_model(client, write_key, two_people):
    pid = two_people[0]
    r = client.patch(
        f"/api/v1/people/{pid}/embeddings/{generate_id()}",
        params={"model_id": "no-such-model"},
        json={"audio_sample_rate_hz": 48000},
        headers={"X-API-Key": write_key},
    )
    assert r.status_code == 422


def test_patch_422_empty_body(client, write_key, patchable_embedding):
    pid, eid = patchable_embedding
    r = client.patch(
        f"/api/v1/people/{pid}/embeddings/{eid}",
        params={"model_id": _MODEL_ID},
        json={},
        headers={"X-API-Key": write_key},
    )
    assert r.status_code == 422


def test_patch_404_unknown_embedding(client, write_key, two_people):
    pid = two_people[0]
    r = client.patch(
        f"/api/v1/people/{pid}/embeddings/{generate_id()}",
        params={"model_id": _MODEL_ID},
        json={"audio_sample_rate_hz": 48000},
        headers={"X-API-Key": write_key},
    )
    assert r.status_code == 404


async def test_patch_409_archived(client, db, write_key, patchable_embedding):
    pid, eid = patchable_embedding
    await db.execute(f"UPDATE {_TABLE} SET archived_at=now() WHERE id=$1", eid)
    try:
        r = client.patch(
            f"/api/v1/people/{pid}/embeddings/{eid}",
            params={"model_id": _MODEL_ID},
            json={"audio_sample_rate_hz": 48000},
            headers={"X-API-Key": write_key},
        )
        assert r.status_code == 409
    finally:
        await db.execute(f"UPDATE {_TABLE} SET archived_at=NULL WHERE id=$1", eid)


def test_patch_updates_audio_sample_rate(client, write_key, patchable_embedding):
    pid, eid = patchable_embedding
    r = client.patch(
        f"/api/v1/people/{pid}/embeddings/{eid}",
        params={"model_id": _MODEL_ID},
        json={"audio_sample_rate_hz": 48000},
        headers={"X-API-Key": write_key},
    )
    assert r.status_code == 200
    data = r.json()
    assert data["embedding_id"] == eid
    assert data["person_id"] == pid
    assert data["audio_sample_rate_hz"] == 48000
    assert isinstance(data["activity_ms"], int)  # untouched, but present


def test_patch_updates_activity_ms(client, write_key, patchable_embedding):
    pid, eid = patchable_embedding
    r = client.patch(
        f"/api/v1/people/{pid}/embeddings/{eid}",
        params={"model_id": _MODEL_ID},
        json={"activity_ms": 750},
        headers={"X-API-Key": write_key},
    )
    assert r.status_code == 200
    assert r.json()["activity_ms"] == 750


def test_patch_updates_recorded_at(client, write_key, patchable_embedding):
    pid, eid = patchable_embedding
    # Use non-zero microseconds so isoformat() preserves the fractional part
    new_ts = "2026-06-15T10:00:00.123456Z"
    r = client.patch(
        f"/api/v1/people/{pid}/embeddings/{eid}",
        params={"model_id": _MODEL_ID},
        json={"recorded_at": new_ts},
        headers={"X-API-Key": write_key},
    )
    assert r.status_code == 200
    assert r.json()["recorded_at"] == new_ts


def test_patch_multi_field(client, write_key, patchable_embedding):
    pid, eid = patchable_embedding
    r = client.patch(
        f"/api/v1/people/{pid}/embeddings/{eid}",
        params={"model_id": _MODEL_ID},
        json={"activity_ms": 100, "audio_sample_rate_hz": 8000},
        headers={"X-API-Key": write_key},
    )
    assert r.status_code == 200
    data = r.json()
    assert data["activity_ms"] == 100
    assert data["audio_sample_rate_hz"] == 8000
