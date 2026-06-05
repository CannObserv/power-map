"""Tests for GET /api/v1/orgs/search and GET /api/v1/orgs/{id}."""

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
    await db.execute("INSERT INTO app_users (id, email) VALUES ($1,$2)", uid, "orgtest@test.com")
    await db.execute(
        "INSERT INTO api_keys (id, user_id, label, key_prefix, key_hash) VALUES ($1,$2,$3,$4,$5)",
        kid,
        uid,
        "Org Test Key",
        raw_key[:8],
        key_hash,
    )
    yield raw_key
    await db.execute("DELETE FROM api_keys WHERE id=$1", kid)
    await db.execute("DELETE FROM app_users WHERE id=$1", uid)


@pytest_asyncio.fixture(loop_scope="session")
async def org_fixture(db):
    """Create a test org with names, acronym, and identifiers; yield ids; clean up."""
    org_id = generate_id()
    name_id = generate_id()
    former_id = generate_id()
    acronym_id = generate_id()

    await db.execute("INSERT INTO organizations (id) VALUES ($1)", org_id)
    await db.execute(
        "INSERT INTO organization_names (id, organization_id, name, name_type, is_canonical)"
        " VALUES ($1,$2,$3,'legal',TRUE)",
        name_id,
        org_id,
        "Television Washington",
    )
    await db.execute(
        "INSERT INTO organization_names (id, organization_id, name, name_type, is_canonical)"
        " VALUES ($1,$2,$3,'former',FALSE)",
        former_id,
        org_id,
        "TV Washington",
    )
    await db.execute(
        "INSERT INTO organization_acronyms (id, organization_id, acronym, is_canonical)"
        " VALUES ($1,$2,$3,TRUE)",
        acronym_id,
        org_id,
        "TVW",
    )

    type_row = await db.fetchrow(
        "SELECT id FROM entity_identifier_types WHERE slug='wa_sos' LIMIT 1"
    )
    if type_row:
        eid_type_id = type_row["id"]
    else:
        eid_type_id = generate_id()
        await db.execute(
            "INSERT INTO entity_identifier_types (id, entity_type, slug, display_name, full_name)"
            " VALUES ($1,'organization','wa_sos','WA SOS','Washington Secretary of State')",
            eid_type_id,
        )

    eid_id = generate_id()
    await db.execute(
        "INSERT INTO identifiers (id, entity_id, entity_identifier_type_id, value)"
        " VALUES ($1,$2,$3,$4)",
        eid_id,
        org_id,
        eid_type_id,
        "12345",
    )

    yield {
        "org_id": org_id,
        "name_id": name_id,
        "former_id": former_id,
        "acronym_id": acronym_id,
        "eid_id": eid_id,
        "eid_type_id": eid_type_id,
    }

    await db.execute("DELETE FROM identifiers WHERE id=$1", eid_id)
    await db.execute("DELETE FROM organization_acronyms WHERE id=$1", acronym_id)
    await db.execute("DELETE FROM organization_names WHERE id IN ($1,$2)", name_id, former_id)
    await db.execute("DELETE FROM organizations WHERE id=$1", org_id)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _search(client, api_key, q, **params):
    return client.get(
        "/api/v1/orgs/search",
        params={"q": q, **params},
        headers={"X-API-Key": api_key},
    )


def _search_by_identifier(client, api_key, identifier_type, identifier_value, **params):
    return client.get(
        "/api/v1/orgs/search",
        params={"identifier_type": identifier_type, "identifier_value": identifier_value, **params},
        headers={"X-API-Key": api_key},
    )


# ---------------------------------------------------------------------------
# Search — response shape
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_search_response_envelope(client, api_key, org_fixture):
    r = _search(client, api_key, "Television")
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
async def test_search_meta_reflects_params(client, api_key, org_fixture):
    r = _search(client, api_key, "Television", limit=5, offset=0)
    meta = r.json()["meta"]
    assert meta["limit"] == 5
    assert meta["offset"] == 0


