"""Integration tests for GET /api/v1/orgs/search and GET /api/v1/orgs/{id}."""

import hashlib
import os

import asyncpg
import pytest
from fastapi.testclient import TestClient

from src.api.main import app
from src.core.db import apply_schema, generate_id

pytestmark = pytest.mark.integration


@pytest.fixture
async def db():
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        pytest.skip("DATABASE_URL not set")
    conn = await asyncpg.connect(dsn)
    try:
        await apply_schema(conn)
        yield conn
    finally:
        await conn.close()


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture
async def api_key(db):
    uid = generate_id()
    kid = generate_id()
    raw_key = "pm_" + os.urandom(16).hex()
    key_hash = hashlib.sha256(raw_key.encode()).hexdigest()
    await db.execute("INSERT INTO app_users (id, email) VALUES ($1,$2)", uid, "orgtest@test.com")
    await db.execute(
        "INSERT INTO api_keys (id, user_id, label, key_prefix, key_hash) VALUES ($1,$2,$3,$4,$5)",
        kid, uid, "Org Test Key", raw_key[:8], key_hash,
    )
    yield raw_key
    await db.execute("DELETE FROM api_keys WHERE id=$1", kid)
    await db.execute("DELETE FROM app_users WHERE id=$1", uid)


@pytest.fixture
async def org_fixture(db):
    """Create a test org with names, acronym, and identifiers; yield ids; clean up."""
    org_id = generate_id()
    name_id = generate_id()
    former_id = generate_id()
    acronym_id = generate_id()

    await db.execute(
        "INSERT INTO organizations (id) VALUES ($1)",
        org_id,
    )
    await db.execute(
        "INSERT INTO organization_names (id, organization_id, name, name_type, is_canonical)"
        " VALUES ($1,$2,$3,'legal',TRUE)",
        name_id, org_id, "Television Washington",
    )
    await db.execute(
        "INSERT INTO organization_names (id, organization_id, name, name_type, is_canonical)"
        " VALUES ($1,$2,$3,'former',FALSE)",
        former_id, org_id, "TV Washington",
    )
    await db.execute(
        "INSERT INTO organization_acronyms (id, organization_id, acronym, is_canonical)"
        " VALUES ($1,$2,$3,TRUE)",
        acronym_id, org_id, "TVW",
    )

    # identifier type — use an existing seed slug or insert one
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
        eid_id, org_id, eid_type_id, "12345",
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
# Auth
# ---------------------------------------------------------------------------


def test_search_missing_key_returns_403(client):
    r = client.get("/api/v1/orgs/search?q=test")
    assert r.status_code == 403


def test_search_invalid_key_returns_401(client):
    r = client.get("/api/v1/orgs/search?q=test", headers={"X-API-Key": "pm_bad"})
    assert r.status_code == 401


def test_get_org_missing_key_returns_403(client):
    r = client.get("/api/v1/orgs/someid")
    assert r.status_code == 403


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------


async def test_search_by_canonical_name(client, api_key, org_fixture):
    r = client.get("/api/v1/orgs/search?q=Television", headers={"X-API-Key": api_key})
    assert r.status_code == 200
    data = r.json()
    ids = [o["id"] for o in data]
    assert org_fixture["org_id"] in ids
    hit = next(o for o in data if o["id"] == org_fixture["org_id"])
    assert hit["name"] == "Television Washington"
    assert hit["acronym"] == "TVW"
    assert hit["slug"] == "tvw"
    assert hit["archived_at"] is None
    assert "parent_id" in hit


async def test_search_by_acronym(client, api_key, org_fixture):
    r = client.get("/api/v1/orgs/search?q=TVW", headers={"X-API-Key": api_key})
    assert r.status_code == 200
    ids = [o["id"] for o in r.json()]
    assert org_fixture["org_id"] in ids


async def test_search_by_name_variant(client, api_key, org_fixture):
    r = client.get("/api/v1/orgs/search?q=TV+Washington", headers={"X-API-Key": api_key})
    assert r.status_code == 200
    ids = [o["id"] for o in r.json()]
    assert org_fixture["org_id"] in ids


