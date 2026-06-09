"""Integration tests for GET /api/v1/roles and GET /api/v1/roles/{id}."""

import hashlib
import os

import pytest
import pytest_asyncio

from src.core.db import generate_id

pytestmark = [
    pytest.mark.integration,
]

_LIST = "/api/v1/roles"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture(loop_scope="session")
async def api_key(db):
    uid = generate_id()
    kid = generate_id()
    raw_key = "pm_" + os.urandom(16).hex()
    key_hash = hashlib.sha256(raw_key.encode()).hexdigest()
    await db.execute("INSERT INTO app_users (id, email) VALUES ($1,$2)", uid, "roles_read@test.com")
    await db.execute(
        "INSERT INTO api_keys (id, user_id, label, key_prefix, key_hash) VALUES ($1,$2,$3,$4,$5)",
        kid,
        uid,
        "Roles Read Key",
        raw_key[:8],
        key_hash,
    )
    yield raw_key
    await db.execute("DELETE FROM api_keys WHERE id=$1", kid)
    await db.execute("DELETE FROM app_users WHERE id=$1", uid)


@pytest_asyncio.fixture(loop_scope="session")
async def role_fixtures(db, link_type):
    """Seed one org with two active roles and one archived role.

    r1 has a seeded link, contact method, and address for detail-shape tests.
    """
    org_id = generate_id()
    await db.execute("INSERT INTO organizations (id) VALUES ($1)", org_id)

    r1 = generate_id()
    r2 = generate_id()
    r3 = generate_id()  # archived

    await db.execute(
        "INSERT INTO roles (id, organization_id, title, notes, established_on)"
        " VALUES ($1,$2,$3,$4,'2020-01-01')",
        r1,
        org_id,
        "Chair",
        "Active chair role",
    )
    await db.execute(
        "INSERT INTO roles (id, organization_id, title) VALUES ($1,$2,$3)",
        r2,
        org_id,
        "Vice Chair",
    )
    await db.execute(
        "INSERT INTO roles (id, organization_id, title, archived_at) VALUES ($1,$2,$3,NOW())",
        r3,
        org_id,
        "Treasurer",
    )

    # Seed related data on r1 for detail-shape assertions.
    link_id = generate_id()
    await db.execute(
        "INSERT INTO links (id, entity_type, entity_id, url, link_type_id)"
        " VALUES ($1,'role',$2,'https://chair.example.com',$3)",
        link_id,
        r1,
        link_type,
    )
    cm_id = generate_id()
    await db.execute(
        "INSERT INTO contact_methods (id, entity_type, entity_id, contact_type, value)"
        " VALUES ($1,'role',$2,'email','chair@example.com')",
        cm_id,
        r1,
    )
    addr_id = generate_id()
    ea_id = generate_id()
    await db.execute(
        "INSERT INTO addresses (id, raw_input, country) VALUES ($1,'1 Chair Lane','US')",
        addr_id,
    )
    await db.execute(
        "INSERT INTO entity_addresses (id, entity_type, entity_id, address_id, address_type)"
        " VALUES ($1,'role',$2,$3,'physical')",
        ea_id,
        r1,
        addr_id,
    )

    yield {
        "org_id": org_id,
        "r1": r1,
        "r2": r2,
        "r3_archived": r3,
        "r1_link_url": "https://chair.example.com",
        "r1_cm_type": "email",
        "r1_addr_type": "physical",
    }

    await db.execute("DELETE FROM entity_addresses WHERE entity_id=$1", r1)
    await db.execute("DELETE FROM addresses WHERE id=$1", addr_id)
    await db.execute("DELETE FROM contact_methods WHERE entity_id=$1 AND entity_type='role'", r1)
    await db.execute("DELETE FROM links WHERE entity_id=$1 AND entity_type='role'", r1)
    await db.execute("DELETE FROM roles WHERE organization_id=$1", org_id)
    await db.execute("DELETE FROM organizations WHERE id=$1", org_id)


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------


def test_list_requires_api_key(client):
    r = client.get(_LIST)
    assert r.status_code == 403


def test_detail_requires_api_key(client):
    r = client.get(f"{_LIST}/{generate_id()}")
    assert r.status_code == 403


# ---------------------------------------------------------------------------
# List
# ---------------------------------------------------------------------------