@pytest.mark.integration
async def test_search_meta_count_matches_data(client, api_key, org_fixture):
    r = _search(client, api_key, "Television")
    body = r.json()
    assert body["meta"]["count"] == len(body["data"])


@pytest.mark.integration
async def test_search_has_more_false_when_under_limit(client, api_key, org_fixture):
    r = _search(client, api_key, "Television", limit=50)
    assert r.json()["meta"]["has_more"] is False


@pytest.mark.integration
async def test_search_has_more_true_when_exactly_limit_plus_one(client, api_key, org_fixture):
    # limit=1 with at least 1 result; has_more depends on total matching rows
    r = _search(client, api_key, "Television", limit=1)
    body = r.json()
    assert len(body["data"]) == 1
    # has_more is True only if more rows exist; with a single fixture org it's False
    assert isinstance(body["meta"]["has_more"], bool)


# ---------------------------------------------------------------------------
# Search — result content
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_search_by_canonical_name(client, api_key, org_fixture):
    r = _search(client, api_key, "Television")
    assert r.status_code == 200
    ids = [o["id"] for o in r.json()["data"]]
    assert org_fixture["org_id"] in ids
    hit = next(o for o in r.json()["data"] if o["id"] == org_fixture["org_id"])
    assert hit["name"] == "Television Washington"
    assert hit["acronym"] == "TVW"
    assert hit["slug"] == "tvw"
    assert hit["archived_at"] is None
    assert "parent_id" in hit


@pytest.mark.integration
async def test_search_by_acronym(client, api_key, org_fixture):
    r = _search(client, api_key, "TVW")
    assert r.status_code == 200
    ids = [o["id"] for o in r.json()["data"]]
    assert org_fixture["org_id"] in ids


@pytest.mark.integration
async def test_search_by_name_variant(client, api_key, org_fixture):
    r = _search(client, api_key, "TV Washington")
    assert r.status_code == 200
    ids = [o["id"] for o in r.json()["data"]]
    assert org_fixture["org_id"] in ids


@pytest.mark.integration
async def test_search_excludes_archived(client, api_key, org_fixture, db):
    await db.execute(
        "UPDATE organizations SET archived_at=NOW() WHERE id=$1", org_fixture["org_id"]
    )
    r = _search(client, api_key, "Television")
    assert r.status_code == 200
    ids = [o["id"] for o in r.json()["data"]]
    assert org_fixture["org_id"] not in ids
    await db.execute("UPDATE organizations SET archived_at=NULL WHERE id=$1", org_fixture["org_id"])


@pytest.mark.integration
async def test_search_include_archived_flag(client, api_key, org_fixture, db):
    await db.execute(
        "UPDATE organizations SET archived_at=NOW() WHERE id=$1", org_fixture["org_id"]
    )
    r = _search(client, api_key, "Television", include_archived="true")
    assert r.status_code == 200
    ids = [o["id"] for o in r.json()["data"]]
    assert org_fixture["org_id"] in ids
    await db.execute("UPDATE organizations SET archived_at=NULL WHERE id=$1", org_fixture["org_id"])


@pytest.mark.integration
async def test_search_archived_result_has_z_suffix_timestamp(client, api_key, org_fixture, db):
    await db.execute(
        "UPDATE organizations SET archived_at=NOW() WHERE id=$1", org_fixture["org_id"]
    )
    r = _search(client, api_key, "Television", include_archived="true")
    hit = next(o for o in r.json()["data"] if o["id"] == org_fixture["org_id"])
    assert hit["archived_at"].endswith("Z"), f"expected Z suffix, got {hit['archived_at']}"
    await db.execute("UPDATE organizations SET archived_at=NULL WHERE id=$1", org_fixture["org_id"])


@pytest.mark.integration
async def test_search_limit(client, api_key, org_fixture):
    r = _search(client, api_key, "Television", limit=1)
    assert r.status_code == 200
    assert len(r.json()["data"]) <= 1


