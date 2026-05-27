"""Tests for GET /api/v1/people/search and GET /api/v1/people/{id}."""

import hashlib
import os

import pytest
import pytest_asyncio

from src.core.db import generate_id

pytestmark = [pytest.mark.asyncio(loop_scope="session")]


@pytest_asyncio.fixture(loop_scope="session")
async def api_key(db):
    uid = generate_id()
    kid = generate_id()
    raw_key = "pm_" + os.urandom(16).hex()
    key_hash = hashlib.sha256(raw_key.encode()).hexdigest()
    await db.execute("INSERT INTO app_users (id, email) VALUES ($1,$2)", uid, "peopletest@test.com")
    await db.execute(
        "INSERT INTO api_keys (id, user_id, label, key_prefix, key_hash) VALUES ($1,$2,$3,$4,$5)",
        kid,
        uid,
        "People Test Key",
        raw_key[:8],
        key_hash,
    )
    yield raw_key
    await db.execute("DELETE FROM api_keys WHERE id=$1", kid)
    await db.execute("DELETE FROM app_users WHERE id=$1", uid)


@pytest_asyncio.fixture(loop_scope="session")
async def person_fixture(db):
    """Create a test person with names and identifiers; yield ids; clean up."""
    person_id = generate_id()
    canonical_name_id = generate_id()
    variant_name_id = generate_id()
    hidden_name_id = generate_id()
    legal_only_name_id = generate_id()

    await db.execute("INSERT INTO people (id) VALUES ($1)", person_id)
    await db.execute(
        "INSERT INTO person_names (id, person_id, name, name_type, is_canonical, visibility)"
        " VALUES ($1,$2,$3,'legal',TRUE,'public')",
        canonical_name_id,
        person_id,
        "Jane Elizabeth Smith",
    )
    await db.execute(
        "INSERT INTO person_names (id, person_id, name, name_type, is_canonical, visibility)"
        " VALUES ($1,$2,$3,'preferred',FALSE,'public')",
        variant_name_id,
        person_id,
        "Jane Smith",
    )
    await db.execute(
        "INSERT INTO person_names (id, person_id, name, name_type, is_canonical, visibility)"
        " VALUES ($1,$2,$3,'legal',FALSE,'hidden')",
        hidden_name_id,
        person_id,
        "SecretNameShouldNotAppear",
    )
    await db.execute(
        "INSERT INTO person_names (id, person_id, name, name_type, is_canonical, visibility)"
        " VALUES ($1,$2,$3,'alias',FALSE,'legal_only')",
        legal_only_name_id,
        person_id,
        "LegalOnlyNameShouldNotAppear",
    )

    type_row = await db.fetchrow(
        "SELECT id FROM entity_identifier_types WHERE slug='person_wa_pdc' LIMIT 1"
    )
    if type_row:
        eid_type_id = type_row["id"]
    else:
        eid_type_id = generate_id()
        await db.execute(
            "INSERT INTO entity_identifier_types (id, entity_type, slug, display_name, full_name)"
            " VALUES ($1,'person','person_wa_pdc','WA PDC','WA Public Disclosure Commission')",
            eid_type_id,
        )

    eid_id = generate_id()
    await db.execute(
        "INSERT INTO identifiers (id, entity_id, entity_identifier_type_id, value)"
        " VALUES ($1,$2,$3,$4)",
        eid_id,
        person_id,
        eid_type_id,
        "PDC-99999",
    )

    yield {
        "person_id": person_id,
        "canonical_name_id": canonical_name_id,
        "variant_name_id": variant_name_id,
        "hidden_name_id": hidden_name_id,
        "legal_only_name_id": legal_only_name_id,
        "eid_id": eid_id,
        "eid_type_id": eid_type_id,
    }

    await db.execute("DELETE FROM identifiers WHERE id=$1", eid_id)
    await db.execute("DELETE FROM person_names WHERE person_id=$1", person_id)
    await db.execute("DELETE FROM people WHERE id=$1", person_id)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _search(client, api_key, q, **params):
    return client.get(
        "/api/v1/people/search",
        params={"q": q, **params},
        headers={"X-API-Key": api_key},
    )


# ---------------------------------------------------------------------------
# Search — response shape
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_search_response_envelope(client, api_key, person_fixture):
    r = _search(client, api_key, "Jane")
    assert r.status_code == 200
    body = r.json()
    assert "data" in body
    assert "meta" in body
    meta = body["meta"]
    assert "limit" in meta
    assert "offset" in meta
    assert "count" in meta
    assert "has_more" in meta


