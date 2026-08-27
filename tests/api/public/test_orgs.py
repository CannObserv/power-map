"""Tests for GET /api/v1/orgs/search and GET /api/v1/orgs/{id}."""

import hashlib
import os
from datetime import date

import pytest
import pytest_asyncio

from src.core.db import generate_id

pytestmark = pytest.mark.integration


# This module's etag tests assert that an entity's updated_at (hence its etag)
# *advances* after a mutation. Postgres now() is fixed at transaction start, so
# the single-transaction rollback client would freeze it. Shadow db/client with
# the committing (autocommit) variants so each write is its own transaction and
# timestamps advance (#288). Rows leak but carry unique ULIDs (session-truncated).
@pytest_asyncio.fixture(loop_scope="session")
async def db(committing_db):
    return committing_db


@pytest_asyncio.fixture(loop_scope="session")
async def client(committing_client):
    return committing_client


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
    # Committing fixture (see db/client shadows above): clean up so committed
    # rows don't leak into other tests' searches.
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
    # Committing fixture: clean up committed rows to avoid cross-test leakage.
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
    r = await _search(client, api_key, "Television")
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
    r = await _search(client, api_key, "Television", limit=5, offset=0)
    meta = r.json()["meta"]
    assert meta["limit"] == 5
    assert meta["offset"] == 0


@pytest.mark.integration
async def test_search_meta_count_matches_data(client, api_key, org_fixture):
    r = await _search(client, api_key, "Television")
    body = r.json()
    assert body["meta"]["count"] == len(body["data"])


@pytest.mark.integration
async def test_search_has_more_false_when_under_limit(client, api_key, org_fixture):
    r = await _search(client, api_key, "Television", limit=50)
    assert r.json()["meta"]["has_more"] is False


@pytest.mark.integration
async def test_search_has_more_true_when_exactly_limit_plus_one(client, api_key, org_fixture):
    # limit=1 with at least 1 result; has_more depends on total matching rows
    r = await _search(client, api_key, "Television", limit=1)
    body = r.json()
    assert len(body["data"]) == 1
    # has_more is True only if more rows exist; with a single fixture org it's False
    assert isinstance(body["meta"]["has_more"], bool)


# ---------------------------------------------------------------------------
# Search — result content
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_search_by_canonical_name(client, api_key, org_fixture):
    r = await _search(client, api_key, "Television")
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
    r = await _search(client, api_key, "TVW")
    assert r.status_code == 200
    ids = [o["id"] for o in r.json()["data"]]
    assert org_fixture["org_id"] in ids


@pytest.mark.integration
async def test_search_by_name_variant(client, api_key, org_fixture):
    r = await _search(client, api_key, "TV Washington")
    assert r.status_code == 200
    ids = [o["id"] for o in r.json()["data"]]
    assert org_fixture["org_id"] in ids


@pytest.mark.integration
async def test_search_matches_partial_last_token(client, api_key, org_fixture):
    """#316: last-token prefix — 'Television Wash' matches 'Television Washington'.

    Cross-entity guard that orgs/search shares the people/search prefix behavior
    (pm_prefix_tsquery, not plainto_tsquery).
    """
    r = await _search(client, api_key, "Television Wash")
    assert r.status_code == 200
    ids = [o["id"] for o in r.json()["data"]]
    assert org_fixture["org_id"] in ids


@pytest.mark.integration
async def test_search_partial_single_token_is_prefix(client, api_key, org_fixture):
    """#316: single-token queries become prefix matches ('Televis' -> 'Television ...')."""
    r = await _search(client, api_key, "Televis")
    assert r.status_code == 200
    ids = [o["id"] for o in r.json()["data"]]
    assert org_fixture["org_id"] in ids


@pytest.mark.integration
async def test_search_excludes_archived(client, api_key, org_fixture, db):
    await db.execute(
        "UPDATE organizations SET archived_at=NOW() WHERE id=$1", org_fixture["org_id"]
    )
    r = await _search(client, api_key, "Television")
    assert r.status_code == 200
    ids = [o["id"] for o in r.json()["data"]]
    assert org_fixture["org_id"] not in ids
    await db.execute("UPDATE organizations SET archived_at=NULL WHERE id=$1", org_fixture["org_id"])


@pytest.mark.integration
async def test_search_include_archived_flag(client, api_key, org_fixture, db):
    await db.execute(
        "UPDATE organizations SET archived_at=NOW() WHERE id=$1", org_fixture["org_id"]
    )
    r = await _search(client, api_key, "Television", include_archived="true")
    assert r.status_code == 200
    ids = [o["id"] for o in r.json()["data"]]
    assert org_fixture["org_id"] in ids
    await db.execute("UPDATE organizations SET archived_at=NULL WHERE id=$1", org_fixture["org_id"])


