"""Tests for GET /api/v1/subscriptions/discover — graph traversal subscription helper."""

import hashlib
import os

import pytest
import pytest_asyncio

from src.core.db import generate_id

pytestmark = [
    pytest.mark.integration,
]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture(loop_scope="session")
async def disc_api_key(db):
    """Any valid API key for discovery tests."""
    uid = generate_id()
    kid = generate_id()
    raw_key = "pm_" + os.urandom(16).hex()
    key_hash = hashlib.sha256(raw_key.encode()).hexdigest()
    await db.execute("INSERT INTO app_users (id, email) VALUES ($1,$2)", uid, "disc@test.com")
    await db.execute(
        "INSERT INTO api_keys (id, user_id, label, key_prefix, key_hash) VALUES ($1,$2,$3,$4,$5)",
        kid,
        uid,
        "Disc Test Key",
        raw_key[:8],
        key_hash,
    )
    return {"raw_key": raw_key, "key_id": kid}


@pytest_asyncio.fixture(loop_scope="session")
async def disc_graph(db):
    """Seed a complete traversal graph; yield IDs."""
    jtype_id = generate_id()
    root_jur_id = generate_id()
    child_jur_id = generate_id()
    jur_rel_id = generate_id()
    root_org_id = generate_id()
    oja_id = generate_id()
    child_org_id = generate_id()
    role_id = generate_id()
    person_id = generate_id()
    asgn_id = generate_id()

    # jurisdiction type
    await db.execute(
        "INSERT INTO jurisdiction_types (id, slug, display_name) VALUES ($1,$2,$3)",
        jtype_id,
        "disc-test-jtype",
        "Disc Test Type",
    )

    # jurisdictions
    await db.execute(
        "INSERT INTO jurisdictions (id, slug, name, type_id) VALUES ($1,$2,$3,$4)",
        root_jur_id,
        "disc-root-jur",
        "Disc Root Jurisdiction",
        jtype_id,
    )
    await db.execute(
        "INSERT INTO jurisdictions (id, slug, name, type_id) VALUES ($1,$2,$3,$4)",
        child_jur_id,
        "disc-child-jur",
        "Disc Child Jurisdiction",
        jtype_id,
    )

    # lineage edge: root supersedes child (uses seeded 'supersedes' type, category='lineage')
    lineage_type_id = await db.fetchval(
        "SELECT id FROM jurisdiction_relationship_types WHERE slug='supersedes'"
    )
    await db.execute(
        "INSERT INTO jurisdiction_relationships (id, from_id, to_id, rel_type_id)"
        " VALUES ($1,$2,$3,$4)",
        jur_rel_id,
        root_jur_id,
        child_jur_id,
        lineage_type_id,
    )

    # spatial containment edge for the spatial-lineage test below
    spatial_child_jur_id = generate_id()
    spatial_jur_rel_id = generate_id()
    await db.execute(
        "INSERT INTO jurisdictions (id, slug, name, type_id) VALUES ($1,$2,$3,$4)",
        spatial_child_jur_id,
        "disc-spatial-child-jur",
        "Disc Spatial Child Jurisdiction",
        jtype_id,
    )
    spatial_type_id = await db.fetchval(
        "SELECT id FROM jurisdiction_relationship_types WHERE slug='is_fully_contained_by'"
    )
    await db.execute(
        "INSERT INTO jurisdiction_relationships (id, from_id, to_id, rel_type_id)"
        " VALUES ($1,$2,$3,$4)",
        spatial_jur_rel_id,
        spatial_child_jur_id,
        root_jur_id,
        spatial_type_id,
    )

    # organization
    await db.execute("INSERT INTO organizations (id) VALUES ($1)", root_org_id)
    await db.execute("INSERT INTO organizations (id) VALUES ($1)", child_org_id)
    await db.execute("UPDATE organizations SET parent_id=$1 WHERE id=$2", root_org_id, child_org_id)

    # org-jurisdiction affiliation (governing)
    governing_type_id = await db.fetchval(
        "SELECT id FROM organization_jurisdiction_affiliation_types WHERE slug='governing'"
    )
    await db.execute(
        "INSERT INTO organization_jurisdiction_affiliations"
        " (id, organization_id, jurisdiction_id, affiliation_type_id) VALUES ($1,$2,$3,$4)",
        oja_id,
        root_org_id,
        root_jur_id,
        governing_type_id,
    )

    # role, person, assignment
    await db.execute(
        "INSERT INTO roles (id, organization_id, title) VALUES ($1,$2,$3)",
        role_id,
        root_org_id,
        "Disc Test Role",
    )
    await db.execute("INSERT INTO people (id) VALUES ($1)", person_id)
    await db.execute(
        "INSERT INTO role_assignments (id, person_id, role_id) VALUES ($1,$2,$3)",
        asgn_id,
        person_id,
        role_id,
    )

    return {
        "root_jur_id": root_jur_id,
        "child_jur_id": child_jur_id,
        "spatial_child_jur_id": spatial_child_jur_id,
        "root_org_id": root_org_id,
        "child_org_id": child_org_id,
        "role_id": role_id,
        "person_id": person_id,
        "asgn_id": asgn_id,
    }