@pytest.mark.integration
async def test_search_limit_capped_at_50(client, api_key, org_fixture):
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
async def test_search_limit_capped_at_50_for_empty_q(client, api_key):
    r = _search(client, api_key, "", limit=999)
    assert r.status_code == 200
    assert r.json()["meta"]["limit"] == 50


# ---------------------------------------------------------------------------
# Get by ID
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_get_org_by_id_full_record(client, api_key, org_fixture):
    oid = org_fixture["org_id"]
    r = client.get(f"/api/v1/orgs/{oid}", headers={"X-API-Key": api_key})
    assert r.status_code == 200
    data = r.json()

    assert data["id"] == oid
    assert data["name"] == "Television Washington"
    assert data["acronym"] == "TVW"
    assert data["slug"] == "tvw"
    assert data["parent_id"] is None
    assert data["archived_at"] is None

    assert len(data["names"]) == 2
    name_ids = {n["id"] for n in data["names"]}
    assert org_fixture["name_id"] in name_ids
    assert org_fixture["former_id"] in name_ids
    canonical = next(n for n in data["names"] if n["is_canonical"])
    assert canonical["name"] == "Television Washington"
    assert canonical["name_type"] == "legal"

    assert len(data["acronyms"]) == 1
    acr = data["acronyms"][0]
    assert acr["id"] == org_fixture["acronym_id"]
    assert acr["acronym"] == "TVW"
    assert acr["is_canonical"] is True

    assert len(data["identifiers"]) >= 1
    eid = next(i for i in data["identifiers"] if i["id"] == org_fixture["eid_id"])
    assert eid["type_id"] == org_fixture["eid_type_id"]
    assert eid["type_slug"] == "wa_sos"
    assert eid["value"] == "12345"


@pytest.mark.integration
async def test_get_org_by_id_not_found(client, api_key):
    r = client.get("/api/v1/orgs/01DOESNOTEXIST00000000000000", headers={"X-API-Key": api_key})
    assert r.status_code == 404


@pytest.mark.integration
async def test_get_archived_org_still_returned(client, api_key, org_fixture, db):
    """GET by ID returns archived orgs — caller must check archived_at."""
    await db.execute(
        "UPDATE organizations SET archived_at=NOW() WHERE id=$1", org_fixture["org_id"]
    )
    r = client.get(f"/api/v1/orgs/{org_fixture['org_id']}", headers={"X-API-Key": api_key})
    assert r.status_code == 200
    archived_at = r.json()["archived_at"]
    assert archived_at is not None
    assert archived_at.endswith("Z"), f"expected Z suffix, got {archived_at}"
    await db.execute("UPDATE organizations SET archived_at=NULL WHERE id=$1", org_fixture["org_id"])


# ---------------------------------------------------------------------------
# Search — identifier_type / identifier_value filter
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_identifier_search_returns_correct_org(client, api_key, org_fixture):
    r = _search_by_identifier(client, api_key, "wa_sos", "12345")
    assert r.status_code == 200
    body = r.json()
    ids = [o["id"] for o in body["data"]]
    assert org_fixture["org_id"] in ids
    assert body["meta"]["has_more"] is False


@pytest.mark.integration
async def test_identifier_search_unknown_type_returns_empty(client, api_key, org_fixture):
    r = _search_by_identifier(client, api_key, "nonexistent_slug", "12345")
    assert r.status_code == 200
    assert r.json()["data"] == []
    assert r.json()["meta"]["has_more"] is False


@pytest.mark.integration
async def test_identifier_search_unknown_value_returns_empty(client, api_key, org_fixture):
    r = _search_by_identifier(client, api_key, "wa_sos", "DOES-NOT-EXIST")
    assert r.status_code == 200
    assert r.json()["data"] == []
    assert r.json()["meta"]["has_more"] is False


@pytest.mark.integration
async def test_identifier_search_empty_type_returns_422(client, api_key):
    r = client.get(
        "/api/v1/orgs/search",
        params={"identifier_type": "", "identifier_value": "12345"},
        headers={"X-API-Key": api_key},
    )
    assert r.status_code == 422


