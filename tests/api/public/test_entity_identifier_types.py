"""Tests for GET /api/v1/entity-identifier-types (#459).

Public read catalog of ``entity_identifier_types`` — the identity vocabulary a
producer addresses entities by. The fourth and last observation vocabulary to
get a catalog endpoint; mirrors the link-types and role-types lookups.

Per-test markers (not a module-level ``pytestmark``): the DB-backed cases carry
``@pytest.mark.integration``; the keyless auth-reject case is a pure unit test
(``unit_client``, never touches the DB) so it runs in the fast suite.
"""

import hashlib
import os

import pytest
import pytest_asyncio

from src.core.db import generate_id

ENDPOINT = "/api/v1/entity-identifier-types"


@pytest_asyncio.fixture(loop_scope="session")
async def api_key(db):
    """Insert a test app_user + api_key; return raw_key (rolled back per test)."""
    uid = generate_id()
    kid = generate_id()
    raw_key = "pm_" + os.urandom(16).hex()
    key_hash = hashlib.sha256(raw_key.encode()).hexdigest()
    await db.execute("INSERT INTO app_users (id, email) VALUES ($1,$2)", uid, "idtypetest@test.com")
    await db.execute(
        "INSERT INTO api_keys (id, user_id, label, key_prefix, key_hash) VALUES ($1,$2,$3,$4,$5)",
        kid,
        uid,
        "Identifier Type Test Key",
        raw_key[:8],
        key_hash,
    )
    return raw_key


@pytest.mark.integration
async def test_identifier_types_with_valid_key_returns_200(client, api_key):
    response = await client.get(ENDPOINT, headers={"X-API-Key": api_key})
    assert response.status_code == 200


@pytest.mark.integration
async def test_identifier_types_response_has_data_list(client, api_key):
    response = await client.get(ENDPOINT, headers={"X-API-Key": api_key})
    body = response.json()
    assert "data" in body
    assert isinstance(body["data"], list)
    # Unpaginated catalog — no meta envelope.
    assert "meta" not in body


@pytest.mark.integration
async def test_identifier_types_items_have_required_fields(client, api_key):
    response = await client.get(ENDPOINT, headers={"X-API-Key": api_key})
    item = response.json()["data"][0]
    assert set(item) == {
        "id",
        "slug",
        "entity_type",
        "display_name",
        "full_name",
        "is_internal",
    }
    assert isinstance(item["is_internal"], bool)


@pytest.mark.integration
async def test_identifier_types_entity_type_is_from_the_check_vocabulary(client, api_key):
    response = await client.get(ENDPOINT, headers={"X-API-Key": api_key})
    kinds = {r["entity_type"] for r in response.json()["data"]}
    assert kinds <= {"organization", "person", "role_assignment", "jurisdiction"}


@pytest.mark.integration
async def test_identifier_types_flags_internal_types(client, api_key):
    """`is_internal` is the field this endpoint exists to expose.

    An internal type *addresses* an entity and never creates one; it is refused
    in `additional_identifiers`. Before this endpoint that distinction was
    discoverable only by reading `src/core/observation.py`.
    """
    response = await client.get(ENDPOINT, headers={"X-API-Key": api_key})
    by_slug = {r["slug"]: r for r in response.json()["data"]}
    assert by_slug["pm_person_id"]["is_internal"] is True
    assert by_slug["pm_org_id"]["is_internal"] is True
    assert by_slug["pm_assignment_id"]["is_internal"] is True
    assert by_slug["pm_jur_id"]["is_internal"] is True


@pytest.mark.integration
async def test_identifier_types_expose_the_456_person_anchors(client, api_key):
    """The pair usa-wa#256 must probe before minting archival-roster Persons.

    Both were hardcoded from prose because there was nothing to query — the
    failure mode a valid-but-wrong slug produces here is a duplicate *Person*.
    """
    response = await client.get(ENDPOINT, headers={"X-API-Key": api_key})
    by_slug = {r["slug"]: r for r in response.json()["data"]}
    for slug in ("person_wa_legislature_member_id", "person_wa_legislature_roster"):
        assert by_slug[slug]["entity_type"] == "person"
        assert by_slug[slug]["is_internal"] is False
    assert by_slug["person_wa_legislature_roster"]["display_name"] == "WA Legislature Roster"


