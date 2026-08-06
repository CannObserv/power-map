"""Integration tests for GET /api/v1/jurisdictions/* endpoints."""

import hashlib
import os

import pytest
import pytest_asyncio

from src.core.db import generate_id

pytestmark = [
    pytest.mark.integration,
]

# Sync auth tests live in test_auth.py per project convention.


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture(loop_scope="session")
async def jur_api_key(db):
    """Read-only API key for jurisdiction tests."""
    uid = generate_id()
    kid = generate_id()
    raw = "pm_" + os.urandom(16).hex()
    key_hash = hashlib.sha256(raw.encode()).hexdigest()
    await db.execute("INSERT INTO app_users (id, email) VALUES ($1,$2)", uid, "jur_test@test.com")
    await db.execute(
        "INSERT INTO api_keys (id, user_id, label, key_prefix, key_hash) VALUES ($1,$2,$3,$4,$5)",
        kid,
        uid,
        "Jur Test Key",
        raw[:8],
        key_hash,
    )
    return raw


@pytest_asyncio.fixture(loop_scope="session")
async def jur_fixtures(db):
    """Create test jurisdictions, relationships, and identifiers; tear down after suite."""
    # Fetch type ids from seeds
    state_type_id = await db.fetchval("SELECT id FROM jurisdiction_types WHERE slug='state'")
    leg_upper_type_id = await db.fetchval(
        "SELECT id FROM jurisdiction_types WHERE slug='legislative_district_upper'"
    )
    is_contained_by_type_id = await db.fetchval(
        "SELECT id FROM jurisdiction_relationship_types WHERE slug='is_fully_contained_by'"
    )
    supersedes_type_id = await db.fetchval(
        "SELECT id FROM jurisdiction_relationship_types WHERE slug='supersedes'"
    )
    ocd_type_id = await db.fetchval("SELECT id FROM entity_identifier_types WHERE slug='jur_ocd'")

    wa_id = generate_id()
    ld21_id = generate_id()
    old_ld21_id = generate_id()
    archived_id = generate_id()
    rel_is_contained_by_id = generate_id()
    rel_supersedes_id = generate_id()
    identifier_id = generate_id()

    await db.execute(
        """
        INSERT INTO jurisdictions (id, slug, name, type_id)
        VALUES ($1,$2,$3,$4)
        """,
        wa_id,
        "usa-wa",
        "Washington",
        state_type_id,
    )
    await db.execute(
        """
        INSERT INTO jurisdictions (id, slug, name, type_id)
        VALUES ($1,$2,$3,$4)
        """,
        ld21_id,
        "usa-wa-ld-21",
        "Legislative District 21",
        leg_upper_type_id,
    )
    await db.execute(
        """
        INSERT INTO jurisdictions (id, slug, name, type_id)
        VALUES ($1,$2,$3,$4)
        """,
        old_ld21_id,
        "usa-wa-ld-21-2010",
        "Legislative District 21 (2010)",
        leg_upper_type_id,
    )
    await db.execute(
        """
        INSERT INTO jurisdictions (id, slug, name, type_id, archived_at)
        VALUES ($1,$2,$3,$4, NOW())
        """,
        archived_id,
        "usa-wa-archived",
        "Archived Jurisdiction",
        state_type_id,
    )

    # LD-21 is_fully_contained_by WA (spatial)
    await db.execute(
        """
        INSERT INTO jurisdiction_relationships (id, from_id, to_id, rel_type_id)
        VALUES ($1,$2,$3,$4)
        """,
        rel_is_contained_by_id,
        ld21_id,
        wa_id,
        is_contained_by_type_id,
    )
    # LD-21 supersedes old_LD-21 (lineage)
    await db.execute(
        """
        INSERT INTO jurisdiction_relationships (id, from_id, to_id, rel_type_id)
        VALUES ($1,$2,$3,$4)
        """,
        rel_supersedes_id,
        ld21_id,
        old_ld21_id,
        supersedes_type_id,
    )

    # OCD identifier on WA
    await db.execute(
        """
        INSERT INTO identifiers (id, entity_id, entity_identifier_type_id, value)
        VALUES ($1,$2,$3,$4)
        """,
        identifier_id,
        wa_id,
        ocd_type_id,
        "ocd-division/country:us/state:wa",
    )

    return {
        "wa_id": wa_id,
        "ld21_id": ld21_id,
        "old_ld21_id": old_ld21_id,
        "archived_id": archived_id,
        "rel_is_contained_by_id": rel_is_contained_by_id,
        "rel_supersedes_id": rel_supersedes_id,
    }


