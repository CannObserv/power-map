"""Tests for GET /api/v1/person-name-types and /organization-name-types (#857).

The name-type catalogs serve the registry in ``src.core.name_types``: each
type's meaning, when to use it, and how power-map treats it. No table backs
them, so the DB-backed cases are integration only because API-key auth is.
"""

import hashlib
import os

import pytest
import pytest_asyncio

from src.core.db import generate_id
from src.core.name_types import ORG_NAME_TYPE_SPECS, PERSON_NAME_TYPE_SPECS
from src.core.types import ORG_NAME_TYPES, PERSON_NAME_TYPES

PERSON = "/api/v1/person-name-types"
ORG = "/api/v1/organization-name-types"

_PROSE = {"slug", "label", "meaning", "use_when", "not_for", "example"}
_PERSON_FLAGS = {
    "display_rank",
    "auto_promote",
    "never_canonical",
    "landing_visibility",
    "renders_row",
    "merge_distinct",
    "decomposable",
    "presentable",
}


@pytest_asyncio.fixture(loop_scope="session")
async def api_key(db):
    """Insert a test app_user + api_key; return raw_key (rolled back per test)."""
    uid = generate_id()
    kid = generate_id()
    raw_key = "pm_" + os.urandom(16).hex()
    key_hash = hashlib.sha256(raw_key.encode()).hexdigest()
    await db.execute("INSERT INTO app_users (id, email) VALUES ($1,$2)", uid, "nametypes@test.com")
    await db.execute(
        "INSERT INTO api_keys (id, user_id, label, key_prefix, key_hash) VALUES ($1,$2,$3,$4,$5)",
        kid,
        uid,
        "Name Type Test Key",
        raw_key[:8],
        key_hash,
    )
    return raw_key


@pytest.mark.integration
async def test_person_name_types_lists_every_type_in_registry_order(client, api_key):
    response = await client.get(PERSON, headers={"X-API-Key": api_key})
    assert response.status_code == 200
    body = response.json()
    assert "meta" not in body  # unpaginated catalog, like its siblings
    assert tuple(r["slug"] for r in body["data"]) == PERSON_NAME_TYPES


@pytest.mark.integration
async def test_person_name_type_items_carry_prose_and_flags(client, api_key):
    response = await client.get(PERSON, headers={"X-API-Key": api_key})
    for item in response.json()["data"]:
        assert set(item) == _PROSE | _PERSON_FLAGS


@pytest.mark.integration
async def test_person_name_types_serve_the_registry_values(client, api_key):
    response = await client.get(PERSON, headers={"X-API-Key": api_key})
    served = {r["slug"]: r for r in response.json()["data"]}
    for spec in PERSON_NAME_TYPE_SPECS:
        item = served[spec.slug]
        for field in _PROSE | _PERSON_FLAGS:
            assert item[field] == getattr(spec, field), f"{spec.slug}.{field}"


@pytest.mark.integration
async def test_misspelling_is_served_as_a_never_presented_search_key(client, api_key):
    response = await client.get(PERSON, headers={"X-API-Key": api_key})
    item = {r["slug"]: r for r in response.json()["data"]}["misspelling"]
    assert item["presentable"] is False
    assert item["never_canonical"] is True
    assert item["landing_visibility"] == "public"
    assert item["display_rank"] is None


@pytest.mark.integration
async def test_organization_name_types_list_prose_only(client, api_key):
    response = await client.get(ORG, headers={"X-API-Key": api_key})
    assert response.status_code == 200
    data = response.json()["data"]
    assert tuple(r["slug"] for r in data) == ORG_NAME_TYPES
    for item, spec in zip(data, ORG_NAME_TYPE_SPECS, strict=True):
        assert set(item) == _PROSE
        assert item["meaning"] == spec.meaning


@pytest.mark.parametrize("endpoint", [PERSON, ORG])
def test_name_types_without_key_returns_403(unit_client, endpoint):
    assert unit_client.get(endpoint).status_code == 403


@pytest.mark.integration
@pytest.mark.parametrize("endpoint", [PERSON, ORG])
async def test_name_types_with_invalid_key_returns_401(client, endpoint):
    response = await client.get(endpoint, headers={"X-API-Key": "pm_invalid"})
    assert response.status_code == 401


@pytest.mark.integration
@pytest.mark.parametrize("endpoint", [PERSON, ORG])
async def test_name_types_etag_round_trips_to_304(client, api_key, endpoint):
    first = await client.get(endpoint, headers={"X-API-Key": api_key})
    etag = first.headers["etag"]
    r = await client.get(endpoint, headers={"X-API-Key": api_key, "If-None-Match": etag})
    assert r.status_code == 304
    assert r.content == b""
    assert "last-modified" not in r.headers