@pytest.mark.integration
async def test_search_archived_result_has_z_suffix_timestamp(client, api_key, org_fixture, db):
    await db.execute(
        "UPDATE organizations SET archived_at=NOW() WHERE id=$1", org_fixture["org_id"]
    )
    r = await _search(client, api_key, "Television", include_archived="true")
    hit = next(o for o in r.json()["data"] if o["id"] == org_fixture["org_id"])
    assert hit["archived_at"].endswith("Z"), f"expected Z suffix, got {hit['archived_at']}"
    await db.execute("UPDATE organizations SET archived_at=NULL WHERE id=$1", org_fixture["org_id"])


@pytest.mark.integration
async def test_search_limit(client, api_key, org_fixture):
    r = await _search(client, api_key, "Television", limit=1)
    assert r.status_code == 200
    assert len(r.json()["data"]) <= 1


@pytest.mark.integration
async def test_search_limit_capped_at_50(client, api_key, org_fixture):
    r = await _search(client, api_key, "a", limit=999)
    assert r.status_code == 200
    assert r.json()["meta"]["limit"] == 50


@pytest.mark.integration
async def test_search_empty_q_returns_empty_envelope(client, api_key):
    r = await _search(client, api_key, "")
    assert r.status_code == 200
    body = r.json()
    assert body["data"] == []
    assert body["meta"]["count"] == 0
    assert body["meta"]["has_more"] is False


@pytest.mark.integration
async def test_search_limit_capped_at_50_for_empty_q(client, api_key):
    r = await _search(client, api_key, "", limit=999)
    assert r.status_code == 200
    assert r.json()["meta"]["limit"] == 50


# ---------------------------------------------------------------------------
# Get by ID
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_get_org_by_id_full_record(client, api_key, org_fixture):
    oid = org_fixture["org_id"]
    r = await client.get(f"/api/v1/orgs/{oid}", headers={"X-API-Key": api_key})
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
async def test_get_org_name_effective_dates_exposed(client, api_key, org_fixture, db):
    """Name items expose effective_start/effective_end as YYYY-MM-DD; NULL end stays null (#239)."""
    oid = org_fixture["org_id"]
    # Former name: closed interval. Canonical name: open-ended (ongoing).
    await db.execute(
        "UPDATE organization_names SET effective_start=$1, effective_end=$2 WHERE id=$3",
        date(2019, 1, 1),
        date(2023, 1, 9),
        org_fixture["former_id"],
    )
    await db.execute(
        "UPDATE organization_names SET effective_start=$1 WHERE id=$2",
        date(2023, 1, 9),
        org_fixture["name_id"],
    )
    r = await client.get(f"/api/v1/orgs/{oid}", headers={"X-API-Key": api_key})
    assert r.status_code == 200
    by_id = {n["id"]: n for n in r.json()["names"]}

    former = by_id[org_fixture["former_id"]]
    assert former["effective_start"] == "2019-01-01"
    assert former["effective_end"] == "2023-01-09"

    current = by_id[org_fixture["name_id"]]
    assert current["effective_start"] == "2023-01-09"
    assert current["effective_end"] is None


@pytest.mark.integration
async def test_get_org_by_id_not_found(client, api_key):
    r = await client.get(
        "/api/v1/orgs/01DOESNOTEXIST00000000000000", headers={"X-API-Key": api_key}
    )
    assert r.status_code == 404


@pytest.mark.integration
async def test_get_archived_org_still_returned(client, api_key, org_fixture, db):
    """GET by ID returns archived orgs — caller must check archived_at."""
    await db.execute(
        "UPDATE organizations SET archived_at=NOW() WHERE id=$1", org_fixture["org_id"]
    )
    r = await client.get(f"/api/v1/orgs/{org_fixture['org_id']}", headers={"X-API-Key": api_key})
    assert r.status_code == 200
    archived_at = r.json()["archived_at"]
    assert archived_at is not None
    assert archived_at.endswith("Z"), f"expected Z suffix, got {archived_at}"
    await db.execute("UPDATE organizations SET archived_at=NULL WHERE id=$1", org_fixture["org_id"])


@pytest.mark.integration
async def test_get_org_detail_timestamps(client, api_key, org_fixture):
    """OrgDetail must expose created_at and updated_at with Z-suffix ISO 8601."""
    oid = org_fixture["org_id"]
    r = await client.get(f"/api/v1/orgs/{oid}", headers={"X-API-Key": api_key})
    assert r.status_code == 200
    data = r.json()
    assert "created_at" in data, "created_at missing from OrgDetail"
    assert "updated_at" in data, "updated_at missing from OrgDetail"
    assert data["created_at"].endswith("Z"), f"created_at missing Z suffix: {data['created_at']}"
    assert data["updated_at"].endswith("Z"), f"updated_at missing Z suffix: {data['updated_at']}"


@pytest.mark.integration
async def test_search_orgs_does_not_expose_timestamps(client, api_key, org_fixture):
    """Search results must not include created_at or updated_at (detail-only fields)."""
    r = await _search(client, api_key, "Television")
    assert r.status_code == 200
    hit = next(o for o in r.json()["data"] if o["id"] == org_fixture["org_id"])
    assert "created_at" not in hit
    assert "updated_at" not in hit