async def test_search_excludes_archived(client, api_key, org_fixture, db):
    await db.execute(
        "UPDATE organizations SET archived_at=NOW() WHERE id=$1", org_fixture["org_id"]
    )
    r = client.get("/api/v1/orgs/search?q=Television", headers={"X-API-Key": api_key})
    assert r.status_code == 200
    ids = [o["id"] for o in r.json()]
    assert org_fixture["org_id"] not in ids
    # restore
    await db.execute(
        "UPDATE organizations SET archived_at=NULL WHERE id=$1", org_fixture["org_id"]
    )


async def test_search_include_archived_flag(client, api_key, org_fixture, db):
    await db.execute(
        "UPDATE organizations SET archived_at=NOW() WHERE id=$1", org_fixture["org_id"]
    )
    r = client.get(
        "/api/v1/orgs/search?q=Television&include_archived=true",
        headers={"X-API-Key": api_key},
    )
    assert r.status_code == 200
    ids = [o["id"] for o in r.json()]
    assert org_fixture["org_id"] in ids
    # restore
    await db.execute(
        "UPDATE organizations SET archived_at=NULL WHERE id=$1", org_fixture["org_id"]
    )


async def test_search_limit(client, api_key, org_fixture):
    r = client.get("/api/v1/orgs/search?q=Television&limit=1", headers={"X-API-Key": api_key})
    assert r.status_code == 200
    assert len(r.json()) <= 1


async def test_search_limit_capped_at_50(client, api_key, org_fixture):
    r = client.get("/api/v1/orgs/search?q=a&limit=999", headers={"X-API-Key": api_key})
    assert r.status_code == 200  # capped silently, not a 422


async def test_search_empty_q_returns_empty(client, api_key):
    r = client.get("/api/v1/orgs/search?q=", headers={"X-API-Key": api_key})
    assert r.status_code == 200
    assert r.json() == []


# ---------------------------------------------------------------------------
# Get by ID
# ---------------------------------------------------------------------------


async def test_get_org_by_id_full_record(client, api_key, org_fixture):
    oid = org_fixture["org_id"]
    r = client.get(f"/api/v1/orgs/{oid}", headers={"X-API-Key": api_key})
    assert r.status_code == 200
    data = r.json()

    # top-level fields
    assert data["id"] == oid
    assert data["name"] == "Television Washington"
    assert data["acronym"] == "TVW"
    assert data["slug"] == "tvw"
    assert data["parent_id"] is None
    assert data["archived_at"] is None

    # names array — both rows, each with id
    assert len(data["names"]) == 2
    name_ids = {n["id"] for n in data["names"]}
    assert org_fixture["name_id"] in name_ids
    assert org_fixture["former_id"] in name_ids
    canonical = next(n for n in data["names"] if n["is_canonical"])
    assert canonical["name"] == "Television Washington"
    assert canonical["name_type"] == "legal"

    # acronyms array
    assert len(data["acronyms"]) == 1
    acr = data["acronyms"][0]
    assert acr["id"] == org_fixture["acronym_id"]
    assert acr["acronym"] == "TVW"
    assert acr["is_canonical"] is True

    # identifiers array
    assert len(data["identifiers"]) >= 1
    eid = next(i for i in data["identifiers"] if i["id"] == org_fixture["eid_id"])
    assert eid["type_id"] == org_fixture["eid_type_id"]
    assert eid["type_slug"] == "wa_sos"
    assert eid["value"] == "12345"


async def test_get_org_by_id_not_found(client, api_key):
    r = client.get("/api/v1/orgs/01DOESNOTEXIST00000000000000", headers={"X-API-Key": api_key})
    assert r.status_code == 404


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
    # restore
    await db.execute(
        "UPDATE organizations SET archived_at=NULL WHERE id=$1", org_fixture["org_id"]
    )