# ---------------------------------------------------------------------------
# Root resolution
# ---------------------------------------------------------------------------


async def test_discover_root_jurisdiction_at_hops_zero(client, disc_api_key, disc_graph):
    """Root jurisdiction appears at hops_from_root=0."""
    r = await client.get(
        "/api/v1/subscriptions/discover",
        params={
            "root_type": "jurisdiction",
            "root_id": disc_graph["root_jur_id"],
            "follow": "",
        },
        headers={"X-API-Key": disc_api_key["raw_key"]},
    )
    assert r.status_code == 200
    data = r.json()["data"]
    assert len(data) == 1
    assert data[0]["entity_id"] == disc_graph["root_jur_id"]
    assert data[0]["entity_type"] == "jurisdiction"
    assert data[0]["hops_from_root"] == 0


async def test_discover_root_organization_at_hops_zero(client, disc_api_key, disc_graph):
    """Root organization appears at hops_from_root=0."""
    r = await client.get(
        "/api/v1/subscriptions/discover",
        params={
            "root_type": "organization",
            "root_id": disc_graph["root_org_id"],
            "follow": "",
        },
        headers={"X-API-Key": disc_api_key["raw_key"]},
    )
    assert r.status_code == 200
    data = r.json()["data"]
    assert len(data) == 1
    assert data[0]["entity_id"] == disc_graph["root_org_id"]
    assert data[0]["entity_type"] == "organization"
    assert data[0]["hops_from_root"] == 0


async def test_discover_root_not_found(client, disc_api_key):
    """Unknown root_id → 404."""
    r = await client.get(
        "/api/v1/subscriptions/discover",
        params={
            "root_type": "jurisdiction",
            "root_id": generate_id(),
            "follow": "",
        },
        headers={"X-API-Key": disc_api_key["raw_key"]},
    )
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# follow=lineage
# ---------------------------------------------------------------------------


async def test_discover_lineage_finds_connected_jurisdiction(client, disc_api_key, disc_graph):
    """lineage step finds child_jur at hops=1."""
    r = await client.get(
        "/api/v1/subscriptions/discover",
        params={
            "root_type": "jurisdiction",
            "root_id": disc_graph["root_jur_id"],
            "follow": "lineage",
            "limit": 100,
        },
        headers={"X-API-Key": disc_api_key["raw_key"]},
    )
    assert r.status_code == 200
    data = r.json()["data"]
    ids = {item["entity_id"] for item in data}
    assert disc_graph["child_jur_id"] in ids

    child = next(i for i in data if i["entity_id"] == disc_graph["child_jur_id"])
    assert child["hops_from_root"] == 1
    assert child["entity_type"] == "jurisdiction"