@pytest.mark.integration
async def test_search_meta_reflects_params(client, api_key, person_fixture):
    r = _search(client, api_key, "Jane", limit=5, offset=0)
    meta = r.json()["meta"]
    assert meta["limit"] == 5
    assert meta["offset"] == 0


@pytest.mark.integration
async def test_search_meta_count_matches_data(client, api_key, person_fixture):
    r = _search(client, api_key, "Jane")
    body = r.json()
    assert body["meta"]["count"] == len(body["data"])


@pytest.mark.integration
async def test_search_has_more_false_when_under_limit(client, api_key, person_fixture):
    r = _search(client, api_key, "Jane Elizabeth Smith", limit=50)
    assert r.json()["meta"]["has_more"] is False


@pytest.mark.integration
async def test_search_has_more_true(client, api_key, person_fixture, db):
    """has_more=True when matching results exceed limit."""
    second_id = generate_id()
    name_id = generate_id()
    await db.execute("INSERT INTO people (id) VALUES ($1)", second_id)
    await db.execute(
        "INSERT INTO person_names"
        " (id, person_id, name, name_type, is_canonical, visibility)"
        " VALUES ($1,$2,'Jane Adams','legal',TRUE,'public')",
        name_id,
        second_id,
    )
    try:
        r = _search(client, api_key, "Jane", limit=1)
        assert r.json()["meta"]["has_more"] is True
    finally:
        await db.execute("DELETE FROM person_names WHERE person_id=$1", second_id)
        await db.execute("DELETE FROM people WHERE id=$1", second_id)


# ---------------------------------------------------------------------------
# Search — result content
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_search_by_canonical_name(client, api_key, person_fixture):
    r = _search(client, api_key, "Jane Elizabeth")
    assert r.status_code == 200
    ids = [p["id"] for p in r.json()["data"]]
    assert person_fixture["person_id"] in ids
    hit = next(p for p in r.json()["data"] if p["id"] == person_fixture["person_id"])
    assert hit["display_name"] == "Jane Elizabeth Smith"
    assert hit["archived_at"] is None


@pytest.mark.integration
async def test_search_by_name_variant(client, api_key, person_fixture):
    r = _search(client, api_key, "Jane Smith")
    assert r.status_code == 200
    ids = [p["id"] for p in r.json()["data"]]
    assert person_fixture["person_id"] in ids


@pytest.mark.integration
async def test_search_does_not_match_hidden_names(client, api_key, person_fixture):
    """Hidden name variants must not be searchable by their content."""
    r = _search(client, api_key, "SecretNameShouldNotAppear")
    assert r.status_code == 200
    ids = [p["id"] for p in r.json()["data"]]
    assert person_fixture["person_id"] not in ids


@pytest.mark.integration
async def test_search_does_not_match_legal_only_names(client, api_key, person_fixture):
    """legal_only name variants must not be searchable by their content."""
    r = _search(client, api_key, "LegalOnlyNameShouldNotAppear")
    assert r.status_code == 200
    ids = [p["id"] for p in r.json()["data"]]
    assert person_fixture["person_id"] not in ids


@pytest.mark.integration
async def test_search_excludes_archived_by_default(client, api_key, person_fixture, db):
    await db.execute(
        "UPDATE people SET archived_at=NOW() WHERE id=$1", person_fixture["person_id"]
    )
    r = _search(client, api_key, "Jane")
    assert r.status_code == 200
    ids = [p["id"] for p in r.json()["data"]]
    assert person_fixture["person_id"] not in ids
    await db.execute("UPDATE people SET archived_at=NULL WHERE id=$1", person_fixture["person_id"])


@pytest.mark.integration
async def test_search_include_archived_flag(client, api_key, person_fixture, db):
    await db.execute(
        "UPDATE people SET archived_at=NOW() WHERE id=$1", person_fixture["person_id"]
    )
    r = _search(client, api_key, "Jane", include_archived="true")
    assert r.status_code == 200
    ids = [p["id"] for p in r.json()["data"]]
    assert person_fixture["person_id"] in ids
    await db.execute("UPDATE people SET archived_at=NULL WHERE id=$1", person_fixture["person_id"])


