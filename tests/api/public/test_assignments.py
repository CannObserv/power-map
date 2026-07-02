"""Integration tests for GET /api/v1/assignments and GET /api/v1/assignments/{id}."""

import hashlib
import os

import pytest
import pytest_asyncio

from src.core.db import generate_id

pytestmark = [
    pytest.mark.integration,
]

_LIST = "/api/v1/assignments"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture(loop_scope="session")
async def api_key(db):
    uid = generate_id()
    kid = generate_id()
    raw_key = "pm_" + os.urandom(16).hex()
    key_hash = hashlib.sha256(raw_key.encode()).hexdigest()
    await db.execute(
        "INSERT INTO app_users (id, email) VALUES ($1,$2)", uid, "assignments_read@test.com"
    )
    await db.execute(
        "INSERT INTO api_keys (id, user_id, label, key_prefix, key_hash) VALUES ($1,$2,$3,$4,$5)",
        kid,
        uid,
        "Assignments Read Key",
        raw_key[:8],
        key_hash,
    )
    yield raw_key
    await db.execute("DELETE FROM api_keys WHERE id=$1", kid)
    await db.execute("DELETE FROM app_users WHERE id=$1", uid)


@pytest_asyncio.fixture(loop_scope="session")
async def assignment_fixtures(db, link_type):
    """Seed person, org, roles, and assignments for read tests.

    a1 — active, with start/end dates, seeded link + contact method + address
    a2 — active, is_current=True, no dates
    a3 — archived
    All under person1 + org1 / role1 / role2.
    """
    person1 = generate_id()
    person2 = generate_id()
    org1 = generate_id()
    role1 = generate_id()
    role2 = generate_id()

    await db.execute("INSERT INTO people (id) VALUES ($1)", person1)
    await db.execute("INSERT INTO people (id) VALUES ($1)", person2)
    await db.execute("INSERT INTO organizations (id) VALUES ($1)", org1)
    await db.execute(
        "INSERT INTO roles (id, organization_id, title) VALUES ($1,$2,'Director')", role1, org1
    )
    await db.execute(
        "INSERT INTO roles (id, organization_id, title) VALUES ($1,$2,'Deputy')", role2, org1
    )

    a1 = generate_id()
    a2 = generate_id()
    a3 = generate_id()

    await db.execute(
        "INSERT INTO role_assignments (id, person_id, role_id, start_date, end_date, notes)"
        " VALUES ($1,$2,$3,'2023-01-01','2024-12-31','a1 notes')",
        a1,
        person1,
        role1,
    )
    await db.execute(
        "INSERT INTO role_assignments (id, person_id, role_id, is_current) VALUES ($1,$2,$3,TRUE)",
        a2,
        person1,
        role2,
    )
    await db.execute(
        "INSERT INTO role_assignments (id, person_id, role_id, archived_at)"
        " VALUES ($1,$2,$3,NOW())",
        a3,
        person2,
        role1,
    )

    # Seed related data on a1 for detail-shape assertions.
    link_id = generate_id()
    await db.execute(
        "INSERT INTO links (id, entity_type, entity_id, url, link_type_id)"
        " VALUES ($1,'role_assignment',$2,'https://assignment.example.com',$3)",
        link_id,
        a1,
        link_type,
    )
    cm_id = generate_id()
    await db.execute(
        "INSERT INTO contact_methods"
        " (id, entity_type, entity_id, contact_type, value, display_label)"
        " VALUES ($1,'role_assignment',$2,'email','a1@example.com','Legislator Direct')",
        cm_id,
        a1,
    )
    addr_id = generate_id()
    ea_id = generate_id()
    await db.execute(
        "INSERT INTO addresses (id, raw_input, country) VALUES ($1,'1 Assignment Ave','US')",
        addr_id,
    )
    await db.execute(
        "INSERT INTO entity_addresses"
        " (id, entity_type, entity_id, address_id, address_type, valid_from, valid_until)"
        " VALUES ($1,'role_assignment',$2,$3,'physical',DATE '2024-01-01',DATE '2025-06-30')",
        ea_id,
        a1,
        addr_id,
    )

    yield {
        "person1": person1,
        "person2": person2,
        "org1": org1,
        "role1": role1,
        "role2": role2,
        "a1": a1,
        "a2": a2,
        "a3": a3,
    }

    await db.execute("DELETE FROM entity_addresses WHERE id=$1", ea_id)
    await db.execute("DELETE FROM addresses WHERE id=$1", addr_id)
    await db.execute("DELETE FROM contact_methods WHERE id=$1", cm_id)
    await db.execute("DELETE FROM links WHERE id=$1", link_id)
    await db.execute("DELETE FROM role_assignments WHERE id IN ($1,$2,$3)", a1, a2, a3)
    await db.execute("DELETE FROM roles WHERE id IN ($1,$2)", role1, role2)
    await db.execute("DELETE FROM organizations WHERE id=$1", org1)
    await db.execute("DELETE FROM people WHERE id IN ($1,$2)", person1, person2)


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
# GET /assignments — list
# ---------------------------------------------------------------------------