async def test_discover_lineage_finds_spatial_containment_child(client, disc_api_key, disc_graph):
    """lineage step traverses spatial containment (is_fully_contained_by) edges."""
    r = await client.get(
        "/api/v1/subscriptions/discover",
        params={
            "root_type": "jurisdiction",
            "root_id": disc_graph["root_jur_id"],
            "follow": "lineage",
            "limit": 100,
        },
        headers={"X-API-Key": disc_api_key["raw_key"]},
    )
    assert r.status_code == 200
    ids = {item["entity_id"] for item in r.json()["data"]}
    assert disc_graph["spatial_child_jur_id"] in ids


async def test_discover_lineage_invalid_for_org_root(client, disc_api_key, disc_graph):
    """lineage follow with root_type=organization → 422."""
    r = await client.get(
        "/api/v1/subscriptions/discover",
        params={
            "root_type": "organization",
            "root_id": disc_graph["root_org_id"],
            "follow": "lineage",
        },
        headers={"X-API-Key": disc_api_key["raw_key"]},
    )
    assert r.status_code == 422


# ---------------------------------------------------------------------------
# follow=affiliated_orgs
# ---------------------------------------------------------------------------


async def test_discover_affiliated_orgs(client, disc_api_key, disc_graph):
    """affiliated_orgs finds governing org at hops=1."""
    r = await client.get(
        "/api/v1/subscriptions/discover",
        params={
            "root_type": "jurisdiction",
            "root_id": disc_graph["root_jur_id"],
            "follow": "affiliated_orgs",
            "limit": 100,
        },
        headers={"X-API-Key": disc_api_key["raw_key"]},
    )
    assert r.status_code == 200
    data = r.json()["data"]
    ids = {item["entity_id"] for item in data}
    assert disc_graph["root_org_id"] in ids
    org = next(i for i in data if i["entity_id"] == disc_graph["root_org_id"])
    assert org["entity_type"] == "organization"
    assert org["hops_from_root"] == 1


async def test_discover_affiliated_orgs_invalid_without_jurisdiction(
    client, disc_api_key, disc_graph
):
    """affiliated_orgs with root_type=organization and no lineage step → 422."""
    r = await client.get(
        "/api/v1/subscriptions/discover",
        params={
            "root_type": "organization",
            "root_id": disc_graph["root_org_id"],
            "follow": "affiliated_orgs",
        },
        headers={"X-API-Key": disc_api_key["raw_key"]},
    )
    assert r.status_code == 422


# ---------------------------------------------------------------------------
# follow=org_children
# ---------------------------------------------------------------------------


async def test_discover_org_children(client, disc_api_key, disc_graph):
    """org_children finds child org."""
    r = await client.get(
        "/api/v1/subscriptions/discover",
        params={
            "root_type": "organization",
            "root_id": disc_graph["root_org_id"],
            "follow": "org_children",
            "limit": 100,
        },
        headers={"X-API-Key": disc_api_key["raw_key"]},
    )
    assert r.status_code == 200
    ids = {item["entity_id"] for item in r.json()["data"]}
    assert disc_graph["child_org_id"] in ids


async def test_discover_org_children_invalid_without_org(client, disc_api_key, disc_graph):
    """org_children with root_type=jurisdiction and no affiliated_orgs → 422."""
    r = await client.get(
        "/api/v1/subscriptions/discover",
        params={
            "root_type": "jurisdiction",
            "root_id": disc_graph["root_jur_id"],
            "follow": "org_children",
        },
        headers={"X-API-Key": disc_api_key["raw_key"]},
    )
    assert r.status_code == 422


# ---------------------------------------------------------------------------
# follow=roles
# ---------------------------------------------------------------------------


async def test_discover_roles(client, disc_api_key, disc_graph):
    """roles step finds role for the org."""
    r = await client.get(
        "/api/v1/subscriptions/discover",
        params={
            "root_type": "organization",
            "root_id": disc_graph["root_org_id"],
            "follow": "roles",
            "limit": 100,
        },
        headers={"X-API-Key": disc_api_key["raw_key"]},
    )
    assert r.status_code == 200
    ids = {item["entity_id"] for item in r.json()["data"]}
    assert disc_graph["role_id"] in ids


