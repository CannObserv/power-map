"""Endpoint tests for the public citation provenance API (#319).

POST /api/v1/citations/{entity_type}/{entity_id}/observations (partial-success)
GET  /api/v1/citations/{entity_type}/{entity_id}
"""

import hashlib
import os

import pytest
import pytest_asyncio

from src.core.db import generate_id

pytestmark = [pytest.mark.integration]


async def _key_with_scopes(db, scopes: list[str]) -> str:
    uid, kid = generate_id(), generate_id()
    raw = "pm_" + os.urandom(16).hex()
    await db.execute("INSERT INTO app_users (id, email) VALUES ($1,$2)", uid, f"{kid}@test.com")
    await db.execute(
        "INSERT INTO api_keys (id, user_id, label, key_prefix, key_hash) VALUES ($1,$2,$3,$4,$5)",
        kid,
        uid,
        "cite key",
        raw[:8],
        hashlib.sha256(raw.encode()).hexdigest(),
    )
    for s in scopes:
        await db.execute("INSERT INTO api_key_scopes (api_key_id, scope_id) VALUES ($1,$2)", kid, s)
    return raw


@pytest_asyncio.fixture(loop_scope="session")
async def write_key(db) -> str:
    return await _key_with_scopes(db, ["citations:write"])


@pytest_asyncio.fixture(loop_scope="session")
async def read_key(db) -> str:
    return await _key_with_scopes(db, ["citations:read"])


@pytest_asyncio.fixture(loop_scope="session")
async def person(db) -> str:
    pid = generate_id()
    await db.execute("INSERT INTO people (id) VALUES ($1)", pid)
    return pid


def _base(entity_type: str, entity_id: str) -> str:
    return f"/api/v1/citations/{entity_type}/{entity_id}"


# ── write ─────────────────────────────────────────────────────────────────────


async def test_observe_new_and_read_back(client, db, write_key, read_key, person):
    r = await client.post(
        f"{_base('person', person)}/observations",
        headers={"X-API-Key": write_key},
        json={"citations": [{"field_name": "notes", "url": "https://s/a", "title": "A"}]},
    )
    assert r.status_code == 200, r.text
    result = r.json()["results"][0]
    assert result["disposition"] == "new"
    cid = result["citation_id"]

    g = await client.get(_base("person", person), headers={"X-API-Key": read_key})
    assert g.status_code == 200
    body = g.json()
    assert body["meta"]["count"] == 1
    row = body["data"][0]
    assert row["id"] == cid
    assert row["url"] == "https://s/a"
    assert row["field_name"] == "notes"


async def test_partial_success(client, write_key, person):
    r = await client.post(
        f"{_base('person', person)}/observations",
        headers={"X-API-Key": write_key},
        json={
            "citations": [
                {"field_name": "notes", "url": "https://s/ok"},
                {"field_name": "bogus_field", "url": "https://s/bad"},
            ]
        },
    )
    assert r.status_code == 200
    results = r.json()["results"]
    assert results[0]["disposition"] == "new"
    assert results[1]["disposition"] == "rejected"
    assert results[1]["reason"] == "citable_field_unknown"


async def test_retract(client, db, write_key, read_key, person):
    r = await client.post(
        f"{_base('person', person)}/observations",
        headers={"X-API-Key": write_key},
        json={"citations": [{"field_name": "notes", "url": "https://s/x", "title": "t"}]},
    )
    cid = r.json()["results"][0]["citation_id"]
    r2 = await client.post(
        f"{_base('person', person)}/observations",
        headers={"X-API-Key": write_key},
        json={"citations": [{"op": "retract", "pm_citation_id": cid}]},
    )
    assert r2.json()["results"][0]["disposition"] == "retracted"

    # Default read excludes archived; include_archived surfaces it.
    g = await client.get(_base("person", person), headers={"X-API-Key": read_key})
    assert g.json()["meta"]["count"] == 0
    g2 = await client.get(
        _base("person", person),
        headers={"X-API-Key": read_key},
        params={"include_archived": "true"},
    )
    assert g2.json()["meta"]["count"] == 1
    assert g2.json()["data"][0]["archived_at"] is not None


async def test_entity_unresolved(client, write_key):
    r = await client.post(
        f"{_base('person', generate_id())}/observations",
        headers={"X-API-Key": write_key},
        json={"citations": [{"field_name": "notes", "url": "https://s/x"}]},
    )
    assert r.status_code == 200
    assert r.json()["results"][0]["reason"] == "entity_unresolved"


async def test_field_name_filter(client, write_key, read_key, person):
    await client.post(
        f"{_base('person', person)}/observations",
        headers={"X-API-Key": write_key},
        json={
            "citations": [
                {"field_name": "notes", "url": "https://s/n"},
                {"url": "https://s/whole"},  # whole-entity
            ]
        },
    )
    g = await client.get(
        _base("person", person), headers={"X-API-Key": read_key}, params={"field_name": "notes"}
    )
    data = g.json()["data"]
    assert len(data) == 1
    assert data[0]["field_name"] == "notes"


# ── auth / validation ─────────────────────────────────────────────────────────


async def test_write_requires_scope(client, read_key, person):
    r = await client.post(
        f"{_base('person', person)}/observations",
        headers={"X-API-Key": read_key},  # read-only key
        json={"citations": [{"field_name": "notes", "url": "https://s/x"}]},
    )
    assert r.status_code == 403