async def test_list_returns_paginated_roles(client, api_key, role_fixtures):
    r = client.get(
        _LIST,
        params={"organization_id": role_fixtures["org_id"]},
        headers={"X-API-Key": api_key},
    )
    assert r.status_code == 200
    body = r.json()
    assert "data" in body
    assert "meta" in body
    ids = {item["id"] for item in body["data"]}
    assert role_fixtures["r1"] in ids
    assert role_fixtures["r2"] in ids
    # archived excluded by default
    assert role_fixtures["r3_archived"] not in ids


async def test_list_shape(client, api_key, role_fixtures):
    r = client.get(
        _LIST,
        params={"organization_id": role_fixtures["org_id"]},
        headers={"X-API-Key": api_key},
    )
    item = next(i for i in r.json()["data"] if i["id"] == role_fixtures["r1"])
    assert item["organization_id"] == role_fixtures["org_id"]
    assert item["title"] == "Chair"
    assert item["established_on"] == "2020-01-01"
    assert item["archived_at"] is None


async def test_list_filter_by_organization_id(client, api_key, role_fixtures):
    r = client.get(
        _LIST,
        params={"organization_id": role_fixtures["org_id"]},
        headers={"X-API-Key": api_key},
    )
    assert r.status_code == 200
    data = r.json()["data"]
    assert len(data) == 2
    assert all(item["organization_id"] == role_fixtures["org_id"] for item in data)


async def test_list_include_archived(client, api_key, role_fixtures):
    r = client.get(
        _LIST,
        params={"organization_id": role_fixtures["org_id"], "include_archived": "true"},
        headers={"X-API-Key": api_key},
    )
    ids = {item["id"] for item in r.json()["data"]}
    assert role_fixtures["r3_archived"] in ids


async def test_list_pagination_meta(client, api_key, role_fixtures):
    r = client.get(
        _LIST,
        params={"organization_id": role_fixtures["org_id"], "limit": 1},
        headers={"X-API-Key": api_key},
    )
    body = r.json()
    assert body["meta"]["limit"] == 1
    assert body["meta"]["count"] == 1
    assert body["meta"]["has_more"] is True


# ---------------------------------------------------------------------------
# Detail
# ---------------------------------------------------------------------------


async def test_detail_returns_role(client, api_key, role_fixtures):
    rid = role_fixtures["r1"]
    r = client.get(f"{_LIST}/{rid}", headers={"X-API-Key": api_key})
    assert r.status_code == 200
    body = r.json()
    assert body["id"] == rid
    assert body["title"] == "Chair"
    assert body["organization_id"] == role_fixtures["org_id"]
    assert body["notes"] == "Active chair role"
    assert body["established_on"] == "2020-01-01"
    assert body["abolished_on"] is None


async def test_detail_404_on_unknown(client, api_key):
    r = client.get(f"{_LIST}/{generate_id()}", headers={"X-API-Key": api_key})
    assert r.status_code == 404


async def test_detail_includes_arrays(client, api_key, role_fixtures):
    """Detail response includes populated links, contact_methods, and addresses."""
    rid = role_fixtures["r1"]
    r = client.get(f"{_LIST}/{rid}", headers={"X-API-Key": api_key})
    assert r.status_code == 200
    body = r.json()

    assert len(body["links"]) == 1
    assert body["links"][0]["url"] == role_fixtures["r1_link_url"]
    assert body["links"][0]["link_type_slug"] == "website"
    assert isinstance(body["links"][0]["is_active"], bool)

    assert len(body["contact_methods"]) == 1
    assert body["contact_methods"][0]["contact_type"] == role_fixtures["r1_cm_type"]
    assert body["contact_methods"][0]["value"] == "chair@example.com"

    assert len(body["addresses"]) == 1
    assert body["addresses"][0]["address_type"] == role_fixtures["r1_addr_type"]
    assert body["addresses"][0]["raw_input"] == "1 Chair Lane"


async def test_detail_etag_304(client, api_key, role_fixtures):
    rid = role_fixtures["r1"]
    r1 = client.get(f"{_LIST}/{rid}", headers={"X-API-Key": api_key})
    assert r1.status_code == 200
    etag = r1.headers["ETag"]

    r2 = client.get(
        f"{_LIST}/{rid}",
        headers={"X-API-Key": api_key, "If-None-Match": etag},
    )
    assert r2.status_code == 304