def test_list_returns_active_by_default(client, api_key, assignment_fixtures):
    r = client.get(
        _LIST,
        params={"person_id": assignment_fixtures["person1"]},
        headers={"X-API-Key": api_key},
    )
    assert r.status_code == 200
    ids = {item["id"] for item in r.json()["data"]}
    assert assignment_fixtures["a1"] in ids
    assert assignment_fixtures["a2"] in ids
    assert assignment_fixtures["a3"] not in ids  # archived, excluded by default


def test_list_include_archived(client, api_key, assignment_fixtures):
    r = client.get(
        _LIST,
        params={"person_id": assignment_fixtures["person2"], "include_archived": "true"},
        headers={"X-API-Key": api_key},
    )
    assert r.status_code == 200
    ids = {item["id"] for item in r.json()["data"]}
    assert assignment_fixtures["a3"] in ids


def test_list_filter_by_person_id(client, api_key, assignment_fixtures):
    r = client.get(
        _LIST,
        params={"person_id": assignment_fixtures["person1"]},
        headers={"X-API-Key": api_key},
    )
    assert r.status_code == 200
    ids = {item["id"] for item in r.json()["data"]}
    assert assignment_fixtures["a1"] in ids
    assert assignment_fixtures["a2"] in ids
    assert assignment_fixtures["a3"] not in ids  # person2, archived


def test_list_filter_by_role_id(client, api_key, assignment_fixtures):
    r = client.get(
        _LIST,
        params={"role_id": assignment_fixtures["role2"]},
        headers={"X-API-Key": api_key},
    )
    assert r.status_code == 200
    ids = {item["id"] for item in r.json()["data"]}
    assert assignment_fixtures["a2"] in ids
    assert assignment_fixtures["a1"] not in ids


def test_list_meta_shape(client, api_key, assignment_fixtures):
    r = client.get(_LIST, headers={"X-API-Key": api_key})
    assert r.status_code == 200
    meta = r.json()["meta"]
    assert "limit" in meta
    assert "offset" in meta
    assert "count" in meta
    assert "has_more" in meta


def test_list_item_shape(client, api_key, assignment_fixtures):
    r = client.get(
        _LIST,
        params={"person_id": assignment_fixtures["person1"]},
        headers={"X-API-Key": api_key},
    )
    item = next(i for i in r.json()["data"] if i["id"] == assignment_fixtures["a1"])
    assert item["person_id"] == assignment_fixtures["person1"]
    assert item["role_id"] == assignment_fixtures["role1"]
    assert item["start_date"] == "2023-01-01"
    assert item["end_date"] == "2024-12-31"
    assert item["is_current"] is False
    assert item["notes"] == "a1 notes"
    assert "created_at" in item
    assert "updated_at" in item
    assert "archived_at" in item


def test_list_pagination(client, api_key, assignment_fixtures):
    r = client.get(_LIST, params={"limit": 1, "offset": 0}, headers={"X-API-Key": api_key})
    assert r.status_code == 200
    body = r.json()
    assert body["meta"]["count"] == 1
    assert body["meta"]["has_more"] is True


# ---------------------------------------------------------------------------
# GET /assignments/{id} — detail
# ---------------------------------------------------------------------------


def test_detail_404_on_unknown(client, api_key):
    r = client.get(f"{_LIST}/{generate_id()}", headers={"X-API-Key": api_key})
    assert r.status_code == 404