# ---------------------------------------------------------------------------
# follow=assignments
# ---------------------------------------------------------------------------


async def test_discover_assignments(client, disc_api_key, disc_graph):
    """assignments step finds role_assignment."""
    r = await client.get(
        "/api/v1/subscriptions/discover",
        params={
            "root_type": "organization",
            "root_id": disc_graph["root_org_id"],
            "follow": "roles,assignments",
            "limit": 100,
        },
        headers={"X-API-Key": disc_api_key["raw_key"]},
    )
    assert r.status_code == 200
    ids = {item["entity_id"] for item in r.json()["data"]}
    assert disc_graph["asgn_id"] in ids


async def test_discover_assignments_invalid_without_roles(client, disc_api_key, disc_graph):
    """assignments without roles in follow list → 422."""
    r = await client.get(
        "/api/v1/subscriptions/discover",
        params={
            "root_type": "organization",
            "root_id": disc_graph["root_org_id"],
            "follow": "assignments",
        },
        headers={"X-API-Key": disc_api_key["raw_key"]},
    )
    assert r.status_code == 422


# ---------------------------------------------------------------------------
# follow=people
# ---------------------------------------------------------------------------


async def test_discover_people(client, disc_api_key, disc_graph):
    """people step finds person via assignment."""
    r = await client.get(
        "/api/v1/subscriptions/discover",
        params={
            "root_type": "organization",
            "root_id": disc_graph["root_org_id"],
            "follow": "roles,assignments,people",
            "limit": 100,
        },
        headers={"X-API-Key": disc_api_key["raw_key"]},
    )
    assert r.status_code == 200
    ids = {item["entity_id"] for item in r.json()["data"]}
    assert disc_graph["person_id"] in ids


async def test_discover_people_invalid_without_assignments(client, disc_api_key, disc_graph):
    """people without assignments in follow list → 422."""
    r = await client.get(
        "/api/v1/subscriptions/discover",
        params={
            "root_type": "organization",
            "root_id": disc_graph["root_org_id"],
            "follow": "roles,people",
        },
        headers={"X-API-Key": disc_api_key["raw_key"]},
    )
    assert r.status_code == 422


# ---------------------------------------------------------------------------
# Full chain
# ---------------------------------------------------------------------------


async def test_discover_full_chain_from_jurisdiction(client, disc_api_key, disc_graph):
    """Full follow chain from jurisdiction root finds all expected entity types."""
    r = await client.get(
        "/api/v1/subscriptions/discover",
        params={
            "root_type": "jurisdiction",
            "root_id": disc_graph["root_jur_id"],
            "follow": "lineage,affiliated_orgs,org_children,roles,assignments,people",
            "limit": 500,
        },
        headers={"X-API-Key": disc_api_key["raw_key"]},
    )
    assert r.status_code == 200
    data = r.json()["data"]
    ids = {item["entity_id"] for item in data}
    assert disc_graph["root_jur_id"] in ids
    assert disc_graph["child_jur_id"] in ids
    assert disc_graph["spatial_child_jur_id"] in ids
    assert disc_graph["root_org_id"] in ids
    assert disc_graph["child_org_id"] in ids
    assert disc_graph["role_id"] in ids
    assert disc_graph["asgn_id"] in ids
    assert disc_graph["person_id"] in ids


# ---------------------------------------------------------------------------
# Response shape
# ---------------------------------------------------------------------------


async def test_discover_response_has_display_name(client, disc_api_key, disc_graph):
    """Each item includes the display_name key."""
    r = await client.get(
        "/api/v1/subscriptions/discover",
        params={
            "root_type": "jurisdiction",
            "root_id": disc_graph["root_jur_id"],
            "follow": "",
        },
        headers={"X-API-Key": disc_api_key["raw_key"]},
    )
    assert r.status_code == 200
    for item in r.json()["data"]:
        assert "display_name" in item


