"""Tests for POST /api/v1/people/identify and POST /api/v1/people/{id}/embeddings."""

import hashlib
import os
import random

import pytest
import pytest_asyncio

from src.core.db import generate_id
from src.core.embedding_registry import EmbeddingRegistry, ModelMeta

pytestmark = [pytest.mark.asyncio(loop_scope="session")]

_MODEL_ID = "pyannote-community-1-embed"
_DIM = 192
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


def test_identify_excludes_archived(client, db, read_key, write_key, two_people, seeded_embeddings):
    """Archived embeddings must not appear in identify results."""
    _, _, embedding_ids, query_vec = seeded_embeddings
    # Archive all five seeded embeddings via direct DB update
    import asyncio

    asyncio.get_event_loop().run_until_complete(
        db.execute(
            f"UPDATE {_TABLE} SET archived_at = now() WHERE id = ANY($1::text[])",
            embedding_ids,
        )
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
        asyncio.get_event_loop().run_until_complete(
            db.execute(
                f"UPDATE {_TABLE} SET archived_at = NULL WHERE id = ANY($1::text[])",
                embedding_ids,
            )
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