# ---------------------------------------------------------------------------
# active flag (#240) — orgs-only domain axis, exposed on detail (read)
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_get_org_detail_exposes_active_default_true(client, api_key, org_fixture):
    """OrgDetail surfaces active; a fresh org defaults to active=True."""
    oid = org_fixture["org_id"]
    r = await client.get(f"/api/v1/orgs/{oid}", headers={"X-API-Key": api_key})
    assert r.status_code == 200
    data = r.json()
    assert "active" in data, "active missing from OrgDetail"
    assert data["active"] is True


@pytest.mark.integration
async def test_get_org_detail_reflects_inactive(client, api_key, org_fixture, db):
    """Setting active=FALSE is reflected in the detail payload."""
    oid = org_fixture["org_id"]
    await db.execute("UPDATE organizations SET active=FALSE WHERE id=$1", oid)
    try:
        r = await client.get(f"/api/v1/orgs/{oid}", headers={"X-API-Key": api_key})
        assert r.status_code == 200
        assert r.json()["active"] is False
    finally:
        await db.execute("UPDATE organizations SET active=TRUE WHERE id=$1", oid)


@pytest.mark.integration
async def test_search_orgs_does_not_expose_active(client, api_key, org_fixture):
    """active is a detail-only field; search results must not include it."""
    r = await _search(client, api_key, "Television")
    assert r.status_code == 200
    hit = next(o for o in r.json()["data"] if o["id"] == org_fixture["org_id"])
    assert "active" not in hit


# ---------------------------------------------------------------------------
# Search — identifier_type / identifier_value filter
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_identifier_search_returns_correct_org(client, api_key, org_fixture):
    r = await _search_by_identifier(client, api_key, "wa_sos", "12345")
    assert r.status_code == 200
    body = r.json()
    ids = [o["id"] for o in body["data"]]
    assert org_fixture["org_id"] in ids
    assert body["meta"]["has_more"] is False


@pytest.mark.integration
async def test_identifier_search_unknown_type_returns_empty(client, api_key, org_fixture):
    r = await _search_by_identifier(client, api_key, "nonexistent_slug", "12345")
    assert r.status_code == 200
    assert r.json()["data"] == []
    assert r.json()["meta"]["has_more"] is False


@pytest.mark.integration
async def test_identifier_search_unknown_value_returns_empty(client, api_key, org_fixture):
    r = await _search_by_identifier(client, api_key, "wa_sos", "DOES-NOT-EXIST")
    assert r.status_code == 200
    assert r.json()["data"] == []
    assert r.json()["meta"]["has_more"] is False


@pytest.mark.integration
async def test_identifier_search_empty_type_returns_422(client, api_key):
    r = await client.get(
        "/api/v1/orgs/search",
        params={"identifier_type": "", "identifier_value": "12345"},
        headers={"X-API-Key": api_key},
    )
    assert r.status_code == 422


@pytest.mark.integration
async def test_identifier_search_empty_value_returns_422(client, api_key):
    r = await client.get(
        "/api/v1/orgs/search",
        params={"identifier_type": "wa_sos", "identifier_value": ""},
        headers={"X-API-Key": api_key},
    )
    assert r.status_code == 422


@pytest.mark.integration
async def test_identifier_search_type_only_returns_422(client, api_key):
    r = await client.get(
        "/api/v1/orgs/search",
        params={"identifier_type": "wa_sos"},
        headers={"X-API-Key": api_key},
    )
    assert r.status_code == 422


