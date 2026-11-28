"""Org kinds on the public API (#877 phase 3c, design D9).

``org_kind_slug`` and ``org_kind_source`` ride on every org read (``getOrg``,
``batchGetOrgs``, ``searchOrgs``): the resolved kind, and who decided it in
vendor-neutral terms (``rules``, ``ai`` or ``operator``). Both are null while
undecided, after a dismiss and after a clear. ``GET /api/v1/org-kinds`` is the
catalog: slug, display name and description, with a conditional GET.
"""

import hashlib
import os

import pytest
import pytest_asyncio

from src.core import classification as cls
from src.core.db import generate_id
from tests.db_utils import drop_committed_key

ORG_KINDS = "/api/v1/org-kinds"


async def _key(db, email: str) -> tuple[str, str, str]:
    uid, kid = generate_id(), generate_id()
    raw = "pm_" + os.urandom(16).hex()
    await db.execute("INSERT INTO app_users (id, email) VALUES ($1,$2)", uid, email)
    await db.execute(
        "INSERT INTO api_keys (id, user_id, label, key_prefix, key_hash) VALUES ($1,$2,$3,$4,$5)",
        kid,
        uid,
        "Org Kind Test Key",
        raw[:8],
        hashlib.sha256(raw.encode()).hexdigest(),
    )
    return raw, kid, uid


@pytest_asyncio.fixture(loop_scope="session")
async def api_key(db):
    raw, _, _ = await _key(db, "orgkindtest@test.com")
    return raw


async def _org(db, name: str) -> str:
    oid = generate_id()
    await db.execute("INSERT INTO organizations (id) VALUES ($1)", oid)
    await db.execute(
        "INSERT INTO organization_names (id, organization_id, name, is_canonical)"
        " VALUES ($1, $2, $3, TRUE)",
        generate_id(),
        oid,
        name,
    )
    return oid


async def _suggest(db, org_id, source, value, confidence=None):
    await db.execute(
        "INSERT INTO classification_suggestions"
        " (id, entity_type, entity_id, field, source, value, model, confidence)"
        " VALUES ($1,'organization',$2,'org_kind',$3,$4,$5,$6)",
        generate_id(),
        org_id,
        source,
        value,
        cls.RULES_VERSION if source == "rules" else "jev-1.14.0",
        confidence,
    )


def _kind(body: dict) -> tuple:
    return body["org_kind_slug"], body["org_kind_source"]


# --- the catalog -----------------------------------------------------------


def test_org_kinds_without_a_key_is_403(unit_client):
    assert unit_client.get(ORG_KINDS).status_code == 403


@pytest.mark.integration
async def test_org_kinds_lists_every_kind_by_slug(client, db, api_key):
    r = await client.get(ORG_KINDS, headers={"X-API-Key": api_key})
    assert r.status_code == 200
    body = r.json()
    assert "meta" not in body
    rows = await db.fetch("SELECT slug, display_name, description FROM org_kinds ORDER BY slug")
    assert body["data"] == [dict(row) for row in rows]
    assert {"business", "government", "legislature"} <= {k["slug"] for k in body["data"]}


@pytest.mark.integration
async def test_org_kinds_revalidates_with_its_etag(client, api_key):
    first = await client.get(ORG_KINDS, headers={"X-API-Key": api_key})
    etag = first.headers["etag"]
    again = await client.get(ORG_KINDS, headers={"X-API-Key": api_key, "If-None-Match": etag})
    assert again.status_code == 304


# --- the org reads ---------------------------------------------------------


@pytest.mark.integration
async def test_get_org_carries_the_kind_and_who_decided_it(client, db, api_key):
    undecided, by_rules, by_ai, by_operator, dismissed, cleared = [
        await _org(db, f"Kind Wire Org {n}") for n in range(6)
    ]
    await _suggest(db, by_rules, "rules", "legislature")
    await cls.record_decision(
        db, org_id=by_rules, action="accept", kind="legislature", source="rules"
    )
    await _suggest(db, by_ai, "jev", "government", 0.9)
    await cls.record_decision(db, org_id=by_ai, action="accept", kind="government", source="jev")
    await cls.record_decision(db, org_id=by_operator, action="set", kind="tribe")
    await cls.record_decision(db, org_id=dismissed, action="dismiss")
    await cls.record_decision(db, org_id=cleared, action="set", kind="business")
    await cls.record_decision(db, org_id=cleared, action="clear")

    got = {}
    for org in (undecided, by_rules, by_ai, by_operator, dismissed, cleared):
        r = await client.get(f"/api/v1/orgs/{org}", headers={"X-API-Key": api_key})
        assert r.status_code == 200
        got[org] = _kind(r.json())

    assert got == {
        undecided: (None, None),
        by_rules: ("legislature", "rules"),
        by_ai: ("government", "ai"),
        by_operator: ("tribe", "operator"),
        dismissed: (None, None),
        cleared: (None, None),
    }


@pytest.mark.integration
async def test_batch_get_and_search_carry_it_too(client, db, api_key):
    org = await _org(db, "Kind Wire Searchable Zebrafinch")
    await cls.record_decision(db, org_id=org, action="set", kind="nonprofit")

    r = await client.post(
        "/api/v1/orgs/batch-get", json={"ids": [org]}, headers={"X-API-Key": api_key}
    )
    assert _kind(r.json()["results"][0]["org"]) == ("nonprofit", "operator")

    r = await client.get(
        "/api/v1/orgs/search", params={"q": "Zebrafinch"}, headers={"X-API-Key": api_key}
    )
    (hit,) = [o for o in r.json()["data"] if o["id"] == org]
    assert _kind(hit) == ("nonprofit", "operator")


@pytest.mark.integration
async def test_a_kind_decision_changes_the_org_etag(
    committing_client, committing_db, committed_entities
):
    db = committing_db
    raw, kid, uid = await _key(db, "orgkindetag@test.com")
    try:
        org = await _org(db, "Kind Wire ETag Org")
        committed_entities.append(("organization", org))
        headers = {"X-API-Key": raw}
        before = await committing_client.get(f"/api/v1/orgs/{org}", headers=headers)

        await cls.record_decision(db, org_id=org, action="set", kind="business")

        after = await committing_client.get(
            f"/api/v1/orgs/{org}", headers={**headers, "If-None-Match": before.headers["etag"]}
        )
        assert after.status_code == 200
        assert after.headers["etag"] != before.headers["etag"]
        assert _kind(after.json()) == ("business", "operator")

        # Same kind, new source: only org_kind_source changes, and the ETag
        # still moves, or a cached reader would keep the old source on a 304.
        await _suggest(db, org, "rules", "business")
        await cls.record_decision(db, org_id=org, action="accept", kind="business", source="rules")
        again = await committing_client.get(
            f"/api/v1/orgs/{org}", headers={**headers, "If-None-Match": after.headers["etag"]}
        )
        assert again.status_code == 200
        assert _kind(again.json()) == ("business", "rules")
    finally:
        await drop_committed_key(db, kid, uid)