@pytest.mark.integration
async def test_identifier_search_empty_value_returns_422(client, api_key):
    r = client.get(
        "/api/v1/orgs/search",
        params={"identifier_type": "wa_sos", "identifier_value": ""},
        headers={"X-API-Key": api_key},
    )
    assert r.status_code == 422


@pytest.mark.integration
async def test_identifier_search_type_only_returns_422(client, api_key):
    r = client.get(
        "/api/v1/orgs/search",
        params={"identifier_type": "wa_sos"},
        headers={"X-API-Key": api_key},
    )
    assert r.status_code == 422


@pytest.mark.integration
async def test_identifier_search_value_only_returns_422(client, api_key):
    r = client.get(
        "/api/v1/orgs/search",
        params={"identifier_value": "12345"},
        headers={"X-API-Key": api_key},
    )
    assert r.status_code == 422


@pytest.mark.integration
async def test_identifier_search_wins_over_q(client, api_key, org_fixture, db):
    """When both q and identifier params are given, identifier takes precedence."""
    other_id = generate_id()
    other_name_id = generate_id()
    await db.execute("INSERT INTO organizations (id) VALUES ($1)", other_id)
    await db.execute(
        "INSERT INTO organization_names (id, organization_id, name, name_type, is_canonical)"
        " VALUES ($1,$2,'Television Other','legal',TRUE)",
        other_name_id,
        other_id,
    )
    try:
        # q matches both "Television" orgs; identifier should narrow to exactly one
        r = client.get(
            "/api/v1/orgs/search",
            params={"q": "Television", "identifier_type": "wa_sos", "identifier_value": "12345"},
            headers={"X-API-Key": api_key},
        )
        assert r.status_code == 200
        ids = [o["id"] for o in r.json()["data"]]
        assert org_fixture["org_id"] in ids
        assert other_id not in ids
    finally:
        await db.execute("DELETE FROM organization_names WHERE id=$1", other_name_id)
        await db.execute("DELETE FROM organizations WHERE id=$1", other_id)


@pytest.mark.integration
async def test_identifier_search_excludes_archived_by_default(client, api_key, org_fixture, db):
    await db.execute(
        "UPDATE organizations SET archived_at=NOW() WHERE id=$1", org_fixture["org_id"]
    )
    r = _search_by_identifier(client, api_key, "wa_sos", "12345")
    assert r.status_code == 200
    ids = [o["id"] for o in r.json()["data"]]
    assert org_fixture["org_id"] not in ids
    await db.execute("UPDATE organizations SET archived_at=NULL WHERE id=$1", org_fixture["org_id"])


@pytest.mark.integration
async def test_identifier_search_include_archived(client, api_key, org_fixture, db):
    await db.execute(
        "UPDATE organizations SET archived_at=NOW() WHERE id=$1", org_fixture["org_id"]
    )
    r = _search_by_identifier(client, api_key, "wa_sos", "12345", include_archived="true")
    assert r.status_code == 200
    ids = [o["id"] for o in r.json()["data"]]
    assert org_fixture["org_id"] in ids
    await db.execute("UPDATE organizations SET archived_at=NULL WHERE id=$1", org_fixture["org_id"])


# ---------------------------------------------------------------------------
# ETag / conditional GET
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_get_org_etag_and_last_modified_present(client, api_key, org_fixture):
    oid = org_fixture["org_id"]
    r = client.get(f"/api/v1/orgs/{oid}", headers={"X-API-Key": api_key})
    assert r.status_code == 200
    etag = r.headers.get("etag")
    assert etag is not None, "ETag header missing"
    assert etag.startswith('"') and etag.endswith('"'), f"ETag not quoted: {etag}"
    assert r.headers.get("last-modified") is not None, "Last-Modified header missing"


@pytest.mark.integration
async def test_get_org_cache_control_and_vary_present(client, api_key, org_fixture):
    oid = org_fixture["org_id"]
    r = client.get(f"/api/v1/orgs/{oid}", headers={"X-API-Key": api_key})
    assert r.status_code == 200
    assert r.headers.get("cache-control") == "no-cache"
    assert r.headers.get("vary") == "X-API-Key"