async def test_discover_jurisdiction_display_name_is_string(client, disc_api_key, disc_graph):
    """Jurisdiction display_name is the jurisdictions.name value (NOT NULL column)."""
    r = await client.get(
        "/api/v1/subscriptions/discover",
        params={"root_type": "jurisdiction", "root_id": disc_graph["root_jur_id"], "follow": ""},
        headers={"X-API-Key": disc_api_key["raw_key"]},
    )
    assert r.status_code == 200
    item = r.json()["data"][0]
    assert item["display_name"] == "Disc Root Jurisdiction"


async def test_discover_role_display_name_is_string(client, disc_api_key, disc_graph):
    """Role display_name is the roles.title value (NOT NULL column)."""
    r = await client.get(
        "/api/v1/subscriptions/discover",
        params={
            "root_type": "organization",
            "root_id": disc_graph["root_org_id"],
            "follow": "roles",
            "limit": 100,
        },
        headers={"X-API-Key": disc_api_key["raw_key"]},
    )
    assert r.status_code == 200
    data = r.json()["data"]
    role_item = next(i for i in data if i["entity_id"] == disc_graph["role_id"])
    assert role_item["display_name"] == "Disc Test Role"


async def test_discover_meta_structure(client, disc_api_key, disc_graph):
    """meta contains limit, offset, count, has_more, truncated."""
    r = await client.get(
        "/api/v1/subscriptions/discover",
        params={
            "root_type": "jurisdiction",
            "root_id": disc_graph["root_jur_id"],
            "follow": "lineage",
            "limit": 100,
        },
        headers={"X-API-Key": disc_api_key["raw_key"]},
    )
    assert r.status_code == 200
    meta = r.json()["meta"]
    assert "limit" in meta
    assert "offset" in meta
    assert "count" in meta
    assert "has_more" in meta
    assert meta["truncated"] is False


async def test_discover_pagination(client, disc_api_key, disc_graph):
    """limit=1 with multi-item result → has_more=True, count=1."""
    r = await client.get(
        "/api/v1/subscriptions/discover",
        params={
            "root_type": "jurisdiction",
            "root_id": disc_graph["root_jur_id"],
            "follow": "lineage",
            "limit": 1,
        },
        headers={"X-API-Key": disc_api_key["raw_key"]},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["meta"]["count"] == 1
    assert body["meta"]["has_more"] is True


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


async def test_discover_unknown_follow_value(client, disc_api_key, disc_graph):
    """Unknown follow value → 422."""
    r = await client.get(
        "/api/v1/subscriptions/discover",
        params={
            "root_type": "jurisdiction",
            "root_id": disc_graph["root_jur_id"],
            "follow": "invalid_step",
        },
        headers={"X-API-Key": disc_api_key["raw_key"]},
    )
    assert r.status_code == 422


async def test_discover_missing_root_type(client, disc_api_key, disc_graph):
    """Missing root_type → 422."""
    r = await client.get(
        "/api/v1/subscriptions/discover",
        params={"root_id": disc_graph["root_jur_id"], "follow": ""},
        headers={"X-API-Key": disc_api_key["raw_key"]},
    )
    assert r.status_code == 422


async def test_discover_invalid_root_type(client, disc_api_key, disc_graph):
    """Unknown root_type value → 422."""
    r = await client.get(
        "/api/v1/subscriptions/discover",
        params={
            "root_type": "galaxy",
            "root_id": disc_graph["root_jur_id"],
            "follow": "",
        },
        headers={"X-API-Key": disc_api_key["raw_key"]},
    )
    assert r.status_code == 422


async def test_discover_requires_auth(client, disc_graph):
    """No API key → 403 (header absent)."""
    r = await client.get(
        "/api/v1/subscriptions/discover",
        params={
            "root_type": "jurisdiction",
            "root_id": disc_graph["root_jur_id"],
            "follow": "",
        },
    )
    assert r.status_code == 403