def test_detail_shape(client, api_key, assignment_fixtures):
    a1 = assignment_fixtures["a1"]
    r = client.get(f"{_LIST}/{a1}", headers={"X-API-Key": api_key})
    assert r.status_code == 200
    body = r.json()
    assert body["id"] == a1
    assert body["person_id"] == assignment_fixtures["person1"]
    assert body["role_id"] == assignment_fixtures["role1"]
    assert body["start_date"] == "2023-01-01"
    assert body["end_date"] == "2024-12-31"
    assert body["is_current"] is False
    assert body["notes"] == "a1 notes"
    assert "links" in body
    assert "contact_methods" in body
    assert "addresses" in body


def test_detail_includes_link(client, api_key, assignment_fixtures):
    r = client.get(f"{_LIST}/{assignment_fixtures['a1']}", headers={"X-API-Key": api_key})
    links = r.json()["links"]
    assert len(links) == 1
    assert links[0]["url"] == "https://assignment.example.com"
    assert "link_type_slug" in links[0]


def test_detail_includes_contact_method(client, api_key, assignment_fixtures):
    r = client.get(f"{_LIST}/{assignment_fixtures['a1']}", headers={"X-API-Key": api_key})
    cms = r.json()["contact_methods"]
    assert len(cms) == 1
    assert cms[0]["value"] == "a1@example.com"


def test_detail_contact_method_includes_display_label(client, api_key, assignment_fixtures):
    """display_label is returned in the contact_methods array."""
    r = client.get(f"{_LIST}/{assignment_fixtures['a1']}", headers={"X-API-Key": api_key})
    cm = r.json()["contact_methods"][0]
    assert "display_label" in cm
    assert cm["display_label"] == "Legislator Direct"


async def test_detail_contact_method_display_label_null_when_unset(client, api_key, db):
    """display_label is null (not absent) when not set on the contact method."""
    org_id = generate_id()
    role_id = generate_id()
    person_id = generate_id()
    assignment_id = generate_id()
    cm_id = generate_id()
    await db.execute("INSERT INTO organizations (id) VALUES ($1)", org_id)
    await db.execute(
        "INSERT INTO roles (id, organization_id, title) VALUES ($1,$2,$3)",
        role_id,
        org_id,
        "Unlabelled Role",
    )
    await db.execute("INSERT INTO people (id) VALUES ($1)", person_id)
    await db.execute(
        "INSERT INTO role_assignments (id, person_id, role_id) VALUES ($1,$2,$3)",
        assignment_id,
        person_id,
        role_id,
    )
    await db.execute(
        "INSERT INTO contact_methods (id, entity_type, entity_id, contact_type, value)"
        " VALUES ($1,'role_assignment',$2,'phone','+12065550101')",
        cm_id,
        assignment_id,
    )
    try:
        r = client.get(f"{_LIST}/{assignment_id}", headers={"X-API-Key": api_key})
        cm = r.json()["contact_methods"][0]
        assert "display_label" in cm
        assert cm["display_label"] is None
    finally:
        await db.execute("DELETE FROM contact_methods WHERE id=$1", cm_id)
        await db.execute("DELETE FROM role_assignments WHERE id=$1", assignment_id)
        await db.execute("DELETE FROM people WHERE id=$1", person_id)
        await db.execute("DELETE FROM roles WHERE id=$1", role_id)
        await db.execute("DELETE FROM organizations WHERE id=$1", org_id)


def test_detail_includes_address(client, api_key, assignment_fixtures):
    r = client.get(f"{_LIST}/{assignment_fixtures['a1']}", headers={"X-API-Key": api_key})
    addrs = r.json()["addresses"]
    assert len(addrs) == 1
    assert addrs[0]["raw_input"] == "1 Assignment Ave"


def test_detail_address_includes_validity_window(client, api_key, assignment_fixtures):
    """valid_from/valid_until surface as ISO dates on assignment addresses (#181)."""
    r = client.get(f"{_LIST}/{assignment_fixtures['a1']}", headers={"X-API-Key": api_key})
    addrs = r.json()["addresses"]
    assert addrs[0]["valid_from"] == "2024-01-01"
    assert addrs[0]["valid_until"] == "2025-06-30"


def test_detail_etag_304(client, api_key, assignment_fixtures):
    a1 = assignment_fixtures["a1"]
    r1 = client.get(f"{_LIST}/{a1}", headers={"X-API-Key": api_key})
    assert r1.status_code == 200
    etag = r1.headers["etag"]
    r2 = client.get(f"{_LIST}/{a1}", headers={"X-API-Key": api_key, "if-none-match": etag})
    assert r2.status_code == 304
