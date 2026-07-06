"""Tests for GET /api/v1/role-types (#268).

Public read catalog of the role_types classifier — the role-type vocabulary
producers attach to. Mirrors the link-types lookup endpoint.

Per-test markers (not a module-level ``pytestmark``): the DB-backed cases carry
``@pytest.mark.integration``; the keyless auth-reject case is a pure unit test
(``unit_client``, never touches the DB) so it runs in the fast suite.
"""

import hashlib
import os

import pytest
import pytest_asyncio

from src.core.db import generate_id


@pytest_asyncio.fixture(loop_scope="session")
async def api_key(db):
    """Insert a test app_user + api_key; yield raw_key; clean up."""
    uid = generate_id()
    kid = generate_id()
    raw_key = "pm_" + os.urandom(16).hex()
    key_hash = hashlib.sha256(raw_key.encode()).hexdigest()
    await db.execute(
        "INSERT INTO app_users (id, email) VALUES ($1,$2)", uid, "roletypetest@test.com"
    )
    await db.execute(
        "INSERT INTO api_keys (id, user_id, label, key_prefix, key_hash) VALUES ($1,$2,$3,$4,$5)",
        kid,
        uid,
        "Role Type Test Key",
        raw_key[:8],
        key_hash,
    )
    yield raw_key
    await db.execute("DELETE FROM api_keys WHERE id=$1", kid)
    await db.execute("DELETE FROM app_users WHERE id=$1", uid)


@pytest.mark.integration
async def test_role_types_with_valid_key_returns_200(client, api_key):
    response = client.get("/api/v1/role-types", headers={"X-API-Key": api_key})
    assert response.status_code == 200


@pytest.mark.integration
async def test_role_types_response_has_data_list(client, api_key):
    response = client.get("/api/v1/role-types", headers={"X-API-Key": api_key})
    body = response.json()
    assert "data" in body
    assert isinstance(body["data"], list)
    # Unpaginated catalog — no meta envelope.
    assert "meta" not in body


@pytest.mark.integration
async def test_role_types_items_have_required_fields(client, api_key):
    response = client.get("/api/v1/role-types", headers={"X-API-Key": api_key})
    item = response.json()["data"][0]
    assert set(item) == {"id", "slug", "display_name", "expects_jurisdiction", "requires_qualifier"}
    assert isinstance(item["expects_jurisdiction"], bool)
    assert isinstance(item["requires_qualifier"], bool)


@pytest.mark.integration
async def test_role_types_seeded_offices_present_and_expect_jurisdiction(client, api_key):
    """The #261 seeded offices are present and flagged expects_jurisdiction=True."""
    response = client.get("/api/v1/role-types", headers={"X-API-Key": api_key})
    by_slug = {r["slug"]: r for r in response.json()["data"]}
    assert {"state_representative", "state_senator"} <= set(by_slug)
    assert by_slug["state_representative"]["display_name"] == "State Representative"
    assert by_slug["state_senator"]["expects_jurisdiction"] is True
    assert by_slug["state_representative"]["expects_jurisdiction"] is True


@pytest.mark.integration
async def test_role_types_member_seeded_non_jurisdictional(client, api_key):
    """The #269 `member` classifier is seeded with expects_jurisdiction=False.

    Coarse body/committee membership (person is a member of a body, no precise
    seat) — a jurisdiction-less classifier, so expects_jurisdiction must be False.
    """
    response = client.get("/api/v1/role-types", headers={"X-API-Key": api_key})
    by_slug = {r["slug"]: r for r in response.json()["data"]}
    assert "member" in by_slug
    assert by_slug["member"]["display_name"] == "Member"
    assert by_slug["member"]["expects_jurisdiction"] is False


@pytest.mark.integration
async def test_role_types_requires_qualifier_seeded(client, api_key):
    """requires_qualifier (#273) is True only for per-position offices.

    House seats are per-position (Position 1/2) so `state_representative`
    requires a qualifier; a state senator (one per district) and a plain
    `member` do not.
    """
    response = client.get("/api/v1/role-types", headers={"X-API-Key": api_key})
    by_slug = {r["slug"]: r for r in response.json()["data"]}
    assert by_slug["state_representative"]["requires_qualifier"] is True
    assert by_slug["state_senator"]["requires_qualifier"] is False
    assert by_slug["member"]["requires_qualifier"] is False


@pytest.mark.integration
async def test_role_types_non_structural_defaults_false(client, api_key, db):
    """A role_type inserted without the flag surfaces expects_jurisdiction=false (default)."""
    rid = generate_id()
    # Per-run-unique slug so a crash-orphaned row can never collide on the next
    # run's slug UNIQUE constraint.
    slug = f"cr_test_nonstructural_{rid[-8:].lower()}"
    await db.execute(
        "INSERT INTO role_types (id, slug, display_name) VALUES ($1,$2,$3)",
        rid,
        slug,
        "CR Test Non-Structural",
    )
    try:
        response = client.get("/api/v1/role-types", headers={"X-API-Key": api_key})
        by_slug = {r["slug"]: r for r in response.json()["data"]}
        assert slug in by_slug
        assert by_slug[slug]["expects_jurisdiction"] is False
    finally:
        await db.execute("DELETE FROM role_types WHERE id=$1", rid)


@pytest.mark.integration
async def test_role_types_sorted_by_slug(client, api_key):
    response = client.get("/api/v1/role-types", headers={"X-API-Key": api_key})
    slugs = [r["slug"] for r in response.json()["data"]]
    assert slugs == sorted(slugs)


def test_role_types_without_key_returns_403(unit_client):
    response = unit_client.get("/api/v1/role-types")
    assert response.status_code == 403


@pytest.mark.integration
async def test_role_types_with_invalid_key_returns_401(client):
    response = client.get("/api/v1/role-types", headers={"X-API-Key": "pm_invalid"})
    assert response.status_code == 401