@pytest.mark.integration
async def test_identifier_search_value_only_returns_422(client, api_key):
    r = await client.get(
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
        r = await client.get(
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
    r = await _search_by_identifier(client, api_key, "wa_sos", "12345")
    assert r.status_code == 200
    ids = [o["id"] for o in r.json()["data"]]
    assert org_fixture["org_id"] not in ids
    await db.execute("UPDATE organizations SET archived_at=NULL WHERE id=$1", org_fixture["org_id"])


@pytest.mark.integration
async def test_identifier_search_include_archived(client, api_key, org_fixture, db):
    await db.execute(
        "UPDATE organizations SET archived_at=NOW() WHERE id=$1", org_fixture["org_id"]
    )
    r = await _search_by_identifier(client, api_key, "wa_sos", "12345", include_archived="true")
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
    r = await client.get(f"/api/v1/orgs/{oid}", headers={"X-API-Key": api_key})
    assert r.status_code == 200
    etag = r.headers.get("etag")
    assert etag is not None, "ETag header missing"
    assert etag.startswith('"') and etag.endswith('"'), f"ETag not quoted: {etag}"
    assert r.headers.get("last-modified") is not None, "Last-Modified header missing"


@pytest.mark.integration
async def test_get_org_cache_control_and_vary_present(client, api_key, org_fixture):
    oid = org_fixture["org_id"]
    r = await client.get(f"/api/v1/orgs/{oid}", headers={"X-API-Key": api_key})
    assert r.status_code == 200
    assert r.headers.get("cache-control") == "no-cache"
    assert r.headers.get("vary") == "X-API-Key"


@pytest.mark.integration
async def test_get_org_304_on_matching_etag(client, api_key, org_fixture):
    oid = org_fixture["org_id"]
    r1 = await client.get(f"/api/v1/orgs/{oid}", headers={"X-API-Key": api_key})
    etag = r1.headers["etag"]
    r2 = await client.get(
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
    r = await client.get(
        f"/api/v1/orgs/{oid}",
        headers={"X-API-Key": api_key, "If-None-Match": '"wrong-etag-value"'},
    )
    assert r.status_code == 200
    assert r.json()["id"] == oid


@pytest.mark.integration
async def test_get_org_etag_changes_after_parent_update(client, api_key, org_fixture, db):
    oid = org_fixture["org_id"]
    r1 = await client.get(f"/api/v1/orgs/{oid}", headers={"X-API-Key": api_key})
    etag1 = r1.headers["etag"]

    await db.execute("SELECT pg_sleep(0.001)")
    await db.execute("UPDATE organizations SET parent_id = parent_id WHERE id=$1", oid)

    r2 = await client.get(f"/api/v1/orgs/{oid}", headers={"X-API-Key": api_key})
    assert r2.headers["etag"] != etag1

    r3 = await client.get(
        f"/api/v1/orgs/{oid}",
        headers={"X-API-Key": api_key, "If-None-Match": etag1},
    )
    assert r3.status_code == 200


@pytest.mark.integration
async def test_get_org_etag_changes_after_name_added(client, api_key, org_fixture, db):
    """Touch-parent trigger: adding a name row bumps the org's updated_at."""
    oid = org_fixture["org_id"]
    r1 = await client.get(f"/api/v1/orgs/{oid}", headers={"X-API-Key": api_key})
    etag1 = r1.headers["etag"]

    await db.execute("SELECT pg_sleep(0.001)")
    tmp_id = generate_id()
    await db.execute(
        "INSERT INTO organization_names (id, organization_id, name, name_type, is_canonical)"
        " VALUES ($1,$2,'Temp Name','former',FALSE)",
        tmp_id,
        oid,
    )

    r2 = await client.get(f"/api/v1/orgs/{oid}", headers={"X-API-Key": api_key})
    assert r2.headers["etag"] != etag1

    await db.execute("DELETE FROM organization_names WHERE id=$1", tmp_id)


@pytest.mark.integration
async def test_get_org_etag_changes_after_acronym_added(client, api_key, org_fixture, db):
    """Touch-parent trigger: adding an acronym row bumps the org's updated_at."""
    oid = org_fixture["org_id"]
    r1 = await client.get(f"/api/v1/orgs/{oid}", headers={"X-API-Key": api_key})
    etag1 = r1.headers["etag"]

    await db.execute("SELECT pg_sleep(0.001)")
    tmp_id = generate_id()
    await db.execute(
        "INSERT INTO organization_acronyms (id, organization_id, acronym, is_canonical)"
        " VALUES ($1,$2,'TMP',FALSE)",
        tmp_id,
        oid,
    )

    r2 = await client.get(f"/api/v1/orgs/{oid}", headers={"X-API-Key": api_key})
    assert r2.headers["etag"] != etag1

    await db.execute("DELETE FROM organization_acronyms WHERE id=$1", tmp_id)


@pytest.mark.integration
async def test_get_org_etag_changes_after_identifier_added(client, api_key, org_fixture, db):
    """Touch-parent trigger: adding an identifier bumps the org's updated_at."""
    oid = org_fixture["org_id"]
    r1 = await client.get(f"/api/v1/orgs/{oid}", headers={"X-API-Key": api_key})
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

    r2 = await client.get(f"/api/v1/orgs/{oid}", headers={"X-API-Key": api_key})
    assert r2.headers["etag"] != etag1

    await db.execute("DELETE FROM identifiers WHERE id=$1", tmp_id)


@pytest.mark.integration
async def test_get_org_etag_changes_after_event_inserted(client, api_key, org_fixture, db):
    """Touch-parent trigger: inserting an entity_event bumps the org's updated_at."""
    oid = org_fixture["org_id"]
    founded_id = await db.fetchval("SELECT id FROM entity_event_types WHERE slug='founded'")
    assert founded_id is not None, "entity_event_types seed missing"
    r1 = await client.get(f"/api/v1/orgs/{oid}", headers={"X-API-Key": api_key})
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
        r2 = await client.get(f"/api/v1/orgs/{oid}", headers={"X-API-Key": api_key})
        assert r2.headers["etag"] != etag1
    finally:
        await db.execute("DELETE FROM entity_events WHERE id=$1", ev_id)


@pytest.mark.integration
async def test_get_org_etag_changes_after_event_updated(client, api_key, org_fixture, db):
    """Touch-parent trigger: updating an entity_event bumps the org's updated_at."""
    oid = org_fixture["org_id"]
    founded_id = await db.fetchval("SELECT id FROM entity_event_types WHERE slug='founded'")
    assert founded_id is not None, "entity_event_types seed missing"
    ev_id = generate_id()
    await db.execute(
        "INSERT INTO entity_events (id, entity_type, entity_id, event_type_id, event_year)"
        " VALUES ($1,'organization',$2,$3,1990)",
        ev_id,
        oid,
        founded_id,
    )
    try:
        r1 = await client.get(f"/api/v1/orgs/{oid}", headers={"X-API-Key": api_key})
        etag1 = r1.headers["etag"]

        await db.execute("SELECT pg_sleep(0.001)")
        await db.execute("UPDATE entity_events SET event_year=1991 WHERE id=$1", ev_id)

        r2 = await client.get(f"/api/v1/orgs/{oid}", headers={"X-API-Key": api_key})
        assert r2.headers["etag"] != etag1
    finally:
        await db.execute("DELETE FROM entity_events WHERE id=$1", ev_id)


@pytest.mark.integration
async def test_get_org_etag_changes_after_event_deleted(client, api_key, org_fixture, db):
    """Touch-parent trigger: hard-deleting an entity_event bumps the org's updated_at."""
    oid = org_fixture["org_id"]
    founded_id = await db.fetchval("SELECT id FROM entity_event_types WHERE slug='founded'")
    assert founded_id is not None, "entity_event_types seed missing"
    ev_id = generate_id()
    await db.execute(
        "INSERT INTO entity_events (id, entity_type, entity_id, event_type_id, event_year)"
        " VALUES ($1,'organization',$2,$3,1990)",
        ev_id,
        oid,
        founded_id,
    )

    r1 = await client.get(f"/api/v1/orgs/{oid}", headers={"X-API-Key": api_key})
    etag1 = r1.headers["etag"]

    await db.execute("SELECT pg_sleep(0.001)")
    await db.execute("DELETE FROM entity_events WHERE id=$1", ev_id)

    r2 = await client.get(f"/api/v1/orgs/{oid}", headers={"X-API-Key": api_key})
    assert r2.headers["etag"] != etag1


# ---------------------------------------------------------------------------
# jurisdiction_affiliations on GET /orgs/{id} and GET /orgs/search?jurisdiction=
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture(loop_scope="session")
async def jur_affiliation_fixtures(db, org_fixture):
    """Seed a jurisdiction + governing affiliation for the test org."""
    jtype_id = generate_id()
    jur_id = generate_id()
    aff_id = generate_id()
    org_id = org_fixture["org_id"]

    await db.execute(
        "INSERT INTO jurisdiction_types (id, slug, display_name) VALUES ($1,$2,$3)",
        jtype_id,
        f"test-jtype-{jtype_id[:8]}",
        "State",
    )
    await db.execute(
        "INSERT INTO jurisdictions (id, slug, name, type_id) VALUES ($1,$2,$3,$4)",
        jur_id,
        f"test-jur-{jur_id[:8]}",
        "Test State",
        jtype_id,
    )
    at_row = await db.fetchrow(
        "SELECT id FROM organization_jurisdiction_affiliation_types WHERE slug='governing'"
    )
    assert at_row is not None
    await db.execute(
        "INSERT INTO organization_jurisdiction_affiliations"
        " (id, organization_id, jurisdiction_id, affiliation_type_id)"
        " VALUES ($1,$2,$3,$4)",
        aff_id,
        org_id,
        jur_id,
        at_row["id"],
    )
    yield {"org_id": org_id, "jur_id": jur_id, "jur_slug": f"test-jur-{jur_id[:8]}"}
    # Committing fixture: clean up committed rows to avoid cross-test leakage.
    await db.execute("DELETE FROM organization_jurisdiction_affiliations WHERE id=$1", aff_id)
    await db.execute("DELETE FROM jurisdictions WHERE id=$1", jur_id)
    await db.execute("DELETE FROM jurisdiction_types WHERE id=$1", jtype_id)


@pytest.mark.integration
async def test_get_org_detail_includes_jurisdiction_affiliations(
    client, api_key, jur_affiliation_fixtures
):
    org_id = jur_affiliation_fixtures["org_id"]
    jur_id = jur_affiliation_fixtures["jur_id"]

    r = await client.get(f"/api/v1/orgs/{org_id}", headers={"X-API-Key": api_key})
    assert r.status_code == 200
    body = r.json()
    assert "jurisdiction_affiliations" in body
    affs = body["jurisdiction_affiliations"]
    assert len(affs) == 1
    assert affs[0]["jurisdiction_id"] == jur_id
    assert affs[0]["affiliation_type"]["slug"] == "governing"
    assert affs[0]["affiliation_type"]["display_name"] == "is governed by"


@pytest.mark.integration
async def test_get_org_detail_no_affiliations_returns_empty_array(client, api_key, org_fixture, db):
    # Use a fresh org with no affiliations
    oid = generate_id()
    await db.execute("INSERT INTO organizations (id) VALUES ($1)", oid)
    try:
        r = await client.get(f"/api/v1/orgs/{oid}", headers={"X-API-Key": api_key})
        assert r.status_code == 200
        assert r.json()["jurisdiction_affiliations"] == []
    finally:
        await db.execute("DELETE FROM organizations WHERE id=$1", oid)


@pytest.mark.integration
async def test_search_jurisdiction_filter_by_slug(client, api_key, jur_affiliation_fixtures):
    slug = jur_affiliation_fixtures["jur_slug"]
    org_id = jur_affiliation_fixtures["org_id"]

    r = await client.get(
        "/api/v1/orgs/search",
        params={"jurisdiction": slug},
        headers={"X-API-Key": api_key},
    )
    assert r.status_code == 200
    ids = [item["id"] for item in r.json()["data"]]
    assert org_id in ids


@pytest.mark.integration
async def test_search_jurisdiction_filter_by_ulid(client, api_key, jur_affiliation_fixtures):
    jur_id = jur_affiliation_fixtures["jur_id"]
    org_id = jur_affiliation_fixtures["org_id"]

    r = await client.get(
        "/api/v1/orgs/search",
        params={"jurisdiction": jur_id},
        headers={"X-API-Key": api_key},
    )
    assert r.status_code == 200
    ids = [item["id"] for item in r.json()["data"]]
    assert org_id in ids


@pytest.mark.integration
async def test_search_jurisdiction_filter_unknown_slug_returns_empty(client, api_key):
    r = await client.get(
        "/api/v1/orgs/search",
        params={"jurisdiction": "no-such-jurisdiction"},
        headers={"X-API-Key": api_key},
    )
    assert r.status_code == 200
    assert r.json()["data"] == []


@pytest.mark.integration
async def test_search_q_with_jurisdiction_filters_by_name(
    client, api_key, jur_affiliation_fixtures, db
):
    """q narrows within jurisdiction: matching org included, non-matching org excluded."""
    slug = jur_affiliation_fixtures["jur_slug"]
    jur_id = jur_affiliation_fixtures["jur_id"]
    org_id = jur_affiliation_fixtures["org_id"]

    # Second org in the same jurisdiction — name does NOT contain "Television"
    other_id = generate_id()
    other_name_id = generate_id()
    other_aff_id = generate_id()
    at_row = await db.fetchrow(
        "SELECT id FROM organization_jurisdiction_affiliation_types WHERE slug='governing'"
    )
    await db.execute("INSERT INTO organizations (id) VALUES ($1)", other_id)
    await db.execute(
        "INSERT INTO organization_names (id, organization_id, name, name_type, is_canonical)"
        " VALUES ($1,$2,'Council of Agriculture','legal',TRUE)",
        other_name_id,
        other_id,
    )
    await db.execute(
        "INSERT INTO organization_jurisdiction_affiliations"
        " (id, organization_id, jurisdiction_id, affiliation_type_id)"
        " VALUES ($1,$2,$3,$4)",
        other_aff_id,
        other_id,
        jur_id,
        at_row["id"],
    )
    try:
        r = await client.get(
            "/api/v1/orgs/search",
            params={"q": "Television", "jurisdiction": slug},
            headers={"X-API-Key": api_key},
        )
        assert r.status_code == 200
        ids = [item["id"] for item in r.json()["data"]]
        assert org_id in ids
        assert other_id not in ids
    finally:
        await db.execute(
            "DELETE FROM organization_jurisdiction_affiliations WHERE id=$1", other_aff_id
        )
        await db.execute("DELETE FROM organization_names WHERE id=$1", other_name_id)
        await db.execute("DELETE FROM organizations WHERE id=$1", other_id)


@pytest.mark.integration
async def test_search_q_with_jurisdiction_excludes_nonmatching_name(
    client, api_key, jur_affiliation_fixtures
):
    """q + jurisdiction: a q that matches no name in the jurisdiction returns an empty result."""
    slug = jur_affiliation_fixtures["jur_slug"]

    r = await client.get(
        "/api/v1/orgs/search",
        params={"q": "zzznotreal", "jurisdiction": slug},
        headers={"X-API-Key": api_key},
    )
    assert r.status_code == 200
    assert r.json()["data"] == []


@pytest.mark.integration
async def test_search_jurisdiction_with_empty_q_returns_affiliation_scoped(
    client, api_key, jur_affiliation_fixtures
):
    """Empty q + jurisdiction: all jurisdiction-scoped orgs returned (regression guard)."""
    slug = jur_affiliation_fixtures["jur_slug"]
    org_id = jur_affiliation_fixtures["org_id"]

    r = await client.get(
        "/api/v1/orgs/search",
        params={"q": "", "jurisdiction": slug},
        headers={"X-API-Key": api_key},
    )
    assert r.status_code == 200
    ids = [item["id"] for item in r.json()["data"]]
    assert org_id in ids


@pytest.mark.integration
async def test_search_jurisdiction_filter_registered_type_not_default(
    client, api_key, db, org_fixture
):
    """?jurisdiction= defaults to governing type — registered affiliation must NOT match."""
    jtype_id = generate_id()
    jur_id = generate_id()
    aff_id = generate_id()
    org_id = org_fixture["org_id"]

    await db.execute(
        "INSERT INTO jurisdiction_types (id, slug, display_name) VALUES ($1,$2,$3)",
        jtype_id,
        f"test-jtype-{jtype_id[:8]}",
        "State",
    )
    jur_slug = f"test-reg-{jur_id[:8]}"
    await db.execute(
        "INSERT INTO jurisdictions (id, slug, name, type_id) VALUES ($1,$2,$3,$4)",
        jur_id,
        jur_slug,
        "Registered State",
        jtype_id,
    )
    at_row = await db.fetchrow(
        "SELECT id FROM organization_jurisdiction_affiliation_types WHERE slug='registered'"
    )
    assert at_row is not None
    await db.execute(
        "INSERT INTO organization_jurisdiction_affiliations"
        " (id, organization_id, jurisdiction_id, affiliation_type_id)"
        " VALUES ($1,$2,$3,$4)",
        aff_id,
        org_id,
        jur_id,
        at_row["id"],
    )
    try:
        r = await client.get(
            "/api/v1/orgs/search",
            params={"jurisdiction": jur_slug},
            headers={"X-API-Key": api_key},
        )
        assert r.status_code == 200
        ids = [item["id"] for item in r.json()["data"]]
        assert org_id not in ids
    finally:
        await db.execute("DELETE FROM organization_jurisdiction_affiliations WHERE id=$1", aff_id)
        await db.execute("DELETE FROM jurisdictions WHERE id=$1", jur_id)
        await db.execute("DELETE FROM jurisdiction_types WHERE id=$1", jtype_id)


# ---------------------------------------------------------------------------
# Stable pagination under tied (rank, name) sort key (#297)
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_search_pagination_stable_under_tied_rank_and_name(client, api_key, db):
    """Offset pagination is complete + duplicate-free when many orgs tie on (rank, name).

    Without a unique tiebreaker in ORDER BY, tied rows come back in a
    query-dependent order, so offset windows over the search results skip and
    duplicate orgs. Seed 12 orgs sharing one identical canonical name (identical
    ts_rank + identical name → full tie), then page the search at a small limit.
    """
    token = "Zzyzxatron"  # rare token so only these fixtures match
    org_ids = [generate_id() for _ in range(50)]
    name_ids = [generate_id() for _ in range(50)]
    try:
        for oid, nid in zip(org_ids, name_ids, strict=True):
            await db.execute("INSERT INTO organizations (id) VALUES ($1)", oid)
            await db.execute(
                "INSERT INTO organization_names"
                " (id, organization_id, name, name_type, is_canonical)"
                " VALUES ($1,$2,$3,'legal',TRUE)",
                nid,
                oid,
                token,
            )

        limit = 3
        collected: list[str] = []
        offset = 0
        while True:
            r = await client.get(
                "/api/v1/orgs/search",
                params={"q": token, "limit": limit, "offset": offset},
                headers={"X-API-Key": api_key},
            )
            assert r.status_code == 200
            body = r.json()
            collected.extend(item["id"] for item in body["data"])
            if not body["meta"]["has_more"]:
                break
            offset += limit

        # Complete and duplicate-free: every seeded org appears exactly once.
        assert len(collected) == len(org_ids)
        assert set(collected) == set(org_ids)
        # Deterministic total order: tied (rank, name) → o.id ascending.
        seeded_slice = [oid for oid in collected if oid in set(org_ids)]
        assert seeded_slice == sorted(org_ids)
    finally:
        await db.execute("DELETE FROM organization_names WHERE id = ANY($1::text[])", name_ids)
        await db.execute("DELETE FROM organizations WHERE id = ANY($1::text[])", org_ids)


# ---------------------------------------------------------------------------
# Succession annotation (#469) — lifespan {start,end} + succeeds/succeeded_by
# ---------------------------------------------------------------------------


async def _insert_org_event(db, org_id, slug, year=None, month=None, day=None, linked_id=None):
    eid = generate_id()
    await db.execute(
        """INSERT INTO entity_events
               (id, entity_type, entity_id, event_type_id,
                event_year, event_month, event_day, linked_entity_type, linked_entity_id)
           SELECT $1, 'organization', $2, t.id, $4, $5, $6,
                  CASE WHEN $7::text IS NOT NULL THEN 'organization' END, $7
           FROM entity_event_types t WHERE t.slug = $3""",
        eid,
        org_id,
        slug,
        year,
        month,
        day,
        linked_id,
    )
    return eid


@pytest_asyncio.fixture(loop_scope="session")
async def succession_pair(db):
    """Predecessor → successor orgs linked by a dated succeeded_by event."""
    pred_id = generate_id()
    succ_id = generate_id()
    name_ids = [generate_id(), generate_id()]
    await db.execute("INSERT INTO organizations (id) VALUES ($1), ($2)", pred_id, succ_id)
    await db.execute(
        "INSERT INTO organization_names (id, organization_id, name, name_type, is_canonical)"
        " VALUES ($1,$3,'Succession Chain Predecessor','legal',TRUE),"
        "        ($2,$4,'Succession Chain Successor','legal',TRUE)",
        name_ids[0],
        name_ids[1],
        pred_id,
        succ_id,
    )
    event_ids = [
        await _insert_org_event(db, pred_id, "founded", 2003, 5),
        await _insert_org_event(db, succ_id, "founded", 2021),
        await _insert_org_event(db, pred_id, "succeeded_by", 2020, linked_id=succ_id),
    ]
    yield {"pred_id": pred_id, "succ_id": succ_id}
    await db.execute("DELETE FROM entity_events WHERE id = ANY($1::text[])", event_ids)
    await db.execute("DELETE FROM organization_names WHERE id = ANY($1::text[])", name_ids)
    await db.execute("DELETE FROM organizations WHERE id IN ($1,$2)", pred_id, succ_id)


@pytest.mark.integration
async def test_detail_predecessor_annotations(client, api_key, succession_pair):
    r = await client.get(
        f"/api/v1/orgs/{succession_pair['pred_id']}", headers={"X-API-Key": api_key}
    )
    assert r.status_code == 200
    data = r.json()
    assert data["succeeded_by"] == succession_pair["succ_id"]
    assert data["succeeds"] is None
    # start: earliest-within-precision (month-only 2003-05 → first of month);
    # end: latest-within-precision from the dated succession (year-only 2020 → Dec 31).
    assert data["lifespan"] == {"start": "2003-05-01", "end": "2020-12-31"}


@pytest.mark.integration
async def test_detail_successor_annotations(client, api_key, succession_pair):
    r = await client.get(
        f"/api/v1/orgs/{succession_pair['succ_id']}", headers={"X-API-Key": api_key}
    )
    assert r.status_code == 200
    data = r.json()
    assert data["succeeds"] == succession_pair["pred_id"]
    assert data["succeeded_by"] is None
    assert data["lifespan"] == {"start": "2021-01-01", "end": None}


@pytest.mark.integration
async def test_detail_no_events_annotations_null(client, api_key, org_fixture):
    r = await client.get(f"/api/v1/orgs/{org_fixture['org_id']}", headers={"X-API-Key": api_key})
    assert r.status_code == 200
    data = r.json()
    assert data["lifespan"] == {"start": None, "end": None}
    assert data["succeeds"] is None
    assert data["succeeded_by"] is None


@pytest.mark.integration
async def test_search_rows_carry_succession_annotations(client, api_key, succession_pair):
    r = await _search(client, api_key, "Succession Chain")
    assert r.status_code == 200
    by_id = {o["id"]: o for o in r.json()["data"]}
    pred = by_id[succession_pair["pred_id"]]
    succ = by_id[succession_pair["succ_id"]]
    assert pred["succeeded_by"] == succession_pair["succ_id"]
    assert pred["lifespan"]["end"] == "2020-12-31"
    assert succ["succeeds"] == succession_pair["pred_id"]
    assert succ["succeeded_by"] is None


@pytest.mark.integration
async def test_archived_succession_event_not_annotated(client, api_key, succession_pair, db):
    eid = await _insert_org_event(
        db, succession_pair["succ_id"], "succeeded_by", linked_id=succession_pair["pred_id"]
    )
    await db.execute("UPDATE entity_events SET archived_at = NOW() WHERE id = $1", eid)
    try:
        r = await client.get(
            f"/api/v1/orgs/{succession_pair['succ_id']}", headers={"X-API-Key": api_key}
        )
        assert r.json()["succeeded_by"] is None
    finally:
        await db.execute("DELETE FROM entity_events WHERE id = $1", eid)


@pytest.mark.integration
async def test_succession_event_touches_linked_org_etag(client, api_key, db):
    """#469: the successor's ETag must advance when the predecessor gains the
    succession event — the successor's payload (`succeeds`) derives from it."""
    pred_id, succ_id = generate_id(), generate_id()
    await db.execute("INSERT INTO organizations (id) VALUES ($1), ($2)", pred_id, succ_id)
    eid = None
    try:
        r1 = await client.get(f"/api/v1/orgs/{succ_id}", headers={"X-API-Key": api_key})
        eid = await _insert_org_event(db, pred_id, "succeeded_by", 2020, linked_id=succ_id)
        r2 = await client.get(f"/api/v1/orgs/{succ_id}", headers={"X-API-Key": api_key})
        assert r2.headers["etag"] != r1.headers["etag"]
        assert r2.json()["succeeds"] == pred_id
    finally:
        if eid:
            await db.execute("DELETE FROM entity_events WHERE id = $1", eid)
        await db.execute("DELETE FROM organizations WHERE id IN ($1,$2)", pred_id, succ_id)