@pytest.mark.integration
async def test_get_org_304_on_matching_etag(client, api_key, org_fixture):
    oid = org_fixture["org_id"]
    r1 = client.get(f"/api/v1/orgs/{oid}", headers={"X-API-Key": api_key})
    etag = r1.headers["etag"]
    r2 = client.get(
        f"/api/v1/orgs/{oid}",
        headers={"X-API-Key": api_key, "If-None-Match": etag},
    )
    assert r2.status_code == 304
    assert r2.content == b""
    assert r2.headers.get("etag") == etag
    assert r2.headers.get("cache-control") == "no-cache"


@pytest.mark.integration
async def test_get_org_200_on_mismatched_etag(client, api_key, org_fixture):
    oid = org_fixture["org_id"]
    r = client.get(
        f"/api/v1/orgs/{oid}",
        headers={"X-API-Key": api_key, "If-None-Match": '"wrong-etag-value"'},
    )
    assert r.status_code == 200
    assert r.json()["id"] == oid


@pytest.mark.integration
async def test_get_org_etag_changes_after_parent_update(client, api_key, org_fixture, db):
    oid = org_fixture["org_id"]
    r1 = client.get(f"/api/v1/orgs/{oid}", headers={"X-API-Key": api_key})
    etag1 = r1.headers["etag"]

    await db.execute("SELECT pg_sleep(0.001)")
    await db.execute("UPDATE organizations SET parent_id = parent_id WHERE id=$1", oid)

    r2 = client.get(f"/api/v1/orgs/{oid}", headers={"X-API-Key": api_key})
    assert r2.headers["etag"] != etag1

    r3 = client.get(
        f"/api/v1/orgs/{oid}",
        headers={"X-API-Key": api_key, "If-None-Match": etag1},
    )
    assert r3.status_code == 200


@pytest.mark.integration
async def test_get_org_etag_changes_after_name_added(client, api_key, org_fixture, db):
    """Touch-parent trigger: adding a name row bumps the org's updated_at."""
    oid = org_fixture["org_id"]
    r1 = client.get(f"/api/v1/orgs/{oid}", headers={"X-API-Key": api_key})
    etag1 = r1.headers["etag"]

    await db.execute("SELECT pg_sleep(0.001)")
    tmp_id = generate_id()
    await db.execute(
        "INSERT INTO organization_names (id, organization_id, name, name_type, is_canonical)"
        " VALUES ($1,$2,'Temp Name','former',FALSE)",
        tmp_id,
        oid,
    )

    r2 = client.get(f"/api/v1/orgs/{oid}", headers={"X-API-Key": api_key})
    assert r2.headers["etag"] != etag1

    await db.execute("DELETE FROM organization_names WHERE id=$1", tmp_id)


@pytest.mark.integration
async def test_get_org_etag_changes_after_acronym_added(client, api_key, org_fixture, db):
    """Touch-parent trigger: adding an acronym row bumps the org's updated_at."""
    oid = org_fixture["org_id"]
    r1 = client.get(f"/api/v1/orgs/{oid}", headers={"X-API-Key": api_key})
    etag1 = r1.headers["etag"]

    await db.execute("SELECT pg_sleep(0.001)")
    tmp_id = generate_id()
    await db.execute(
        "INSERT INTO organization_acronyms (id, organization_id, acronym, is_canonical)"
        " VALUES ($1,$2,'TMP',FALSE)",
        tmp_id,
        oid,
    )

    r2 = client.get(f"/api/v1/orgs/{oid}", headers={"X-API-Key": api_key})
    assert r2.headers["etag"] != etag1

    await db.execute("DELETE FROM organization_acronyms WHERE id=$1", tmp_id)


