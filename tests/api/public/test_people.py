"""Tests for GET /api/v1/people/search and GET /api/v1/people/{id}."""

import hashlib
import os

import pytest
import pytest_asyncio

from src.core.db import generate_id

pytestmark = pytest.mark.integration


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


def _search_by_identifier(client, api_key, identifier_type, identifier_value, **params):
    return client.get(
        "/api/v1/people/search",
        params={"identifier_type": identifier_type, "identifier_value": identifier_value, **params},
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
    await db.execute("UPDATE people SET archived_at=NOW() WHERE id=$1", person_fixture["person_id"])
    r = _search(client, api_key, "Jane")
    assert r.status_code == 200
    ids = [p["id"] for p in r.json()["data"]]
    assert person_fixture["person_id"] not in ids
    await db.execute("UPDATE people SET archived_at=NULL WHERE id=$1", person_fixture["person_id"])


@pytest.mark.integration
async def test_search_include_archived_flag(client, api_key, person_fixture, db):
    await db.execute("UPDATE people SET archived_at=NOW() WHERE id=$1", person_fixture["person_id"])
    r = _search(client, api_key, "Jane", include_archived="true")
    assert r.status_code == 200
    ids = [p["id"] for p in r.json()["data"]]
    assert person_fixture["person_id"] in ids
    await db.execute("UPDATE people SET archived_at=NULL WHERE id=$1", person_fixture["person_id"])


@pytest.mark.integration
async def test_search_archived_result_has_z_suffix_timestamp(client, api_key, person_fixture, db):
    await db.execute("UPDATE people SET archived_at=NOW() WHERE id=$1", person_fixture["person_id"])
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
    await db.execute("UPDATE people SET archived_at=NOW() WHERE id=$1", person_fixture["person_id"])
    r = client.get(f"/api/v1/people/{person_fixture['person_id']}", headers={"X-API-Key": api_key})
    assert r.status_code == 200
    archived_at = r.json()["archived_at"]
    assert archived_at is not None
    assert archived_at.endswith("Z"), f"expected Z suffix, got {archived_at}"
    await db.execute("UPDATE people SET archived_at=NULL WHERE id=$1", person_fixture["person_id"])


@pytest.mark.integration
async def test_get_person_detail_timestamps(client, api_key, person_fixture):
    """PersonDetail must expose created_at and updated_at with Z-suffix ISO 8601."""
    pid = person_fixture["person_id"]
    r = client.get(f"/api/v1/people/{pid}", headers={"X-API-Key": api_key})
    assert r.status_code == 200
    data = r.json()
    assert "created_at" in data, "created_at missing from PersonDetail"
    assert "updated_at" in data, "updated_at missing from PersonDetail"
    assert data["created_at"].endswith("Z"), f"created_at missing Z suffix: {data['created_at']}"
    assert data["updated_at"].endswith("Z"), f"updated_at missing Z suffix: {data['updated_at']}"


@pytest.mark.integration
async def test_search_people_does_not_expose_timestamps(client, api_key, person_fixture):
    """Search results must not include created_at or updated_at (detail-only fields)."""
    r = _search(client, api_key, "Jane Elizabeth")
    assert r.status_code == 200
    hit = next(p for p in r.json()["data"] if p["id"] == person_fixture["person_id"])
    assert "created_at" not in hit
    assert "updated_at" not in hit


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


# ---------------------------------------------------------------------------
# Search — identifier_type / identifier_value filter
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_identifier_search_returns_correct_person(client, api_key, person_fixture):
    r = _search_by_identifier(client, api_key, "person_wa_pdc", "PDC-99999")
    assert r.status_code == 200
    body = r.json()
    ids = [p["id"] for p in body["data"]]
    assert person_fixture["person_id"] in ids
    assert body["meta"]["has_more"] is False


@pytest.mark.integration
async def test_identifier_search_unknown_type_returns_empty(client, api_key, person_fixture):
    r = _search_by_identifier(client, api_key, "nonexistent_slug", "PDC-99999")
    assert r.status_code == 200
    assert r.json()["data"] == []
    assert r.json()["meta"]["has_more"] is False


@pytest.mark.integration
async def test_identifier_search_unknown_value_returns_empty(client, api_key, person_fixture):
    r = _search_by_identifier(client, api_key, "person_wa_pdc", "DOES-NOT-EXIST")
    assert r.status_code == 200
    assert r.json()["data"] == []
    assert r.json()["meta"]["has_more"] is False


@pytest.mark.integration
async def test_identifier_search_empty_type_returns_422(client, api_key):
    r = client.get(
        "/api/v1/people/search",
        params={"identifier_type": "", "identifier_value": "PDC-99999"},
        headers={"X-API-Key": api_key},
    )
    assert r.status_code == 422


@pytest.mark.integration
async def test_identifier_search_empty_value_returns_422(client, api_key):
    r = client.get(
        "/api/v1/people/search",
        params={"identifier_type": "person_wa_pdc", "identifier_value": ""},
        headers={"X-API-Key": api_key},
    )
    assert r.status_code == 422


@pytest.mark.integration
async def test_identifier_search_type_only_returns_422(client, api_key):
    r = client.get(
        "/api/v1/people/search",
        params={"identifier_type": "person_wa_pdc"},
        headers={"X-API-Key": api_key},
    )
    assert r.status_code == 422


@pytest.mark.integration
async def test_identifier_search_value_only_returns_422(client, api_key):
    r = client.get(
        "/api/v1/people/search",
        params={"identifier_value": "PDC-99999"},
        headers={"X-API-Key": api_key},
    )
    assert r.status_code == 422


@pytest.mark.integration
async def test_identifier_search_wins_over_q(client, api_key, person_fixture, db):
    """When both q and identifier params are given, identifier takes precedence."""
    other_id = generate_id()
    other_name_id = generate_id()
    await db.execute("INSERT INTO people (id) VALUES ($1)", other_id)
    await db.execute(
        "INSERT INTO person_names (id, person_id, name, name_type, is_canonical, visibility)"
        " VALUES ($1,$2,'Jane Other','legal',TRUE,'public')",
        other_name_id,
        other_id,
    )
    try:
        # q matches both "Jane" people; identifier should narrow to exactly one
        r = client.get(
            "/api/v1/people/search",
            params={
                "q": "Jane",
                "identifier_type": "person_wa_pdc",
                "identifier_value": "PDC-99999",
            },
            headers={"X-API-Key": api_key},
        )
        assert r.status_code == 200
        ids = [p["id"] for p in r.json()["data"]]
        assert person_fixture["person_id"] in ids
        assert other_id not in ids
    finally:
        await db.execute("DELETE FROM person_names WHERE id=$1", other_name_id)
        await db.execute("DELETE FROM people WHERE id=$1", other_id)


@pytest.mark.integration
async def test_identifier_search_excludes_archived_by_default(client, api_key, person_fixture, db):
    await db.execute("UPDATE people SET archived_at=NOW() WHERE id=$1", person_fixture["person_id"])
    r = _search_by_identifier(client, api_key, "person_wa_pdc", "PDC-99999")
    assert r.status_code == 200
    ids = [p["id"] for p in r.json()["data"]]
    assert person_fixture["person_id"] not in ids
    await db.execute("UPDATE people SET archived_at=NULL WHERE id=$1", person_fixture["person_id"])


@pytest.mark.integration
async def test_identifier_search_include_archived(client, api_key, person_fixture, db):
    await db.execute("UPDATE people SET archived_at=NOW() WHERE id=$1", person_fixture["person_id"])
    r = _search_by_identifier(
        client, api_key, "person_wa_pdc", "PDC-99999", include_archived="true"
    )
    assert r.status_code == 200
    ids = [p["id"] for p in r.json()["data"]]
    assert person_fixture["person_id"] in ids
    await db.execute("UPDATE people SET archived_at=NULL WHERE id=$1", person_fixture["person_id"])


# ---------------------------------------------------------------------------
# ETag / conditional GET
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_get_person_etag_and_last_modified_present(client, api_key, person_fixture):
    pid = person_fixture["person_id"]
    r = client.get(f"/api/v1/people/{pid}", headers={"X-API-Key": api_key})
    assert r.status_code == 200
    etag = r.headers.get("etag")
    assert etag is not None, "ETag header missing"
    assert etag.startswith('"') and etag.endswith('"'), f"ETag not quoted: {etag}"
    assert r.headers.get("last-modified") is not None, "Last-Modified header missing"


@pytest.mark.integration
async def test_get_person_cache_control_and_vary_present(client, api_key, person_fixture):
    pid = person_fixture["person_id"]
    r = client.get(f"/api/v1/people/{pid}", headers={"X-API-Key": api_key})
    assert r.status_code == 200
    assert r.headers.get("cache-control") == "no-cache"
    assert r.headers.get("vary") == "X-API-Key"


@pytest.mark.integration
async def test_get_person_304_on_matching_etag(client, api_key, person_fixture):
    pid = person_fixture["person_id"]
    r1 = client.get(f"/api/v1/people/{pid}", headers={"X-API-Key": api_key})
    etag = r1.headers["etag"]
    r2 = client.get(
        f"/api/v1/people/{pid}",
        headers={"X-API-Key": api_key, "If-None-Match": etag},
    )
    assert r2.status_code == 304
    assert r2.content == b""
    assert r2.headers.get("etag") == etag
    assert r2.headers.get("cache-control") == "no-cache"


@pytest.mark.integration
async def test_get_person_200_on_mismatched_etag(client, api_key, person_fixture):
    pid = person_fixture["person_id"]
    r = client.get(
        f"/api/v1/people/{pid}",
        headers={"X-API-Key": api_key, "If-None-Match": '"wrong-etag-value"'},
    )
    assert r.status_code == 200
    assert r.json()["id"] == pid


@pytest.mark.integration
async def test_get_person_etag_changes_after_parent_update(client, api_key, person_fixture, db):
    pid = person_fixture["person_id"]
    r1 = client.get(f"/api/v1/people/{pid}", headers={"X-API-Key": api_key})
    etag1 = r1.headers["etag"]

    await db.execute("SELECT pg_sleep(0.001)")
    await db.execute("UPDATE people SET archived_at = archived_at WHERE id=$1", pid)

    r2 = client.get(f"/api/v1/people/{pid}", headers={"X-API-Key": api_key})
    assert r2.headers["etag"] != etag1

    r3 = client.get(
        f"/api/v1/people/{pid}",
        headers={"X-API-Key": api_key, "If-None-Match": etag1},
    )
    assert r3.status_code == 200


@pytest.mark.integration
async def test_get_person_etag_changes_after_name_added(client, api_key, person_fixture, db):
    """Touch-parent trigger: adding a name row bumps the person's updated_at."""
    pid = person_fixture["person_id"]
    r1 = client.get(f"/api/v1/people/{pid}", headers={"X-API-Key": api_key})
    etag1 = r1.headers["etag"]

    await db.execute("SELECT pg_sleep(0.001)")
    tmp_id = generate_id()
    await db.execute(
        "INSERT INTO person_names (id, person_id, name, name_type, is_canonical, visibility)"
        " VALUES ($1,$2,'Temp Public Name','alias',FALSE,'public')",
        tmp_id,
        pid,
    )

    r2 = client.get(f"/api/v1/people/{pid}", headers={"X-API-Key": api_key})
    assert r2.headers["etag"] != etag1

    await db.execute("DELETE FROM person_names WHERE id=$1", tmp_id)


@pytest.mark.integration
async def test_get_person_etag_changes_after_identifier_added(client, api_key, person_fixture, db):
    """Touch-parent trigger: adding an identifier bumps the person's updated_at."""
    pid = person_fixture["person_id"]
    r1 = client.get(f"/api/v1/people/{pid}", headers={"X-API-Key": api_key})
    etag1 = r1.headers["etag"]

    await db.execute("SELECT pg_sleep(0.001)")
    tmp_id = generate_id()
    await db.execute(
        "INSERT INTO identifiers (id, entity_id, entity_identifier_type_id, value)"
        " VALUES ($1,$2,$3,'PDC-TMP-001')",
        tmp_id,
        pid,
        person_fixture["eid_type_id"],
    )

    r2 = client.get(f"/api/v1/people/{pid}", headers={"X-API-Key": api_key})
    assert r2.headers["etag"] != etag1

    await db.execute("DELETE FROM identifiers WHERE id=$1", tmp_id)


@pytest.mark.integration
async def test_get_person_etag_changes_after_event_inserted(client, api_key, person_fixture, db):
    """Touch-parent trigger: inserting an entity_event bumps the person's updated_at."""
    pid = person_fixture["person_id"]
    birth_id = await db.fetchval("SELECT id FROM entity_event_types WHERE slug='birth'")
    assert birth_id is not None, "entity_event_types seed missing"
    r1 = client.get(f"/api/v1/people/{pid}", headers={"X-API-Key": api_key})
    etag1 = r1.headers["etag"]

    await db.execute("SELECT pg_sleep(0.001)")
    ev_id = generate_id()
    await db.execute(
        "INSERT INTO entity_events (id, entity_type, entity_id, event_type_id, event_year)"
        " VALUES ($1,'person',$2,$3,1970)",
        ev_id,
        pid,
        birth_id,
    )
    try:
        r2 = client.get(f"/api/v1/people/{pid}", headers={"X-API-Key": api_key})
        assert r2.headers["etag"] != etag1
    finally:
        await db.execute("DELETE FROM entity_events WHERE id=$1", ev_id)


@pytest.mark.integration
async def test_get_person_etag_changes_after_event_updated(client, api_key, person_fixture, db):
    """Touch-parent trigger: updating an entity_event bumps the person's updated_at."""
    pid = person_fixture["person_id"]
    birth_id = await db.fetchval("SELECT id FROM entity_event_types WHERE slug='birth'")
    assert birth_id is not None, "entity_event_types seed missing"
    ev_id = generate_id()
    await db.execute(
        "INSERT INTO entity_events (id, entity_type, entity_id, event_type_id, event_year)"
        " VALUES ($1,'person',$2,$3,1970)",
        ev_id,
        pid,
        birth_id,
    )
    try:
        r1 = client.get(f"/api/v1/people/{pid}", headers={"X-API-Key": api_key})
        etag1 = r1.headers["etag"]

        await db.execute("SELECT pg_sleep(0.001)")
        await db.execute("UPDATE entity_events SET event_year=1971 WHERE id=$1", ev_id)

        r2 = client.get(f"/api/v1/people/{pid}", headers={"X-API-Key": api_key})
        assert r2.headers["etag"] != etag1
    finally:
        await db.execute("DELETE FROM entity_events WHERE id=$1", ev_id)


@pytest.mark.integration
async def test_get_person_etag_changes_after_event_deleted(client, api_key, person_fixture, db):
    """Touch-parent trigger: hard-deleting an entity_event bumps the person's updated_at."""
    pid = person_fixture["person_id"]
    birth_id = await db.fetchval("SELECT id FROM entity_event_types WHERE slug='birth'")
    assert birth_id is not None, "entity_event_types seed missing"
    ev_id = generate_id()
    await db.execute(
        "INSERT INTO entity_events (id, entity_type, entity_id, event_type_id, event_year)"
        " VALUES ($1,'person',$2,$3,1970)",
        ev_id,
        pid,
        birth_id,
    )

    r1 = client.get(f"/api/v1/people/{pid}", headers={"X-API-Key": api_key})
    etag1 = r1.headers["etag"]

    await db.execute("SELECT pg_sleep(0.001)")
    await db.execute("DELETE FROM entity_events WHERE id=$1", ev_id)

    r2 = client.get(f"/api/v1/people/{pid}", headers={"X-API-Key": api_key})
    assert r2.headers["etag"] != etag1