# ---------------------------------------------------------------------------
# GET /jurisdictions — list
# ---------------------------------------------------------------------------


async def test_list_empty(client, jur_api_key, db):
    """Empty state: no jurisdictions → data=[], has_more=False."""
    # Use a type slug that has no rows to isolate this test from jur_fixtures.
    r = await client.get(
        "/api/v1/jurisdictions?type=judicial_district",
        headers={"X-API-Key": jur_api_key},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["data"] == []
    assert body["meta"]["has_more"] is False


async def test_list_returns_active_by_default(client, jur_api_key, jur_fixtures):
    """Archived jurisdictions excluded from default list."""
    r = await client.get("/api/v1/jurisdictions", headers={"X-API-Key": jur_api_key})
    assert r.status_code == 200
    ids = {item["id"] for item in r.json()["data"]}
    assert jur_fixtures["archived_id"] not in ids


async def test_list_includes_archived_when_flag_set(client, jur_api_key, jur_fixtures):
    r = await client.get(
        "/api/v1/jurisdictions?include_archived=true",
        headers={"X-API-Key": jur_api_key},
    )
    assert r.status_code == 200
    ids = {item["id"] for item in r.json()["data"]}
    assert jur_fixtures["archived_id"] in ids


async def test_list_type_filter(client, jur_api_key, jur_fixtures):
    r = await client.get(
        "/api/v1/jurisdictions?type=state",
        headers={"X-API-Key": jur_api_key},
    )
    assert r.status_code == 200
    body = r.json()
    assert all(item["type"]["slug"] == "state" for item in body["data"])
    ids = {item["id"] for item in body["data"]}
    assert jur_fixtures["wa_id"] in ids
    assert jur_fixtures["ld21_id"] not in ids


async def test_list_pagination(client, jur_api_key, jur_fixtures):
    r1 = await client.get(
        "/api/v1/jurisdictions?limit=2&offset=0&include_archived=true",
        headers={"X-API-Key": jur_api_key},
    )
    assert r1.status_code == 200
    body1 = r1.json()
    assert len(body1["data"]) <= 2
    assert body1["meta"]["limit"] == 2

    if body1["meta"]["has_more"]:
        r2 = await client.get(
            "/api/v1/jurisdictions?limit=2&offset=2&include_archived=true",
            headers={"X-API-Key": jur_api_key},
        )
        assert r2.status_code == 200
        ids1 = {item["id"] for item in body1["data"]}
        ids2 = {item["id"] for item in r2.json()["data"]}
        assert ids1.isdisjoint(ids2)


async def test_list_response_shape(client, jur_api_key, jur_fixtures):
    r = await client.get(
        "/api/v1/jurisdictions?type=state",
        headers={"X-API-Key": jur_api_key},
    )
    assert r.status_code == 200
    item = next(i for i in r.json()["data"] if i["id"] == jur_fixtures["wa_id"])
    assert item["slug"] == "usa-wa"
    assert item["name"] == "Washington"
    assert item["type"]["slug"] == "state"
    assert "valid_from" in item
    assert "recorded_at" in item


# ---------------------------------------------------------------------------
# GET /jurisdictions/{id}
# ---------------------------------------------------------------------------


async def test_get_by_id(client, jur_api_key, jur_fixtures):
    r = await client.get(
        f"/api/v1/jurisdictions/{jur_fixtures['wa_id']}",
        headers={"X-API-Key": jur_api_key},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["id"] == jur_fixtures["wa_id"]
    assert body["slug"] == "usa-wa"
    assert isinstance(body["identifiers"], list)


async def test_get_by_slug(client, jur_api_key, jur_fixtures):
    r = await client.get("/api/v1/jurisdictions/usa-wa", headers={"X-API-Key": jur_api_key})
    assert r.status_code == 200
    assert r.json()["id"] == jur_fixtures["wa_id"]


async def test_get_includes_identifiers(client, jur_api_key, jur_fixtures):
    r = await client.get(
        f"/api/v1/jurisdictions/{jur_fixtures['wa_id']}",
        headers={"X-API-Key": jur_api_key},
    )
    assert r.status_code == 200
    idents = r.json()["identifiers"]
    assert len(idents) == 1
    assert idents[0]["type_slug"] == "jur_ocd"
    assert idents[0]["value"] == "ocd-division/country:us/state:wa"


async def test_get_not_found(client, jur_api_key):
    r = await client.get(
        "/api/v1/jurisdictions/nonexistent-slug", headers={"X-API-Key": jur_api_key}
    )
    assert r.status_code == 404


async def test_get_etag_304(client, jur_api_key, jur_fixtures):
    r1 = await client.get(
        f"/api/v1/jurisdictions/{jur_fixtures['wa_id']}",
        headers={"X-API-Key": jur_api_key},
    )
    assert r1.status_code == 200
    etag = r1.headers["ETag"]

    r2 = await client.get(
        f"/api/v1/jurisdictions/{jur_fixtures['wa_id']}",
        headers={"X-API-Key": jur_api_key, "If-None-Match": etag},
    )
    assert r2.status_code == 304


# ---------------------------------------------------------------------------
# GET /jurisdictions/resolve
# ---------------------------------------------------------------------------


async def test_resolve_by_slug(client, jur_api_key, jur_fixtures):
    r = await client.get(
        "/api/v1/jurisdictions/resolve?slug=usa-wa",
        headers={"X-API-Key": jur_api_key},
    )
    assert r.status_code == 200
    assert r.json()["id"] == jur_fixtures["wa_id"]


async def test_resolve_by_identifier(client, jur_api_key, jur_fixtures):
    r = await client.get(
        "/api/v1/jurisdictions/resolve?scheme=jur_ocd&value=ocd-division/country:us/state:wa",
        headers={"X-API-Key": jur_api_key},
    )
    assert r.status_code == 200
    assert r.json()["id"] == jur_fixtures["wa_id"]


async def test_resolve_slug_not_found(client, jur_api_key):
    r = await client.get(
        "/api/v1/jurisdictions/resolve?slug=does-not-exist",
        headers={"X-API-Key": jur_api_key},
    )
    assert r.status_code == 404


async def test_resolve_identifier_not_found(client, jur_api_key):
    r = await client.get(
        "/api/v1/jurisdictions/resolve?scheme=jur_ocd&value=ocd-division/country:us/state:zz",
        headers={"X-API-Key": jur_api_key},
    )
    assert r.status_code == 404


async def test_resolve_no_params_returns_422(client, jur_api_key):
    r = await client.get("/api/v1/jurisdictions/resolve", headers={"X-API-Key": jur_api_key})
    assert r.status_code == 422


async def test_resolve_partial_identifier_returns_422(client, jur_api_key):
    r = await client.get(
        "/api/v1/jurisdictions/resolve?scheme=jur_ocd",
        headers={"X-API-Key": jur_api_key},
    )
    assert r.status_code == 422


async def test_resolve_both_slug_and_identifier_returns_422(client, jur_api_key):
    r = await client.get(
        "/api/v1/jurisdictions/resolve?slug=usa-wa&scheme=jur_ocd&value=x",
        headers={"X-API-Key": jur_api_key},
    )
    assert r.status_code == 422


# ---------------------------------------------------------------------------
# GET /jurisdictions/{id}/relationships
# ---------------------------------------------------------------------------


async def test_relationships_empty(client, jur_api_key, jur_fixtures):
    """LD-21 has no governance-category edges — filter should return empty."""
    r = await client.get(
        f"/api/v1/jurisdictions/{jur_fixtures['ld21_id']}/relationships?category=governance",
        headers={"X-API-Key": jur_api_key},
    )
    assert r.status_code == 200
    assert r.json()["data"] == []


async def test_relationships_direction_from(client, jur_api_key, jur_fixtures):
    """LD-21 'from' direction → is_fully_contained_by edge to WA."""
    r = await client.get(
        f"/api/v1/jurisdictions/{jur_fixtures['ld21_id']}/relationships?direction=from",
        headers={"X-API-Key": jur_api_key},
    )
    assert r.status_code == 200
    rels = r.json()["data"]
    rel_ids = {rel["id"] for rel in rels}
    assert jur_fixtures["rel_is_contained_by_id"] in rel_ids


async def test_relationships_direction_to(client, jur_api_key, jur_fixtures):
    """WA 'to' direction → is_fully_contained_by edge from LD-21."""
    r = await client.get(
        f"/api/v1/jurisdictions/{jur_fixtures['wa_id']}/relationships?direction=to",
        headers={"X-API-Key": jur_api_key},
    )
    assert r.status_code == 200
    rels = r.json()["data"]
    rel_ids = {rel["id"] for rel in rels}
    assert jur_fixtures["rel_is_contained_by_id"] in rel_ids


async def test_relationships_category_filter(client, jur_api_key, jur_fixtures):
    r = await client.get(
        f"/api/v1/jurisdictions/{jur_fixtures['ld21_id']}/relationships?category=lineage",
        headers={"X-API-Key": jur_api_key},
    )
    assert r.status_code == 200
    rels = r.json()["data"]
    assert all(rel["rel_type"]["category"] == "lineage" for rel in rels)
    rel_ids = {rel["id"] for rel in rels}
    assert jur_fixtures["rel_supersedes_id"] in rel_ids


async def test_relationships_rel_type_filter(client, jur_api_key, jur_fixtures):
    r = await client.get(
        f"/api/v1/jurisdictions/{jur_fixtures['wa_id']}/relationships?rel_type=is_fully_contained_by",
        headers={"X-API-Key": jur_api_key},
    )
    assert r.status_code == 200
    rels = r.json()["data"]
    assert len(rels) >= 1
    assert all(rel["rel_type"]["slug"] == "is_fully_contained_by" for rel in rels)


async def test_relationships_not_found_returns_404(client, jur_api_key):
    r = await client.get(
        "/api/v1/jurisdictions/nonexistent/relationships",
        headers={"X-API-Key": jur_api_key},
    )
    assert r.status_code == 404


async def test_relationships_response_shape(client, jur_api_key, jur_fixtures):
    r = await client.get(
        f"/api/v1/jurisdictions/{jur_fixtures['ld21_id']}/relationships?direction=from",
        headers={"X-API-Key": jur_api_key},
    )
    assert r.status_code == 200
    rel = r.json()["data"][0]
    assert "id" in rel
    assert "from_id" in rel
    assert "to_id" in rel
    assert "rel_type" in rel
    assert rel["rel_type"]["is_symmetric"] is False  # 'is_fully_contained_by' is not symmetric


# ---------------------------------------------------------------------------
# GET /jurisdictions/{id}/lineage
# ---------------------------------------------------------------------------


async def test_lineage_single_hop(client, jur_api_key, jur_fixtures):
    """LD-21 supersedes old_LD-21 — lineage should include both."""
    r = await client.get(
        f"/api/v1/jurisdictions/{jur_fixtures['ld21_id']}/lineage",
        headers={"X-API-Key": jur_api_key},
    )
    assert r.status_code == 200
    ids = {item["id"] for item in r.json()["data"]}
    assert jur_fixtures["ld21_id"] in ids
    assert jur_fixtures["old_ld21_id"] in ids


async def test_lineage_min_depth_includes_direct_neighbor(client, jur_api_key, jur_fixtures):
    # depth=1 → CTE condition is 'l.depth < 1': allows exactly one recursive step
    # (base row at depth=0 triggers it, producing depth=1 neighbors).
    # Minimum enforced by ge=1; there is no way to return only the seed node.
    r = await client.get(
        f"/api/v1/jurisdictions/{jur_fixtures['ld21_id']}/lineage?depth=1",
        headers={"X-API-Key": jur_api_key},
    )
    assert r.status_code == 200
    ids = {item["id"] for item in r.json()["data"]}
    assert jur_fixtures["ld21_id"] in ids
    assert jur_fixtures["old_ld21_id"] in ids


async def test_lineage_no_lineage_edges_returns_only_self(client, jur_api_key, jur_fixtures):
    """WA has no lineage edges → lineage returns just WA."""
    r = await client.get(
        f"/api/v1/jurisdictions/{jur_fixtures['wa_id']}/lineage",
        headers={"X-API-Key": jur_api_key},
    )
    assert r.status_code == 200
    ids = {item["id"] for item in r.json()["data"]}
    assert ids == {jur_fixtures["wa_id"]}


async def test_lineage_not_found_returns_404(client, jur_api_key):
    r = await client.get(
        "/api/v1/jurisdictions/nonexistent/lineage",
        headers={"X-API-Key": jur_api_key},
    )
    assert r.status_code == 404


async def test_lineage_by_slug(client, jur_api_key, jur_fixtures):
    r = await client.get(
        "/api/v1/jurisdictions/usa-wa-ld-21/lineage",
        headers={"X-API-Key": jur_api_key},
    )
    assert r.status_code == 200
    ids = {item["id"] for item in r.json()["data"]}
    assert jur_fixtures["ld21_id"] in ids


# ---------------------------------------------------------------------------
# Conditional GET — relationships & lineage (#392 PR-C)
# ---------------------------------------------------------------------------


async def test_relationships_etag_round_trips_to_304(client, jur_api_key, jur_fixtures):
    url = f"/api/v1/jurisdictions/{jur_fixtures['wa_id']}/relationships"
    first = await client.get(url, headers={"X-API-Key": jur_api_key})
    assert first.status_code == 200
    etag = first.headers["etag"]

    r = await client.get(url, headers={"X-API-Key": jur_api_key, "If-None-Match": etag})
    assert r.status_code == 304
    assert r.content == b""
    assert r.headers["cache-control"] == "no-cache"
    assert r.headers["vary"] == "X-API-Key"


async def test_relationships_etag_is_per_filter_and_per_window(client, jur_api_key, jur_fixtures):
    base = f"/api/v1/jurisdictions/{jur_fixtures['wa_id']}/relationships"

    async def tag(query: str) -> str:
        r = await client.get(f"{base}{query}", headers={"X-API-Key": jur_api_key})
        assert r.status_code == 200
        return r.headers["etag"]

    plain = await tag("")
    assert await tag("?direction=from") != plain
    assert await tag("?direction=to") != plain
    assert await tag("?category=lineage") != plain
    assert await tag("?rel_type=supersedes") != plain
    assert await tag("?limit=5") != plain
    assert await tag("?offset=1") != plain


async def test_relationships_etag_accepts_slug_and_id_alike(client, jur_api_key, jur_fixtures):
    """Lookup accepts ULID or slug; both address the same resource version."""
    by_id = await client.get(
        f"/api/v1/jurisdictions/{jur_fixtures['wa_id']}/relationships",
        headers={"X-API-Key": jur_api_key},
    )
    by_slug = await client.get(
        "/api/v1/jurisdictions/usa-wa/relationships", headers={"X-API-Key": jur_api_key}
    )
    assert by_id.status_code == by_slug.status_code == 200
    assert by_id.headers["etag"] == by_slug.headers["etag"]


async def test_lineage_etag_round_trips_to_304(client, jur_api_key, jur_fixtures):
    url = f"/api/v1/jurisdictions/{jur_fixtures['ld21_id']}/lineage"
    first = await client.get(url, headers={"X-API-Key": jur_api_key})
    assert first.status_code == 200
    etag = first.headers["etag"]

    r = await client.get(url, headers={"X-API-Key": jur_api_key, "If-None-Match": etag})
    assert r.status_code == 304
    assert r.content == b""
    assert r.headers["vary"] == "X-API-Key"


async def test_lineage_etag_changes_when_a_traversed_jurisdiction_is_renamed(
    client, db, jur_api_key, jur_fixtures
):
    """A content hash over the traversal result covers *every* row it returned —
    including a predecessor reached through the recursive CTE."""
    url = f"/api/v1/jurisdictions/{jur_fixtures['ld21_id']}/lineage"
    before = (await client.get(url, headers={"X-API-Key": jur_api_key})).headers["etag"]

    await db.execute(
        "UPDATE jurisdictions SET name = name || ' (renamed)' WHERE id = $1",
        jur_fixtures["old_ld21_id"],
    )

    after = await client.get(url, headers={"X-API-Key": jur_api_key, "If-None-Match": before})
    assert after.status_code == 200, "renamed lineage member still revalidated as unchanged"


async def test_lineage_etag_tracks_the_traversal_not_the_depth_param(
    client, db, jur_api_key, jur_fixtures
):
    """`depth` is deliberately *not* baked into the tag.

    A content hash covers the representation the traversal actually produced, so
    two depths that reach the same set share a tag (correct, and cache-friendly)
    while a depth that reaches further gets its own. Baking the param in would
    only manufacture spurious misses.
    """
    base = f"/api/v1/jurisdictions/{jur_fixtures['ld21_id']}/lineage"

    async def tag(query: str) -> str:
        r = await client.get(f"{base}{query}", headers={"X-API-Key": jur_api_key})
        assert r.status_code == 200
        return r.headers["etag"]

    # The fixture chain is a single hop, so every depth reaches the same set.
    assert await tag("?depth=1") == await tag("?depth=10")

    # Extend it by one hop; now depth=1 and depth=10 see different sets.
    older_id = generate_id()
    state_type_id = await db.fetchval("SELECT id FROM jurisdiction_types WHERE slug='state'")
    supersedes_type_id = await db.fetchval(
        "SELECT id FROM jurisdiction_relationship_types WHERE slug='supersedes'"
    )
    await db.execute(
        "INSERT INTO jurisdictions (id, slug, name, type_id) VALUES ($1,$2,$3,$4)",
        older_id,
        f"usa-wa-ld-21-older-{older_id}",
        "Even Older LD 21",
        state_type_id,
    )
    await db.execute(
        "INSERT INTO jurisdiction_relationships (id, from_id, to_id, rel_type_id)"
        " VALUES ($1,$2,$3,$4)",
        generate_id(),
        jur_fixtures["old_ld21_id"],
        older_id,
        supersedes_type_id,
    )

    assert await tag("?depth=1") != await tag("?depth=10")


async def test_relationship_items_expose_updated_at(client, jur_api_key, jur_fixtures):
    """#392 added the column; expose it like the #301 RA→RA edge does (CR #392/16)."""
    r = await client.get(
        f"/api/v1/jurisdictions/{jur_fixtures['wa_id']}/relationships",
        headers={"X-API-Key": jur_api_key},
    )
    assert r.status_code == 200
    item = r.json()["data"][0]
    assert "updated_at" in item, "relationship items should expose updated_at"
    assert item["updated_at"].endswith("Z"), "timestamps serialize ISO 8601 with Z"