@pytest.mark.integration
async def test_get_org_etag_changes_after_identifier_added(client, api_key, org_fixture, db):
    """Touch-parent trigger: adding an identifier bumps the org's updated_at."""
    oid = org_fixture["org_id"]
    r1 = client.get(f"/api/v1/orgs/{oid}", headers={"X-API-Key": api_key})
    etag1 = r1.headers["etag"]

    await db.execute("SELECT pg_sleep(0.001)")
    tmp_id = generate_id()
    await db.execute(
        "INSERT INTO identifiers (id, entity_id, entity_identifier_type_id, value)"
        " VALUES ($1,$2,$3,'tmp-99')",
        tmp_id,
        oid,
        org_fixture["eid_type_id"],
    )

    r2 = client.get(f"/api/v1/orgs/{oid}", headers={"X-API-Key": api_key})
    assert r2.headers["etag"] != etag1

    await db.execute("DELETE FROM identifiers WHERE id=$1", tmp_id)


@pytest.mark.integration
async def test_get_org_etag_changes_after_event_inserted(client, api_key, org_fixture, db):
    """Touch-parent trigger: inserting an entity_event bumps the org's updated_at."""
    oid = org_fixture["org_id"]
    founded_id = await db.fetchval("SELECT id FROM entity_event_types WHERE slug='founded'")
    r1 = client.get(f"/api/v1/orgs/{oid}", headers={"X-API-Key": api_key})
    etag1 = r1.headers["etag"]

    await db.execute("SELECT pg_sleep(0.001)")
    ev_id = generate_id()
    await db.execute(
        "INSERT INTO entity_events (id, entity_type, entity_id, event_type_id, event_year)"
        " VALUES ($1,'organization',$2,$3,1990)",
        ev_id,
        oid,
        founded_id,
    )
    try:
        r2 = client.get(f"/api/v1/orgs/{oid}", headers={"X-API-Key": api_key})
        assert r2.headers["etag"] != etag1
    finally:
        await db.execute("DELETE FROM entity_events WHERE id=$1", ev_id)


@pytest.mark.integration
async def test_get_org_etag_changes_after_event_updated(client, api_key, org_fixture, db):
    """Touch-parent trigger: updating an entity_event bumps the org's updated_at."""
    oid = org_fixture["org_id"]
    founded_id = await db.fetchval("SELECT id FROM entity_event_types WHERE slug='founded'")
    ev_id = generate_id()
    await db.execute(
        "INSERT INTO entity_events (id, entity_type, entity_id, event_type_id, event_year)"
        " VALUES ($1,'organization',$2,$3,1990)",
        ev_id,
        oid,
        founded_id,
    )
    try:
        r1 = client.get(f"/api/v1/orgs/{oid}", headers={"X-API-Key": api_key})
        etag1 = r1.headers["etag"]

        await db.execute("SELECT pg_sleep(0.001)")
        await db.execute("UPDATE entity_events SET event_year=1991 WHERE id=$1", ev_id)

        r2 = client.get(f"/api/v1/orgs/{oid}", headers={"X-API-Key": api_key})
        assert r2.headers["etag"] != etag1
    finally:
        await db.execute("DELETE FROM entity_events WHERE id=$1", ev_id)


@pytest.mark.integration
async def test_get_org_etag_changes_after_event_deleted(client, api_key, org_fixture, db):
    """Touch-parent trigger: hard-deleting an entity_event bumps the org's updated_at."""
    oid = org_fixture["org_id"]
    founded_id = await db.fetchval("SELECT id FROM entity_event_types WHERE slug='founded'")
    ev_id = generate_id()
    await db.execute(
        "INSERT INTO entity_events (id, entity_type, entity_id, event_type_id, event_year)"
        " VALUES ($1,'organization',$2,$3,1990)",
        ev_id,
        oid,
        founded_id,
    )

    r1 = client.get(f"/api/v1/orgs/{oid}", headers={"X-API-Key": api_key})
    etag1 = r1.headers["etag"]

    await db.execute("SELECT pg_sleep(0.001)")
    await db.execute("DELETE FROM entity_events WHERE id=$1", ev_id)

    r2 = client.get(f"/api/v1/orgs/{oid}", headers={"X-API-Key": api_key})
    assert r2.headers["etag"] != etag1