@pytest.mark.integration
async def test_search_archived_result_has_z_suffix_timestamp(client, api_key, person_fixture, db):
    await db.execute(
        "UPDATE people SET archived_at=NOW() WHERE id=$1", person_fixture["person_id"]
    )
    r = _search(client, api_key, "Jane", include_archived="true")
    hit = next(p for p in r.json()["data"] if p["id"] == person_fixture["person_id"])
    assert hit["archived_at"].endswith("Z"), f"expected Z suffix, got {hit['archived_at']}"
    await db.execute("UPDATE people SET archived_at=NULL WHERE id=$1", person_fixture["person_id"])


@pytest.mark.integration
async def test_search_limit(client, api_key, person_fixture):
    r = _search(client, api_key, "Jane", limit=1)
    assert r.status_code == 200
    assert len(r.json()["data"]) <= 1


@pytest.mark.integration
async def test_search_limit_capped_at_50(client, api_key, person_fixture):
    r = _search(client, api_key, "a", limit=999)
    assert r.status_code == 200
    assert r.json()["meta"]["limit"] == 50


@pytest.mark.integration
async def test_search_empty_q_returns_empty_envelope(client, api_key):
    r = _search(client, api_key, "")
    assert r.status_code == 200
    body = r.json()
    assert body["data"] == []
    assert body["meta"]["count"] == 0
    assert body["meta"]["has_more"] is False


@pytest.mark.integration
async def test_search_empty_q_limit_capped_at_50(client, api_key):
    r = _search(client, api_key, "", limit=999)
    assert r.status_code == 200
    assert r.json()["meta"]["limit"] == 50


# ---------------------------------------------------------------------------
# Get by ID
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_get_person_by_id_full_record(client, api_key, person_fixture):
    pid = person_fixture["person_id"]
    r = client.get(f"/api/v1/people/{pid}", headers={"X-API-Key": api_key})
    assert r.status_code == 200
    data = r.json()

    assert data["id"] == pid
    assert data["display_name"] == "Jane Elizabeth Smith"
    assert data["archived_at"] is None

    # Only public names — canonical + variant, not hidden or legal_only
    public_name_ids = {n["id"] for n in data["names"]}
    assert person_fixture["canonical_name_id"] in public_name_ids
    assert person_fixture["variant_name_id"] in public_name_ids
    assert person_fixture["hidden_name_id"] not in public_name_ids
    assert person_fixture["legal_only_name_id"] not in public_name_ids

    canonical = next(n for n in data["names"] if n["is_canonical"])
    assert canonical["name"] == "Jane Elizabeth Smith"
    assert canonical["name_type"] == "legal"

    assert len(data["identifiers"]) >= 1
    eid = next(i for i in data["identifiers"] if i["id"] == person_fixture["eid_id"])
    assert eid["type_id"] == person_fixture["eid_type_id"]
    assert eid["type_slug"] == "person_wa_pdc"
    assert eid["value"] == "PDC-99999"


@pytest.mark.integration
async def test_get_person_by_id_not_found(client, api_key):
    r = client.get("/api/v1/people/01DOESNOTEXIST00000000000000", headers={"X-API-Key": api_key})
    assert r.status_code == 404


@pytest.mark.integration
async def test_get_archived_person_still_returned(client, api_key, person_fixture, db):
    """GET by ID returns archived people — caller must check archived_at."""
    await db.execute(
        "UPDATE people SET archived_at=NOW() WHERE id=$1", person_fixture["person_id"]
    )
    r = client.get(
        f"/api/v1/people/{person_fixture['person_id']}", headers={"X-API-Key": api_key}
    )
    assert r.status_code == 200
    archived_at = r.json()["archived_at"]
    assert archived_at is not None
    assert archived_at.endswith("Z"), f"expected Z suffix, got {archived_at}"
    await db.execute("UPDATE people SET archived_at=NULL WHERE id=$1", person_fixture["person_id"])


@pytest.mark.integration
async def test_get_person_detail_names_only_public(client, api_key, person_fixture):
    """Detail endpoint must not leak hidden or legal_only name variants."""
    pid = person_fixture["person_id"]
    r = client.get(f"/api/v1/people/{pid}", headers={"X-API-Key": api_key})
    assert r.status_code == 200
    names = r.json()["names"]
    returned_ids = {n["id"] for n in names}
    assert person_fixture["hidden_name_id"] not in returned_ids, "hidden name leaked"
    assert person_fixture["legal_only_name_id"] not in returned_ids, "legal_only name leaked"
