"""Tests for GET /api/v1/link-types.

Per-test markers: DB-backed cases carry ``@pytest.mark.integration``; the keyless
auth-reject case is a pure unit test (``unit_client``) so it runs in the fast
suite. (No module-level ``pytestmark`` — see test_role_types.py / test_auth.py.)
"""

import hashlib
import os

import pytest
import pytest_asyncio

from src.core.db import generate_id


@pytest_asyncio.fixture(loop_scope="session")
async def api_key(db):
    """Insert a test app_user + api_key; return raw_key (rolled back per test)."""
    uid = generate_id()
    kid = generate_id()
    raw_key = "pm_" + os.urandom(16).hex()
    key_hash = hashlib.sha256(raw_key.encode()).hexdigest()
    await db.execute(
        "INSERT INTO app_users (id, email) VALUES ($1,$2)", uid, "linktypetest@test.com"
    )
    await db.execute(
        "INSERT INTO api_keys (id, user_id, label, key_prefix, key_hash) VALUES ($1,$2,$3,$4,$5)",
        kid,
        uid,
        "Link Type Test Key",
        raw_key[:8],
        key_hash,
    )
    return raw_key


@pytest.mark.integration
async def test_link_types_with_valid_key_returns_200(client, api_key):
    """GET /api/v1/link-types with valid key returns 200."""
    response = await client.get("/api/v1/link-types", headers={"X-API-Key": api_key})
    assert response.status_code == 200


@pytest.mark.integration
async def test_link_types_response_has_data_list(client, api_key):
    """Response has `data` key with list of items."""
    response = await client.get("/api/v1/link-types", headers={"X-API-Key": api_key})
    assert response.status_code == 200
    body = response.json()
    assert "data" in body
    assert isinstance(body["data"], list)


@pytest.mark.integration
async def test_link_types_items_have_required_fields(client, api_key):
    """Each item in data list has id, slug, display_name, is_social."""
    response = await client.get("/api/v1/link-types", headers={"X-API-Key": api_key})
    assert response.status_code == 200
    body = response.json()
    if body["data"]:  # At least check that fields are present if any exist
        item = body["data"][0]
        assert "id" in item
        assert "slug" in item
        assert "display_name" in item
        assert "is_social" in item
        assert isinstance(item["is_social"], bool)


def test_link_types_without_key_returns_403(unit_client):
    """GET /api/v1/link-types without X-API-Key returns 403."""
    response = unit_client.get("/api/v1/link-types")
    assert response.status_code == 403


@pytest.mark.integration
async def test_link_types_with_invalid_key_returns_401(client):
    """GET /api/v1/link-types with invalid key returns 401."""
    response = await client.get("/api/v1/link-types", headers={"X-API-Key": "pm_invalid"})
    assert response.status_code == 401


# ── conditional GET (#392) ────────────────────────────────────────────────────


@pytest.mark.integration
async def test_link_types_etag_round_trips_to_304(client, api_key):
    first = await client.get("/api/v1/link-types", headers={"X-API-Key": api_key})
    assert first.status_code == 200
    etag = first.headers["etag"]
    assert etag.startswith('"') and etag.endswith('"')

    r = await client.get(
        "/api/v1/link-types", headers={"X-API-Key": api_key, "If-None-Match": etag}
    )
    assert r.status_code == 304
    assert r.content == b""
    assert r.headers["cache-control"] == "no-cache"
    assert r.headers["vary"] == "X-API-Key"
    # A catalog with no updated_at column has no defensible Last-Modified.
    assert "last-modified" not in r.headers


@pytest.mark.integration
async def test_link_types_etag_changes_on_in_place_rename(client, db, api_key):
    """The reason this catalog gets a content hash rather than a watermark.

    `link_types` is admin-editable (`settings_link_types.py` UPDATEs
    display_name/slug) and has only `created_at` — a count + max(created_at)
    tag would be *stable* across this rename and a 304ing consumer would hold
    the stale label indefinitely.
    """
    before = (await client.get("/api/v1/link-types", headers={"X-API-Key": api_key})).headers[
        "etag"
    ]
    await db.execute(
        "UPDATE link_types SET display_name = display_name || ' (renamed)'"
        " WHERE id = (SELECT id FROM link_types ORDER BY slug LIMIT 1)"
    )
    after = await client.get(
        "/api/v1/link-types", headers={"X-API-Key": api_key, "If-None-Match": before}
    )
    assert after.status_code == 200, "in-place rename still revalidated as unchanged"
    assert after.headers["etag"] != before


@pytest.mark.integration
async def test_link_types_etag_changes_on_row_add(client, db, api_key):
    before = (await client.get("/api/v1/link-types", headers={"X-API-Key": api_key})).headers[
        "etag"
    ]
    await db.execute(
        "INSERT INTO link_types (id, slug, display_name, is_social) VALUES ($1,$2,$3,FALSE)",
        generate_id(),
        "zzz-conditional-get-probe",
        "Probe",
    )
    after = await client.get(
        "/api/v1/link-types", headers={"X-API-Key": api_key, "If-None-Match": before}
    )
    assert after.status_code == 200