async def test_read_requires_scope(client, write_key, person):
    r = await client.get(_base("person", person), headers={"X-API-Key": write_key})
    assert r.status_code == 403


async def test_unknown_entity_type_422(client, write_key):
    r = await client.post(
        f"{_base('widget', generate_id())}/observations",
        headers={"X-API-Key": write_key},
        json={"citations": [{"url": "https://s/x"}]},
    )
    assert r.status_code == 422


# ── embedded transport (citations[] on a person observation) ──────────────────


@pytest_asyncio.fixture(loop_scope="session")
async def obs_key(db) -> str:
    return await _key_with_scopes(db, ["observations:write"])


async def test_embedded_citation_on_person_observation(client, obs_key):
    r = await client.post(
        "/api/v1/people/observations",
        headers={"X-API-Key": obs_key},
        json={
            "identifier_type": "person_wa_pdc",
            "identifier_value": generate_id(),
            "citations": [{"field_name": "notes", "url": "https://s/embed", "title": "E"}],
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["disposition"] in ("new", "auto-attached")
    assert body["citations"][0]["disposition"] == "new"
    assert body["citations"][0]["citation_id"]


async def test_embedded_bad_citation_rejects_whole_observation(client, obs_key):
    r = await client.post(
        "/api/v1/people/observations",
        headers={"X-API-Key": obs_key},
        json={
            "identifier_type": "person_wa_pdc",
            "identifier_value": generate_id(),
            "citations": [{"field_name": "bogus_field", "url": "https://s/x"}],
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert body["disposition"] == "rejected"
    assert body["reason"] == "citable_field_unknown"


# ── conditional GET (#392) ────────────────────────────────────────────────────


async def _cite(client, write_key, person, url: str, field: str | None = "notes") -> str:
    r = await client.post(
        f"{_base('person', person)}/observations",
        headers={"X-API-Key": write_key},
        json={"citations": [{"field_name": field, "url": url, "title": "T"}]},
    )
    assert r.status_code == 200, r.text
    return r.json()["results"][0]["citation_id"]


async def test_citations_etag_round_trips_to_304(client, read_key, person):
    url = _base("person", person)
    first = await client.get(url, headers={"X-API-Key": read_key})
    assert first.status_code == 200
    etag = first.headers["etag"]
    assert etag.startswith('"') and etag.endswith('"')

    r = await client.get(url, headers={"X-API-Key": read_key, "If-None-Match": etag})
    assert r.status_code == 304
    assert r.content == b""
    assert r.headers["cache-control"] == "no-cache"
    assert r.headers["vary"] == "X-API-Key"


async def test_citations_empty_collection_revalidates_without_last_modified(
    client, read_key, person
):
    """The 99.9%-empty poll must be 304-able; nothing was ever modified."""
    url = _base("person", person)
    first = await client.get(url, headers={"X-API-Key": read_key})
    assert first.status_code == 200
    assert "last-modified" not in first.headers
    r = await client.get(
        url, headers={"X-API-Key": read_key, "If-None-Match": first.headers["etag"]}
    )
    assert r.status_code == 304


async def test_citations_etag_changes_when_a_citation_is_added(client, write_key, read_key, person):
    url = _base("person", person)
    before = (await client.get(url, headers={"X-API-Key": read_key})).headers["etag"]
    await _cite(client, write_key, person, "https://s/new")
    after = await client.get(url, headers={"X-API-Key": read_key, "If-None-Match": before})
    assert after.status_code == 200
    assert after.headers["etag"] != before


async def test_citations_etag_changes_when_a_citation_is_retracted(
    client, write_key, read_key, person
):
    """A retract archives rather than deletes — the count over the *filtered*
    (active-only) set is what moves, so the default view must not 304 through it."""
    cid = await _cite(client, write_key, person, "https://s/gone")
    url = _base("person", person)
    before = (await client.get(url, headers={"X-API-Key": read_key})).headers["etag"]

    r = await client.post(
        f"{url}/observations",
        headers={"X-API-Key": write_key},
        json={"citations": [{"op": "retract", "pm_citation_id": cid}]},
    )
    assert r.status_code == 200, r.text

    after = await client.get(url, headers={"X-API-Key": read_key, "If-None-Match": before})
    assert after.status_code == 200, "retracted citation still revalidated as unchanged"


async def test_citations_etag_is_per_filter_and_per_window(client, write_key, read_key, person):
    """Every param that changes the body is baked into the tag."""
    await _cite(client, write_key, person, "https://s/a", field="notes")
    base = _base("person", person)

    async def tag(query: str) -> str:
        r = await client.get(f"{base}{query}", headers={"X-API-Key": read_key})
        assert r.status_code == 200
        return r.headers["etag"]

    plain = await tag("")
    assert await tag("?field_name=notes") != plain
    assert await tag("?include_archived=true") != plain
    assert await tag("?limit=5") != plain
    assert await tag("?offset=1") != plain


async def test_citations_etag_does_not_cross_entities(client, write_key, read_key, person, db):
    other = generate_id()
    await db.execute("INSERT INTO people (id) VALUES ($1)", other)
    a = (await client.get(_base("person", person), headers={"X-API-Key": read_key})).headers["etag"]
    b = (await client.get(_base("person", other), headers={"X-API-Key": read_key})).headers["etag"]
    assert a != b