@pytest.mark.integration
async def test_identifier_types_sorted_by_slug(client, api_key):
    response = await client.get(ENDPOINT, headers={"X-API-Key": api_key})
    slugs = [r["slug"] for r in response.json()["data"]]
    assert slugs == sorted(slugs)


def test_identifier_types_without_key_returns_403(unit_client):
    response = unit_client.get(ENDPOINT)
    assert response.status_code == 403


@pytest.mark.integration
async def test_identifier_types_with_invalid_key_returns_401(client):
    response = await client.get(ENDPOINT, headers={"X-API-Key": "pm_invalid"})
    assert response.status_code == 401


# ── conditional GET (#392) ────────────────────────────────────────────────────


@pytest.mark.integration
async def test_identifier_types_etag_round_trips_to_304(client, api_key):
    first = await client.get(ENDPOINT, headers={"X-API-Key": api_key})
    assert first.status_code == 200
    etag = first.headers["etag"]
    assert etag.startswith('"') and etag.endswith('"')

    r = await client.get(ENDPOINT, headers={"X-API-Key": api_key, "If-None-Match": etag})
    assert r.status_code == 304
    assert r.content == b""
    assert r.headers["cache-control"] == "no-cache"
    assert r.headers["vary"] == "X-API-Key"
    # A catalog with no updated_at column has no defensible Last-Modified.
    assert "last-modified" not in r.headers


@pytest.mark.integration
async def test_identifier_types_etag_changes_on_in_place_rename(client, db, api_key):
    """Why this catalog gets a content hash rather than a count+max watermark.

    `settings_identifier_types.py` is full admin CRUD — it UPDATEs slug and
    display_name in place. A count + max(created_at) tag would be *stable*
    across exactly that edit, and a 304ing consumer would hold a slug that no
    longer resolves.
    """
    before = (await client.get(ENDPOINT, headers={"X-API-Key": api_key})).headers["etag"]
    await db.execute(
        "UPDATE entity_identifier_types SET display_name = display_name || ' (renamed)'"
        " WHERE id = (SELECT id FROM entity_identifier_types ORDER BY slug LIMIT 1)"
    )
    after = await client.get(ENDPOINT, headers={"X-API-Key": api_key, "If-None-Match": before})
    assert after.status_code == 200, "in-place rename still revalidated as unchanged"
    assert after.headers["etag"] != before


@pytest.mark.integration
async def test_identifier_types_etag_changes_on_flag_flip(client, db, api_key):
    """`is_internal` decides whether a slug can mint an entity — a flip must invalidate."""
    before = (await client.get(ENDPOINT, headers={"X-API-Key": api_key})).headers["etag"]
    await db.execute(
        "UPDATE entity_identifier_types SET is_internal = NOT is_internal"
        " WHERE id = (SELECT id FROM entity_identifier_types ORDER BY slug LIMIT 1)"
    )
    after = await client.get(ENDPOINT, headers={"X-API-Key": api_key, "If-None-Match": before})
    assert after.status_code == 200


@pytest.mark.integration
async def test_identifier_types_etag_changes_on_row_add(client, db, api_key):
    before = (await client.get(ENDPOINT, headers={"X-API-Key": api_key})).headers["etag"]
    await db.execute(
        "INSERT INTO entity_identifier_types"
        " (id, entity_type, slug, display_name, full_name, is_internal)"
        " VALUES ($1,'person',$2,$3,$4,FALSE)",
        generate_id(),
        "zzz-conditional-get-probe",
        "Probe",
        "Conditional GET Probe",
    )
    after = await client.get(ENDPOINT, headers={"X-API-Key": api_key, "If-None-Match": before})
    assert after.status_code == 200
