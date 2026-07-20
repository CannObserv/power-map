"""Tests for POST /api/v1/people/identify and POST /api/v1/people/{id}/embeddings."""

import hashlib
import json
import os
import random

import pytest
import pytest_asyncio

from src.api.main import app
from src.api.public.embeddings import _get_registry as _emb_get_registry
from src.api.public.people import _get_registry
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
    return raw


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
    return raw


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
    return raw


@pytest_asyncio.fixture(loop_scope="session")
async def two_people(db):
    """Two active people for seeding embeddings."""
    ids = [generate_id(), generate_id()]
    for pid in ids:
        await db.execute("INSERT INTO people (id) VALUES ($1)", pid)
    return ids


# ---------------------------------------------------------------------------
# Auth / scope guards
# ---------------------------------------------------------------------------


async def test_identify_requires_read_scope(client, unscoped_key):
    r = await client.post(
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


async def test_write_requires_write_scope(client, read_key):
    pid = generate_id()
    r = await client.post(
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


async def test_identify_empty_returns_empty_matches(client, read_key):
    r = await client.post(
        "/api/v1/people/identify",
        json={"model_id": _MODEL_ID, "embedding": _rand_embedding()},
        headers={"X-API-Key": read_key},
    )
    assert r.status_code == 200
    data = r.json()
    assert data["matches"] == []


async def test_identify_unknown_model_returns_empty(client, read_key):
    r = await client.post(
        "/api/v1/people/identify",
        json={"model_id": "no-such-model", "embedding": _rand_embedding()},
        headers={"X-API-Key": read_key},
    )
    assert r.status_code == 200
    assert r.json()["matches"] == []


async def test_identify_dim_mismatch_422(client, read_key):
    r = await client.post(
        "/api/v1/people/identify",
        json={"model_id": _MODEL_ID, "embedding": _rand_embedding(dim=64)},
        headers={"X-API-Key": read_key},
    )
    assert r.status_code == 422


async def test_identify_top_k_clamped(client, read_key):
    """top_k > 25 is silently clamped; should not 422."""
    r = await client.post(
        "/api/v1/people/identify",
        json={"model_id": _MODEL_ID, "embedding": _rand_embedding(), "top_k": 999},
        headers={"X-API-Key": read_key},
    )
    assert r.status_code == 200


# ---------------------------------------------------------------------------
# Input-embedding validation (#299) — zero-norm and non-finite vectors
# ---------------------------------------------------------------------------


async def test_identify_422_zero_vector(client, read_key):
    """cosine(0, x) is NaN — reject the zero vector at validation, not as null similarity."""
    r = await client.post(
        "/api/v1/people/identify",
        json={"model_id": _MODEL_ID, "embedding": [0.0] * _DIM},
        headers={"X-API-Key": read_key},
    )
    assert r.status_code == 422


async def test_identify_422_nan_element(client, read_key):
    """Server-side json.loads accepts NaN literals; must 422, not 500 at the pgvector layer.

    httpx's json= refuses to serialize NaN, so build the raw body with
    stdlib json.dumps (allow_nan=True default emits the bare ``NaN`` literal).
    """
    vec = _rand_embedding()
    vec[3] = float("nan")
    r = await client.post(
        "/api/v1/people/identify",
        content=json.dumps({"model_id": _MODEL_ID, "embedding": vec}),
        headers={"X-API-Key": read_key, "Content-Type": "application/json"},
    )
    assert r.status_code == 422


async def test_identify_422_infinity_element(client, read_key):
    vec = _rand_embedding()
    vec[0] = float("inf")
    r = await client.post(
        "/api/v1/people/identify",
        content=json.dumps({"model_id": _MODEL_ID, "embedding": vec}),
        headers={"X-API-Key": read_key, "Content-Type": "application/json"},
    )
    assert r.status_code == 422


async def test_write_422_zero_vector(client, write_key, two_people):
    """Write path must also reject zero vectors — a stored zero vector poisons reads with NaN."""
    pid = two_people[0]
    r = await client.post(
        f"/api/v1/people/{pid}/embeddings",
        json={
            "model_id": _MODEL_ID,
            "embedding": [0.0] * _DIM,
            "activity_ms": 500,
            "audio_sample_rate_hz": 16000,
            "source": {
                "service": "observo",
                "job_id": "j_zero",
                "segment": 0,
                "recorded_at": "2026-01-01T00:00:00Z",
            },
        },
        headers={"X-API-Key": write_key},
    )
    assert r.status_code == 422


async def test_write_422_nan_element(client, write_key, two_people):
    pid = two_people[0]
    vec = _rand_embedding()
    vec[-1] = float("nan")
    r = await client.post(
        f"/api/v1/people/{pid}/embeddings",
        content=json.dumps(
            {
                "model_id": _MODEL_ID,
                "embedding": vec,
                "activity_ms": 500,
                "audio_sample_rate_hz": 16000,
                "source": {
                    "service": "observo",
                    "job_id": "j_nan",
                    "segment": 0,
                    "recorded_at": "2026-01-01T00:00:00Z",
                },
            }
        ),
        headers={"X-API-Key": write_key, "Content-Type": "application/json"},
    )
    assert r.status_code == 422


# ---------------------------------------------------------------------------
# write — 404 / 422
# ---------------------------------------------------------------------------


async def test_write_404_unknown_person(client, write_key):
    r = await client.post(
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


async def test_write_422_dim_mismatch(client, write_key, two_people):
    pid = two_people[0]
    r = await client.post(
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


async def test_write_422_unknown_model(client, write_key, two_people):
    pid = two_people[0]
    r = await client.post(
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
        r = await client.post(
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

    return pid0, pid1, embedding_ids, query_vec


async def test_write_returns_embedding_id(seeded_embeddings):
    _, _, embedding_ids, _ = seeded_embeddings
    assert len(embedding_ids) == 5
    for eid in embedding_ids:
        assert len(eid) == 26


async def test_write_duplicate_returns_200_with_same_id(client, write_key, seeded_embeddings):
    pid0, _, embedding_ids, query_vec = seeded_embeddings
    r = await client.post(
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


async def test_identify_returns_top_k_matches(client, read_key, seeded_embeddings):
    _, _, _, query_vec = seeded_embeddings
    r = await client.post(
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
        r = await client.post(
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
# POST /people/verify — closed-set verification (#299)
# ---------------------------------------------------------------------------


async def test_verify_requires_read_scope(client, unscoped_key, two_people):
    r = await client.post(
        "/api/v1/people/verify",
        json={
            "model_id": _MODEL_ID,
            "embedding": _rand_embedding(),
            "person_ids": [two_people[0]],
        },
        headers={"X-API-Key": unscoped_key},
    )
    assert r.status_code == 403


def test_verify_rejects_missing_key(unit_client):
    r = unit_client.post(
        "/api/v1/people/verify",
        json={
            "model_id": _MODEL_ID,
            "embedding": _rand_embedding(),
            "person_ids": [generate_id()],
        },
    )
    assert r.status_code == 403


async def test_verify_422_unknown_model(client, read_key, two_people):
    """Unlike identify's matches:[] convention, verify 422s on an unknown model —
    an all-null response would be indistinguishable from 'roster has no enrollments'."""
    r = await client.post(
        "/api/v1/people/verify",
        json={
            "model_id": "no-such-model",
            "embedding": _rand_embedding(),
            "person_ids": [two_people[0]],
        },
        headers={"X-API-Key": read_key},
    )
    assert r.status_code == 422


async def test_verify_422_dim_mismatch(client, read_key, two_people):
    r = await client.post(
        "/api/v1/people/verify",
        json={
            "model_id": _MODEL_ID,
            "embedding": _rand_embedding(dim=64),
            "person_ids": [two_people[0]],
        },
        headers={"X-API-Key": read_key},
    )
    assert r.status_code == 422


async def test_verify_422_zero_vector(client, read_key, two_people):
    r = await client.post(
        "/api/v1/people/verify",
        json={
            "model_id": _MODEL_ID,
            "embedding": [0.0] * _DIM,
            "person_ids": [two_people[0]],
        },
        headers={"X-API-Key": read_key},
    )
    assert r.status_code == 422


async def test_verify_422_empty_person_ids(client, read_key):
    r = await client.post(
        "/api/v1/people/verify",
        json={"model_id": _MODEL_ID, "embedding": _rand_embedding(), "person_ids": []},
        headers={"X-API-Key": read_key},
    )
    assert r.status_code == 422


async def test_verify_422_over_cap(client, read_key):
    r = await client.post(
        "/api/v1/people/verify",
        json={
            "model_id": _MODEL_ID,
            "embedding": _rand_embedding(),
            "person_ids": [generate_id() for _ in range(501)],
        },
        headers={"X-API-Key": read_key},
    )
    assert r.status_code == 422


async def test_verify_accepts_roster_scale_candidate_set(client, read_key, seeded_embeddings):
    """#310: cap raised 25 → 500 so a legislature-scale roster fits in one call."""
    pid0, pid1, _, query_vec = seeded_embeddings
    roster = [pid0, pid1] + [generate_id() for _ in range(498)]
    r = await client.post(
        "/api/v1/people/verify",
        json={"model_id": _MODEL_ID, "embedding": query_vec, "person_ids": roster},
        headers={"X-API-Key": read_key},
    )
    assert r.status_code == 200
    results = r.json()["results"]
    assert len(results) == 500
    assert [x["person_id"] for x in results] == roster
    assert abs(results[0]["similarity"] - 1.0) < 1e-4
    assert results[2]["similarity"] is None


async def test_verify_scores_full_roster_in_request_order(client, db, read_key, seeded_embeddings):
    """One result per requested id, in request order; best enrollment wins.

    pid0 has 3 enrollments (one identical to the query vector → similarity 1.0,
    winning embedding_id = its enrollment); pid1 has 2; a fresh person has none
    → similarity/embedding_id null with n_embeddings 0.
    """
    pid0, pid1, embedding_ids, query_vec = seeded_embeddings
    empty_pid = generate_id()
    await db.execute("INSERT INTO people (id) VALUES ($1)", empty_pid)

    r = await client.post(
        "/api/v1/people/verify",
        json={
            "model_id": _MODEL_ID,
            "embedding": query_vec,
            "person_ids": [pid1, empty_pid, pid0],
        },
        headers={"X-API-Key": read_key},
    )
    assert r.status_code == 200
    results = r.json()["results"]
    assert [x["person_id"] for x in results] == [pid1, empty_pid, pid0]

    by_pid = {x["person_id"]: x for x in results}
    assert by_pid[pid0]["n_embeddings"] == 3
    assert abs(by_pid[pid0]["similarity"] - 1.0) < 1e-4
    assert by_pid[pid0]["embedding_id"] == embedding_ids[0]

    assert by_pid[pid1]["n_embeddings"] == 2
    assert by_pid[pid1]["similarity"] is not None
    assert by_pid[pid1]["embedding_id"] in embedding_ids[3:5]

    assert by_pid[empty_pid]["n_embeddings"] == 0
    assert by_pid[empty_pid]["similarity"] is None
    assert by_pid[empty_pid]["embedding_id"] is None


async def test_verify_unknown_person_id_yields_zero_row(client, read_key, seeded_embeddings):
    """An id that matches no person at all still gets its result row (no 404)."""
    ghost = generate_id()
    _, _, _, query_vec = seeded_embeddings
    r = await client.post(
        "/api/v1/people/verify",
        json={"model_id": _MODEL_ID, "embedding": query_vec, "person_ids": [ghost]},
        headers={"X-API-Key": read_key},
    )
    assert r.status_code == 200
    results = r.json()["results"]
    assert results == [
        {"person_id": ghost, "similarity": None, "embedding_id": None, "n_embeddings": 0}
    ]


async def test_verify_dedupes_person_ids(client, read_key, seeded_embeddings):
    """Duplicate ids collapse to one result, first-occurrence order preserved."""
    pid0, pid1, _, query_vec = seeded_embeddings
    r = await client.post(
        "/api/v1/people/verify",
        json={
            "model_id": _MODEL_ID,
            "embedding": query_vec,
            "person_ids": [pid1, pid0, pid1, pid0],
        },
        headers={"X-API-Key": read_key},
    )
    assert r.status_code == 200
    assert [x["person_id"] for x in r.json()["results"]] == [pid1, pid0]


async def test_verify_422_non_queryable_model(client, read_key, two_people):
    """A registered but non-queryable model 422s, same as an unknown one."""
    non_queryable = ModelMeta(
        model_id=_MODEL_ID,
        table_name=_TABLE,
        dimension=_DIM,
        metric="cosine",
        accepts_writes=True,
        is_queryable=False,
        operator="<=>",
    )
    app.dependency_overrides[_emb_get_registry] = lambda: EmbeddingRegistry(
        {_MODEL_ID: non_queryable}
    )
    try:
        r = await client.post(
            "/api/v1/people/verify",
            json={
                "model_id": _MODEL_ID,
                "embedding": _rand_embedding(),
                "person_ids": [two_people[0]],
            },
            headers={"X-API-Key": read_key},
        )
    finally:
        app.dependency_overrides.pop(_emb_get_registry, None)
    assert r.status_code == 422


async def test_verify_tied_distances_deterministic_winner(
    client, db, read_key, write_key, two_people
):
    """Identical enrollments (exact distance tie) must yield a stable embedding_id.

    The winner is the oldest enrollment (ascending ULID) — pinned so repeated
    verify calls with the same inputs return the same winning embedding_id.
    """
    pid = two_people[0]
    vec = _rand_embedding()
    eids = []
    for seg in range(2):
        r = await client.post(
            f"/api/v1/people/{pid}/embeddings",
            json={
                "model_id": _MODEL_ID,
                "embedding": vec,
                "activity_ms": 500,
                "audio_sample_rate_hz": 16000,
                "source": {
                    "service": "observo",
                    "job_id": "job_tie",
                    "segment": seg,
                    "recorded_at": "2026-06-01T00:00:00Z",
                },
            },
            headers={"X-API-Key": write_key},
        )
        assert r.status_code == 200, r.text
        eids.append(r.json()["embedding_id"])

    expected_winner = min(eids)
    for _ in range(3):
        r = await client.post(
            "/api/v1/people/verify",
            json={"model_id": _MODEL_ID, "embedding": vec, "person_ids": [pid]},
            headers={"X-API-Key": read_key},
        )
        assert r.status_code == 200
        (res,) = r.json()["results"]
        assert abs(res["similarity"] - 1.0) < 1e-4
        assert res["embedding_id"] == expected_winner


async def test_verify_excludes_archived_embeddings(client, db, read_key, seeded_embeddings):
    """Archived enrollments neither score nor count."""
    pid0, _, embedding_ids, query_vec = seeded_embeddings
    pid0_eids = embedding_ids[:3]
    await db.execute(
        f"UPDATE {_TABLE} SET archived_at = now() WHERE id = ANY($1::text[])",
        pid0_eids,
    )
    try:
        r = await client.post(
            "/api/v1/people/verify",
            json={"model_id": _MODEL_ID, "embedding": query_vec, "person_ids": [pid0]},
            headers={"X-API-Key": read_key},
        )
        assert r.status_code == 200
        (res,) = r.json()["results"]
        assert res["n_embeddings"] == 0
        assert res["similarity"] is None
        assert res["embedding_id"] is None
    finally:
        await db.execute(
            f"UPDATE {_TABLE} SET archived_at = NULL WHERE id = ANY($1::text[])",
            pid0_eids,
        )


# ---------------------------------------------------------------------------
# POST /people/verify-batch — multi-embedding closed-set verification (#310)
# ---------------------------------------------------------------------------


async def test_verify_batch_requires_read_scope(client, unscoped_key, two_people):
    r = await client.post(
        "/api/v1/people/verify-batch",
        json={
            "model_id": _MODEL_ID,
            "embeddings": [_rand_embedding()],
            "person_ids": [two_people[0]],
        },
        headers={"X-API-Key": unscoped_key},
    )
    assert r.status_code == 403


def test_verify_batch_rejects_missing_key(unit_client):
    r = unit_client.post(
        "/api/v1/people/verify-batch",
        json={
            "model_id": _MODEL_ID,
            "embeddings": [_rand_embedding()],
            "person_ids": [generate_id()],
        },
    )
    assert r.status_code == 403


async def test_verify_batch_422_unknown_model(client, read_key, two_people):
    r = await client.post(
        "/api/v1/people/verify-batch",
        json={
            "model_id": "no-such-model",
            "embeddings": [_rand_embedding()],
            "person_ids": [two_people[0]],
        },
        headers={"X-API-Key": read_key},
    )
    assert r.status_code == 422


async def test_verify_batch_422_non_queryable_model(client, read_key, two_people):
    non_queryable = ModelMeta(
        model_id=_MODEL_ID,
        table_name=_TABLE,
        dimension=_DIM,
        metric="cosine",
        accepts_writes=True,
        is_queryable=False,
        operator="<=>",
    )
    app.dependency_overrides[_emb_get_registry] = lambda: EmbeddingRegistry(
        {_MODEL_ID: non_queryable}
    )
    try:
        r = await client.post(
            "/api/v1/people/verify-batch",
            json={
                "model_id": _MODEL_ID,
                "embeddings": [_rand_embedding()],
                "person_ids": [two_people[0]],
            },
            headers={"X-API-Key": read_key},
        )
    finally:
        app.dependency_overrides.pop(_emb_get_registry, None)
    assert r.status_code == 422


async def test_verify_batch_422_dim_mismatch_reports_index(client, read_key, two_people):
    """A dimension mismatch on any embedding 422s and names the failing index."""
    r = await client.post(
        "/api/v1/people/verify-batch",
        json={
            "model_id": _MODEL_ID,
            "embeddings": [_rand_embedding(), _rand_embedding(dim=64)],
            "person_ids": [two_people[0]],
        },
        headers={"X-API-Key": read_key},
    )
    assert r.status_code == 422
    assert "index 1" in r.json()["detail"]


async def test_verify_batch_422_zero_vector_element(client, read_key, two_people):
    r = await client.post(
        "/api/v1/people/verify-batch",
        json={
            "model_id": _MODEL_ID,
            "embeddings": [_rand_embedding(), [0.0] * _DIM],
            "person_ids": [two_people[0]],
        },
        headers={"X-API-Key": read_key},
    )
    assert r.status_code == 422


async def test_verify_batch_422_empty_embeddings(client, read_key, two_people):
    r = await client.post(
        "/api/v1/people/verify-batch",
        json={"model_id": _MODEL_ID, "embeddings": [], "person_ids": [two_people[0]]},
        headers={"X-API-Key": read_key},
    )
    assert r.status_code == 422


async def test_verify_batch_422_over_embedding_cap(client, read_key, two_people):
    r = await client.post(
        "/api/v1/people/verify-batch",
        json={
            "model_id": _MODEL_ID,
            "embeddings": [_rand_embedding() for _ in range(51)],
            "person_ids": [two_people[0]],
        },
        headers={"X-API-Key": read_key},
    )
    assert r.status_code == 422


async def test_verify_batch_422_over_person_cap(client, read_key):
    r = await client.post(
        "/api/v1/people/verify-batch",
        json={
            "model_id": _MODEL_ID,
            "embeddings": [_rand_embedding()],
            "person_ids": [generate_id() for _ in range(501)],
        },
        headers={"X-API-Key": read_key},
    )
    assert r.status_code == 422


async def test_verify_batch_groups_per_embedding_in_request_order(
    client, db, read_key, seeded_embeddings
):
    """One group per embedding, embeddings request order; each group carries the
    full per-candidate result list with #299 semantics (candidate request order,
    null = no enrollment, best enrollment wins)."""
    pid0, pid1, embedding_ids, query_vec = seeded_embeddings
    empty_pid = generate_id()
    await db.execute("INSERT INTO people (id) VALUES ($1)", empty_pid)

    r = await client.post(
        "/api/v1/people/verify-batch",
        json={
            "model_id": _MODEL_ID,
            "embeddings": [query_vec, _rand_embedding()],
            "person_ids": [pid1, empty_pid, pid0],
        },
        headers={"X-API-Key": read_key},
    )
    assert r.status_code == 200
    groups = r.json()["results"]
    assert [g["embedding_index"] for g in groups] == [0, 1]

    for g in groups:
        assert [x["person_id"] for x in g["results"]] == [pid1, empty_pid, pid0]
        by_pid = {x["person_id"]: x for x in g["results"]}
        assert by_pid[pid0]["n_embeddings"] == 3
        assert by_pid[pid1]["n_embeddings"] == 2
        assert by_pid[empty_pid]["n_embeddings"] == 0
        assert by_pid[empty_pid]["similarity"] is None
        assert by_pid[empty_pid]["embedding_id"] is None

    # Group 0's query is identical to pid0's first enrollment
    g0 = {x["person_id"]: x for x in groups[0]["results"]}
    assert abs(g0[pid0]["similarity"] - 1.0) < 1e-4
    assert g0[pid0]["embedding_id"] == embedding_ids[0]


async def test_verify_batch_dedupes_person_ids(client, read_key, seeded_embeddings):
    pid0, pid1, _, query_vec = seeded_embeddings
    r = await client.post(
        "/api/v1/people/verify-batch",
        json={
            "model_id": _MODEL_ID,
            "embeddings": [query_vec],
            "person_ids": [pid1, pid0, pid1, pid0],
        },
        headers={"X-API-Key": read_key},
    )
    assert r.status_code == 200
    (group,) = r.json()["results"]
    assert [x["person_id"] for x in group["results"]] == [pid1, pid0]


async def test_verify_batch_excludes_archived_embeddings(client, db, read_key, seeded_embeddings):
    pid0, _, embedding_ids, query_vec = seeded_embeddings
    pid0_eids = embedding_ids[:3]
    await db.execute(
        f"UPDATE {_TABLE} SET archived_at = now() WHERE id = ANY($1::text[])",
        pid0_eids,
    )
    try:
        r = await client.post(
            "/api/v1/people/verify-batch",
            json={"model_id": _MODEL_ID, "embeddings": [query_vec], "person_ids": [pid0]},
            headers={"X-API-Key": read_key},
        )
        assert r.status_code == 200
        (group,) = r.json()["results"]
        (res,) = group["results"]
        assert res["n_embeddings"] == 0
        assert res["similarity"] is None
        assert res["embedding_id"] is None
    finally:
        await db.execute(
            f"UPDATE {_TABLE} SET archived_at = NULL WHERE id = ANY($1::text[])",
            pid0_eids,
        )


# ---------------------------------------------------------------------------
# POST /people/embeddings/presence — bulk enrollment-presence query (#310)
# ---------------------------------------------------------------------------


async def test_presence_requires_read_scope(client, unscoped_key, two_people):
    r = await client.post(
        "/api/v1/people/embeddings/presence",
        json={"model_id": _MODEL_ID, "person_ids": [two_people[0]]},
        headers={"X-API-Key": unscoped_key},
    )
    assert r.status_code == 403


def test_presence_rejects_missing_key(unit_client):
    r = unit_client.post(
        "/api/v1/people/embeddings/presence",
        json={"model_id": _MODEL_ID, "person_ids": [generate_id()]},
    )
    assert r.status_code == 403


async def test_presence_422_unknown_model(client, read_key, two_people):
    r = await client.post(
        "/api/v1/people/embeddings/presence",
        json={"model_id": "no-such-model", "person_ids": [two_people[0]]},
        headers={"X-API-Key": read_key},
    )
    assert r.status_code == 422


async def test_presence_422_non_queryable_model(client, read_key, two_people):
    """Presence exists to pre-filter verify candidate sets — a model you cannot
    verify against 422s, mirroring verify."""
    non_queryable = ModelMeta(
        model_id=_MODEL_ID,
        table_name=_TABLE,
        dimension=_DIM,
        metric="cosine",
        accepts_writes=True,
        is_queryable=False,
        operator="<=>",
    )
    app.dependency_overrides[_emb_get_registry] = lambda: EmbeddingRegistry(
        {_MODEL_ID: non_queryable}
    )
    try:
        r = await client.post(
            "/api/v1/people/embeddings/presence",
            json={"model_id": _MODEL_ID, "person_ids": [two_people[0]]},
            headers={"X-API-Key": read_key},
        )
    finally:
        app.dependency_overrides.pop(_emb_get_registry, None)
    assert r.status_code == 422


async def test_presence_422_empty_person_ids(client, read_key):
    r = await client.post(
        "/api/v1/people/embeddings/presence",
        json={"model_id": _MODEL_ID, "person_ids": []},
        headers={"X-API-Key": read_key},
    )
    assert r.status_code == 422


async def test_presence_422_over_cap(client, read_key):
    r = await client.post(
        "/api/v1/people/embeddings/presence",
        json={"model_id": _MODEL_ID, "person_ids": [generate_id() for _ in range(1001)]},
        headers={"X-API-Key": read_key},
    )
    assert r.status_code == 422


async def test_presence_counts_in_request_order_with_dedup(client, db, read_key, seeded_embeddings):
    """One row per requested id, request order, duplicates deduped; ids with no
    active enrollments (unknown ids included) come back with n_embeddings 0."""
    pid0, pid1, _, _ = seeded_embeddings
    ghost = generate_id()
    r = await client.post(
        "/api/v1/people/embeddings/presence",
        json={"model_id": _MODEL_ID, "person_ids": [pid1, ghost, pid0, pid1]},
        headers={"X-API-Key": read_key},
    )
    assert r.status_code == 200
    results = r.json()["results"]
    assert results == [
        {"person_id": pid1, "n_embeddings": 2},
        {"person_id": ghost, "n_embeddings": 0},
        {"person_id": pid0, "n_embeddings": 3},
    ]


async def test_presence_excludes_archived_embeddings(client, db, read_key, seeded_embeddings):
    pid0, _, embedding_ids, _ = seeded_embeddings
    pid0_eids = embedding_ids[:3]
    await db.execute(
        f"UPDATE {_TABLE} SET archived_at = now() WHERE id = ANY($1::text[])",
        pid0_eids,
    )
    try:
        r = await client.post(
            "/api/v1/people/embeddings/presence",
            json={"model_id": _MODEL_ID, "person_ids": [pid0]},
            headers={"X-API-Key": read_key},
        )
        assert r.status_code == 200
        assert r.json()["results"] == [{"person_id": pid0, "n_embeddings": 0}]
    finally:
        await db.execute(
            f"UPDATE {_TABLE} SET archived_at = NULL WHERE id = ANY($1::text[])",
            pid0_eids,
        )


# ---------------------------------------------------------------------------
# GET /people/{id} — voice_embeddings_count
# ---------------------------------------------------------------------------


async def test_person_detail_has_voice_embeddings_count(client, read_key, seeded_embeddings):
    pid0, _, _, _ = seeded_embeddings
    r = await client.get(f"/api/v1/people/{pid0}", headers={"X-API-Key": read_key})
    assert r.status_code == 200
    data = r.json()
    assert "voice_embeddings_count" in data
    assert data["voice_embeddings_count"] == 3


async def test_person_detail_voice_count_zero_for_no_embeddings(client, read_key, two_people):
    pid0 = two_people[0]
    # Before seeded_embeddings runs, count should be 0.
    # This test is order-sensitive; run it before seeding if possible.
    # As a sanity check we just assert the field exists and is an int.
    r = await client.get(f"/api/v1/people/{pid0}", headers={"X-API-Key": read_key})
    assert r.status_code == 200
    assert isinstance(r.json()["voice_embeddings_count"], int)


async def test_person_detail_voice_count_sums_across_models(client, read_key, seeded_embeddings):
    """voice_embeddings_count sums counts from all queryable models.

    Both registry entries point at the same physical table (_TABLE) — a
    same-table proxy, since only one real embeddings table exists. The DB-free
    multi-table coverage lives in tests/api/public/test_people_detail_arrays.py.
    pid0 has 3 rows in _TABLE, so the dual registry counts it twice → 3 + 3 = 6.
    """
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

    app.dependency_overrides[_get_registry] = lambda: dual
    try:
        r = await client.get(f"/api/v1/people/{pid0}", headers={"X-API-Key": read_key})
    finally:
        app.dependency_overrides.pop(_get_registry, None)

    assert r.status_code == 200
    assert r.json()["voice_embeddings_count"] == 6


# ---------------------------------------------------------------------------
# Archived person — write returns 404
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture(loop_scope="session")
async def archived_person(db):
    pid = generate_id()
    await db.execute("INSERT INTO people (id, archived_at) VALUES ($1, now())", pid)
    return pid


async def test_write_404_archived_person(client, write_key, archived_person):
    r = await client.post(
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
    r = await client.post(
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
    return pid, eid


@pytest_asyncio.fixture(loop_scope="session")
async def batch_job_embeddings(db, two_people, write_key, client):
    """Two embeddings sharing source_job_id='job_batch_test' for batch-delete tests."""
    pid = two_people[0]
    job_id = "job_batch_test"
    eids: list[str] = []
    for seg in range(2):
        r = await client.post(
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
    return pid, job_id, eids


# ---------------------------------------------------------------------------
# DELETE /{embedding_id} — single soft-delete
# ---------------------------------------------------------------------------


async def test_delete_single_requires_write_scope(client, read_key, two_people):
    pid = two_people[0]
    r = await client.delete(
        f"/api/v1/people/{pid}/embeddings/{generate_id()}",
        params={"model_id": _MODEL_ID},
        headers={"X-API-Key": read_key},
    )
    assert r.status_code == 403


async def test_delete_single_422_unknown_model(client, write_key, two_people):
    pid = two_people[0]
    r = await client.delete(
        f"/api/v1/people/{pid}/embeddings/{generate_id()}",
        params={"model_id": "no-such-model"},
        headers={"X-API-Key": write_key},
    )
    assert r.status_code == 422


async def test_delete_single_404_unknown_embedding(client, write_key, two_people):
    pid = two_people[0]
    r = await client.delete(
        f"/api/v1/people/{pid}/embeddings/{generate_id()}",
        params={"model_id": _MODEL_ID},
        headers={"X-API-Key": write_key},
    )
    assert r.status_code == 404


async def test_delete_single_archives_embedding(client, db, write_key, archivable_embedding):
    pid, eid = archivable_embedding
    await db.execute(f"UPDATE {_TABLE} SET archived_at=NULL WHERE id=$1", eid)
    try:
        r = await client.delete(
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
        first = await client.delete(
            f"/api/v1/people/{pid}/embeddings/{eid}",
            params={"model_id": _MODEL_ID},
            headers={"X-API-Key": write_key},
        )
        assert first.status_code == 200
        first_ts = first.json()["archived_at"]

        second = await client.delete(
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


async def test_batch_delete_requires_write_scope(client, read_key, two_people):
    pid = two_people[0]
    r = await client.delete(
        f"/api/v1/people/{pid}/embeddings",
        params={"model_id": _MODEL_ID, "source_job_id": "any-job"},
        headers={"X-API-Key": read_key},
    )
    assert r.status_code == 403


async def test_batch_delete_422_unknown_model(client, write_key, two_people):
    pid = two_people[0]
    r = await client.delete(
        f"/api/v1/people/{pid}/embeddings",
        params={"model_id": "no-such-model", "source_job_id": "any-job"},
        headers={"X-API-Key": write_key},
    )
    assert r.status_code == 422


async def test_restore_requires_write_scope(client, read_key, two_people):
    pid = two_people[0]
    r = await client.post(
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
        r = await client.delete(
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


async def test_batch_delete_zero_when_no_matches(client, write_key, two_people):
    pid = two_people[0]
    r = await client.delete(
        f"/api/v1/people/{pid}/embeddings",
        params={"model_id": _MODEL_ID, "source_job_id": "no-such-job-xyz"},
        headers={"X-API-Key": write_key},
    )
    assert r.status_code == 200
    assert r.json()["archived_count"] == 0


# ---------------------------------------------------------------------------
# POST /{embedding_id}/restore
# ---------------------------------------------------------------------------


async def test_restore_404_unknown_embedding(client, write_key, two_people):
    pid = two_people[0]
    r = await client.post(
        f"/api/v1/people/{pid}/embeddings/{generate_id()}/restore",
        params={"model_id": _MODEL_ID},
        headers={"X-API-Key": write_key},
    )
    assert r.status_code == 404


async def test_restore_reactivates_archived_embedding(client, db, write_key, archivable_embedding):
    pid, eid = archivable_embedding
    await db.execute(f"UPDATE {_TABLE} SET archived_at=now() WHERE id=$1", eid)
    try:
        r = await client.post(
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
    r = await client.post(
        f"/api/v1/people/{pid}/embeddings/{eid}/restore",
        params={"model_id": _MODEL_ID},
        headers={"X-API-Key": write_key},
    )
    assert r.status_code == 409


# ---------------------------------------------------------------------------
# GET /{person_id}/embeddings — listing
# ---------------------------------------------------------------------------


async def test_list_requires_read_scope(client, unscoped_key, two_people):
    pid = two_people[0]
    r = await client.get(
        f"/api/v1/people/{pid}/embeddings",
        params={"model_id": _MODEL_ID},
        headers={"X-API-Key": unscoped_key},
    )
    assert r.status_code == 403


async def test_list_422_unknown_model(client, read_key, two_people):
    pid = two_people[0]
    r = await client.get(
        f"/api/v1/people/{pid}/embeddings",
        params={"model_id": "no-such-model"},
        headers={"X-API-Key": read_key},
    )
    assert r.status_code == 422


async def test_list_active_only_by_default(client, read_key, seeded_embeddings):
    pid0, _, _, _ = seeded_embeddings
    r = await client.get(
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
        r_active = await client.get(
            f"/api/v1/people/{pid0}/embeddings",
            params={"model_id": _MODEL_ID},
            headers={"X-API-Key": read_key},
        )
        r_all = await client.get(
            f"/api/v1/people/{pid0}/embeddings",
            params={"model_id": _MODEL_ID, "include_archived": "true"},
            headers={"X-API-Key": read_key},
        )
        assert r_all.json()["meta"]["count"] > r_active.json()["meta"]["count"]
        archived = [item for item in r_all.json()["data"] if item["archived_at"] is not None]
        assert len(archived) >= 1
    finally:
        await db.execute(f"UPDATE {_TABLE} SET archived_at=NULL WHERE id=$1", eid)


async def test_list_pagination_stable_under_tied_created_at(
    client, db, read_key, write_key, two_people
):
    """Offset pagination is deterministic and complete when rows share created_at (#297).

    Bulk-ingested embeddings from one job can land at an identical ``created_at``.
    With ``ORDER BY created_at DESC`` alone, Postgres may return tied rows in a
    different order per query, so offset windows overlap and gap — the client
    sees duplicates and silently skips others. The unique PK ``id`` must break
    the tie so the total order is deterministic.
    """
    pid = two_people[0]
    # Seed 12 embeddings for one person, then force one shared created_at so the
    # sort key ties across every row.
    embedding_ids: list[str] = []
    for seg in range(50):
        r = await client.post(
            f"/api/v1/people/{pid}/embeddings",
            json={
                "model_id": _MODEL_ID,
                "embedding": _rand_embedding(),
                "activity_ms": 500,
                "audio_sample_rate_hz": 16000,
                "source": {
                    "service": "observo",
                    "job_id": "job_tied",
                    "segment": 900 + seg,
                    "recorded_at": "2026-06-01T00:00:00Z",
                },
            },
            headers={"X-API-Key": write_key},
        )
        assert r.status_code == 200, r.text
        embedding_ids.append(r.json()["embedding_id"])

    await db.execute(
        f"UPDATE {_TABLE} SET created_at = '2026-07-07T00:23:12.311563Z'"
        " WHERE id = ANY($1::text[])",
        embedding_ids,
    )

    limit = 3
    collected: list[str] = []
    offset = 0
    while True:
        r = await client.get(
            f"/api/v1/people/{pid}/embeddings",
            params={"model_id": _MODEL_ID, "limit": limit, "offset": offset},
            headers={"X-API-Key": read_key},
        )
        assert r.status_code == 200
        body = r.json()
        collected.extend(item["embedding_id"] for item in body["data"])
        if not body["meta"]["has_more"]:
            break
        offset += limit

    # Complete and duplicate-free: every seeded id appears exactly once.
    assert len(collected) == len(embedding_ids)
    assert set(collected) == set(embedding_ids)
    # Deterministic total order: tied created_at → id DESC (ULIDs ascending in
    # insertion order, so newest-first is reverse-sorted).
    assert collected == sorted(embedding_ids, reverse=True)


# ---------------------------------------------------------------------------
# List — optional source_job_id filter (#279)
# ---------------------------------------------------------------------------


async def test_list_filter_by_source_job_id(client, db, read_key, write_key, seeded_embeddings):
    """Optional source_job_id narrows list results to that job; omitting returns all."""
    pid0, _, _, _ = seeded_embeddings
    # Add one embedding under a different job for the same person.
    r = await client.post(
        f"/api/v1/people/{pid0}/embeddings",
        json={
            "model_id": _MODEL_ID,
            "embedding": _rand_embedding(),
            "activity_ms": 500,
            "audio_sample_rate_hz": 16000,
            "source": {
                "service": "observo",
                "job_id": "job_other",
                "segment": 0,
                "recorded_at": "2026-06-01T00:00:00Z",
            },
        },
        headers={"X-API-Key": write_key},
    )
    assert r.status_code == 200, r.text
    try:
        # Unfiltered: all of pid0's active rows (3 from job_seed + 1 from job_other).
        r_all = await client.get(
            f"/api/v1/people/{pid0}/embeddings",
            params={"model_id": _MODEL_ID},
            headers={"X-API-Key": read_key},
        )
        assert r_all.json()["meta"]["count"] >= 4

        # Filtered to job_seed: only those rows.
        r_seed = await client.get(
            f"/api/v1/people/{pid0}/embeddings",
            params={"model_id": _MODEL_ID, "source_job_id": "job_seed"},
            headers={"X-API-Key": read_key},
        )
        assert r_seed.status_code == 200
        seed_body = r_seed.json()
        assert seed_body["meta"]["count"] == 3
        assert all(item["source_job_id"] == "job_seed" for item in seed_body["data"])

        # Filtered to job_other: exactly the one row.
        r_other = await client.get(
            f"/api/v1/people/{pid0}/embeddings",
            params={"model_id": _MODEL_ID, "source_job_id": "job_other"},
            headers={"X-API-Key": read_key},
        )
        assert r_other.json()["meta"]["count"] == 1
        assert r_other.json()["data"][0]["source_job_id"] == "job_other"
    finally:
        await db.execute(f"DELETE FROM {_TABLE} WHERE source_job_id='job_other'")


async def test_list_filter_by_source_job_id_no_match_returns_empty(
    client, read_key, seeded_embeddings
):
    """Unknown source_job_id yields an empty page (200), not 404."""
    pid0, _, _, _ = seeded_embeddings
    r = await client.get(
        f"/api/v1/people/{pid0}/embeddings",
        params={"model_id": _MODEL_ID, "source_job_id": "no-such-job-xyz"},
        headers={"X-API-Key": read_key},
    )
    assert r.status_code == 200
    assert r.json()["meta"]["count"] == 0
    assert r.json()["data"] == []


async def test_list_filter_by_source_job_id_respects_archived(
    client, db, read_key, seeded_embeddings
):
    """The source_job_id filter honors the active-only default and include_archived."""
    pid0, _, embedding_ids, _ = seeded_embeddings
    eid = embedding_ids[0]  # a job_seed row for pid0
    await db.execute(f"UPDATE {_TABLE} SET archived_at=now() WHERE id=$1", eid)
    try:
        # Default (active-only) + job filter: the archived row is excluded.
        r_active = await client.get(
            f"/api/v1/people/{pid0}/embeddings",
            params={"model_id": _MODEL_ID, "source_job_id": "job_seed"},
            headers={"X-API-Key": read_key},
        )
        assert r_active.status_code == 200
        active = r_active.json()
        assert active["meta"]["count"] == 2
        assert all(item["archived_at"] is None for item in active["data"])
        assert eid not in {item["embedding_id"] for item in active["data"]}

        # include_archived + job filter: the archived row is included.
        r_all = await client.get(
            f"/api/v1/people/{pid0}/embeddings",
            params={
                "model_id": _MODEL_ID,
                "source_job_id": "job_seed",
                "include_archived": "true",
            },
            headers={"X-API-Key": read_key},
        )
        assert r_all.json()["meta"]["count"] == 3
        assert eid in {item["embedding_id"] for item in r_all.json()["data"]}
    finally:
        await db.execute(f"UPDATE {_TABLE} SET archived_at=NULL WHERE id=$1", eid)


# ---------------------------------------------------------------------------
# List — optional source_segment filter (#299)
# ---------------------------------------------------------------------------


async def test_list_filter_by_source_segment(client, read_key, seeded_embeddings):
    """source_job_id + source_segment narrows to the exact provenance row."""
    pid0, _, embedding_ids, _ = seeded_embeddings
    r = await client.get(
        f"/api/v1/people/{pid0}/embeddings",
        params={"model_id": _MODEL_ID, "source_job_id": "job_seed", "source_segment": 1},
        headers={"X-API-Key": read_key},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["meta"]["count"] == 1
    assert body["data"][0]["embedding_id"] == embedding_ids[1]
    assert body["data"][0]["source_segment"] == 1


async def test_list_filter_source_segment_finds_archived_row(
    client, db, read_key, seeded_embeddings
):
    """The observo 409-recovery shape: one call finds the archived provenance row."""
    pid0, _, embedding_ids, _ = seeded_embeddings
    eid = embedding_ids[2]  # job_seed segment 2
    await db.execute(f"UPDATE {_TABLE} SET archived_at=now() WHERE id=$1", eid)
    try:
        r = await client.get(
            f"/api/v1/people/{pid0}/embeddings",
            params={
                "model_id": _MODEL_ID,
                "source_job_id": "job_seed",
                "source_segment": 2,
                "include_archived": "true",
            },
            headers={"X-API-Key": read_key},
        )
        assert r.status_code == 200
        body = r.json()
        assert body["meta"]["count"] == 1
        assert body["data"][0]["embedding_id"] == eid
        assert body["data"][0]["archived_at"] is not None

        # Active-only default excludes it.
        r_active = await client.get(
            f"/api/v1/people/{pid0}/embeddings",
            params={"model_id": _MODEL_ID, "source_job_id": "job_seed", "source_segment": 2},
            headers={"X-API-Key": read_key},
        )
        assert r_active.json()["meta"]["count"] == 0
    finally:
        await db.execute(f"UPDATE {_TABLE} SET archived_at=NULL WHERE id=$1", eid)


async def test_list_filter_source_segment_without_job(client, read_key, seeded_embeddings):
    """source_segment composes independently of source_job_id (person-scoped anyway)."""
    pid0, _, embedding_ids, _ = seeded_embeddings
    r = await client.get(
        f"/api/v1/people/{pid0}/embeddings",
        params={"model_id": _MODEL_ID, "source_segment": 0},
        headers={"X-API-Key": read_key},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["meta"]["count"] >= 1
    assert all(item["source_segment"] == 0 for item in body["data"])


# ---------------------------------------------------------------------------
# Person-existence guard — list and batch delete
# ---------------------------------------------------------------------------


async def test_list_404_unknown_person(client, read_key):
    r = await client.get(
        f"/api/v1/people/{generate_id()}/embeddings",
        params={"model_id": _MODEL_ID},
        headers={"X-API-Key": read_key},
    )
    assert r.status_code == 404


async def test_list_404_archived_person(client, read_key, archived_person):
    r = await client.get(
        f"/api/v1/people/{archived_person}/embeddings",
        params={"model_id": _MODEL_ID},
        headers={"X-API-Key": read_key},
    )
    assert r.status_code == 404


async def test_batch_delete_404_unknown_person(client, write_key):
    r = await client.delete(
        f"/api/v1/people/{generate_id()}/embeddings",
        params={"model_id": _MODEL_ID, "source_job_id": "any-job"},
        headers={"X-API-Key": write_key},
    )
    assert r.status_code == 404


async def test_batch_delete_404_archived_person(client, write_key, archived_person):
    r = await client.delete(
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
    r = await client.post(
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
    return pid, eid


async def test_write_on_archived_slot_returns_409(client, db, write_key, patchable_embedding):
    """POST with same provenance key as an archived row must 409, not return the archived row."""
    pid, eid = patchable_embedding
    await db.execute(f"UPDATE {_TABLE} SET archived_at=now() WHERE id=$1", eid)
    try:
        r = await client.post(
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


async def test_patch_requires_write_scope(client, read_key, two_people):
    pid = two_people[0]
    r = await client.patch(
        f"/api/v1/people/{pid}/embeddings/{generate_id()}",
        params={"model_id": _MODEL_ID},
        json={"audio_sample_rate_hz": 48000},
        headers={"X-API-Key": read_key},
    )
    assert r.status_code == 403


async def test_patch_422_unknown_model(client, write_key, two_people):
    pid = two_people[0]
    r = await client.patch(
        f"/api/v1/people/{pid}/embeddings/{generate_id()}",
        params={"model_id": "no-such-model"},
        json={"audio_sample_rate_hz": 48000},
        headers={"X-API-Key": write_key},
    )
    assert r.status_code == 422


async def test_patch_422_empty_body(client, write_key, patchable_embedding):
    pid, eid = patchable_embedding
    r = await client.patch(
        f"/api/v1/people/{pid}/embeddings/{eid}",
        params={"model_id": _MODEL_ID},
        json={},
        headers={"X-API-Key": write_key},
    )
    assert r.status_code == 422


async def test_patch_404_unknown_embedding(client, write_key, two_people):
    pid = two_people[0]
    r = await client.patch(
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
        r = await client.patch(
            f"/api/v1/people/{pid}/embeddings/{eid}",
            params={"model_id": _MODEL_ID},
            json={"audio_sample_rate_hz": 48000},
            headers={"X-API-Key": write_key},
        )
        assert r.status_code == 409
    finally:
        await db.execute(f"UPDATE {_TABLE} SET archived_at=NULL WHERE id=$1", eid)


async def test_patch_updates_audio_sample_rate(client, write_key, patchable_embedding):
    pid, eid = patchable_embedding
    r = await client.patch(
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


async def test_patch_updates_activity_ms(client, write_key, patchable_embedding):
    pid, eid = patchable_embedding
    r = await client.patch(
        f"/api/v1/people/{pid}/embeddings/{eid}",
        params={"model_id": _MODEL_ID},
        json={"activity_ms": 750},
        headers={"X-API-Key": write_key},
    )
    assert r.status_code == 200
    assert r.json()["activity_ms"] == 750


async def test_patch_updates_recorded_at(client, write_key, patchable_embedding):
    pid, eid = patchable_embedding
    # Use non-zero microseconds so isoformat() preserves the fractional part
    new_ts = "2026-06-15T10:00:00.123456Z"
    r = await client.patch(
        f"/api/v1/people/{pid}/embeddings/{eid}",
        params={"model_id": _MODEL_ID},
        json={"recorded_at": new_ts},
        headers={"X-API-Key": write_key},
    )
    assert r.status_code == 200
    assert r.json()["recorded_at"] == new_ts


async def test_patch_multi_field(client, write_key, patchable_embedding):
    pid, eid = patchable_embedding
    r = await client.patch(
        f"/api/v1/people/{pid}/embeddings/{eid}",
        params={"model_id": _MODEL_ID},
        json={"activity_ms": 100, "audio_sample_rate_hz": 8000},
        headers={"X-API-Key": write_key},
    )
    assert r.status_code == 200
    data = r.json()
    assert data["activity_ms"] == 100
    assert data["audio_sample_rate_hz"] == 8000
