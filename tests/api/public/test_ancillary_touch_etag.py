"""An ancillary write still changes its parent's detail ETag (#651 Keep, #327, #392).

The touch-parent triggers outlived the change outbox they used to feed: they
bump the parent's ``updated_at``, and every detail ETag is keyed on that column
(``make_etag``). This suite drives one write to each touch-triggered
ancillary table through raw SQL — the path an app-layer bump would miss — and
reads the parent's ETag back over the public API.

Committing variants (#288): ``now()`` is frozen inside one transaction, so the
rollback client could never see ``updated_at`` advance. Rows leak but carry
unique ULIDs and are cleared by the session-start truncation.
"""

import hashlib
import os
from collections.abc import Awaitable, Callable

import pytest
import pytest_asyncio

from src.core.db import generate_id

pytestmark = pytest.mark.integration


@pytest_asyncio.fixture(loop_scope="session")
async def db(committing_db):
    return committing_db


@pytest_asyncio.fixture(loop_scope="session")
async def client(committing_client):
    return committing_client


@pytest_asyncio.fixture(loop_scope="session")
async def api_key(db):
    uid, kid = generate_id(), generate_id()
    raw_key = "pm_" + os.urandom(16).hex()
    await db.execute(
        "INSERT INTO app_users (id, email) VALUES ($1,$2)", uid, f"etag-{uid}@test.com"
    )
    await db.execute(
        "INSERT INTO api_keys (id, user_id, label, key_prefix, key_hash) VALUES ($1,$2,$3,$4,$5)",
        kid,
        uid,
        "Ancillary ETag Key",
        raw_key[:8],
        hashlib.sha256(raw_key.encode()).hexdigest(),
    )
    yield raw_key
    await db.execute("DELETE FROM api_keys WHERE id=$1", kid)
    await db.execute("DELETE FROM app_users WHERE id=$1", uid)


# ── parents: entity_type → (create, detail path) ─────────────────────────────


async def _org(db) -> str:
    oid = generate_id()
    await db.execute("INSERT INTO organizations (id) VALUES ($1)", oid)
    return oid


async def _person(db) -> str:
    pid = generate_id()
    await db.execute("INSERT INTO people (id) VALUES ($1)", pid)
    return pid


async def _role(db) -> str:
    rid = generate_id()
    await db.execute(
        "INSERT INTO roles (id, organization_id, title) VALUES ($1,$2,'Director')",
        rid,
        await _org(db),
    )
    return rid


async def _assignment(db) -> str:
    raid = generate_id()
    await db.execute(
        "INSERT INTO role_assignments (id, person_id, role_id, is_current) VALUES ($1,$2,$3,TRUE)",
        raid,
        await _person(db),
        await _role(db),
    )
    return raid


async def _jurisdiction(db) -> str:
    jid = generate_id()
    type_id = await db.fetchval("SELECT id FROM jurisdiction_types WHERE slug='state'")
    await db.execute(
        "INSERT INTO jurisdictions (id, slug, name, type_id) VALUES ($1,$2,$3,$4)",
        jid,
        f"etag-{jid}",
        f"ETag Probe {jid}",
        type_id,
    )
    return jid


_PARENTS: dict[str, tuple[Callable[..., Awaitable[str]], str]] = {
    "organization": (_org, "/api/v1/orgs/{}"),
    "person": (_person, "/api/v1/people/{}"),
    "role": (_role, "/api/v1/roles/{}"),
    "role_assignment": (_assignment, "/api/v1/assignments/{}"),
    "jurisdiction": (_jurisdiction, "/api/v1/jurisdictions/{}"),
}


# ── ancillary writes: one INSERT per touch-triggered table ───────────────────


async def _link(db, entity_type: str, entity_id: str) -> None:
    lt_id = await db.fetchval("SELECT id FROM link_types WHERE slug='website'")
    await db.execute(
        "INSERT INTO links (id, entity_type, entity_id, url, link_type_id) VALUES ($1,$2,$3,$4,$5)",
        generate_id(),
        entity_type,
        entity_id,
        f"https://example.gov/{entity_id}",
        lt_id,
    )


async def _contact(db, entity_type: str, entity_id: str) -> None:
    await db.execute(
        "INSERT INTO contact_methods (id, entity_type, entity_id, contact_type, value)"
        " VALUES ($1,$2,$3,'email',$4)",
        generate_id(),
        entity_type,
        entity_id,
        f"{entity_id.lower()}@example.gov",
    )


async def _identifier(db, entity_type: str, entity_id: str) -> None:
    type_id = await db.fetchval(
        "SELECT id FROM entity_identifier_types WHERE entity_type=$1 ORDER BY slug LIMIT 1",
        entity_type,
    )
    await db.execute(
        "INSERT INTO identifiers (id, entity_id, entity_identifier_type_id, value)"
        " VALUES ($1,$2,$3,$4)",
        generate_id(),
        entity_id,
        type_id,
        f"ETAG-{entity_id}",
    )


async def _address(db, entity_type: str, entity_id: str) -> None:
    aid = generate_id()
    await db.execute(
        "INSERT INTO addresses (id, address_line_1, city, region, postal_code, country)"
        " VALUES ($1,'123 Main St','Olympia','WA','98501','US')",
        aid,
    )
    await db.execute(
        "INSERT INTO entity_addresses (id, entity_type, entity_id, address_id, address_type)"
        " VALUES ($1,$2,$3,$4,'mailing')",
        generate_id(),
        entity_type,
        entity_id,
        aid,
    )


async def _citation(db, entity_type: str, entity_id: str) -> None:
    await db.execute(
        "INSERT INTO citations (id, entity_type, entity_id, url, title) VALUES ($1,$2,$3,$4,'t')",
        generate_id(),
        entity_type,
        entity_id,
        f"https://example.gov/src/{entity_id}",
    )


_ANCILLARY = {
    "links": _link,
    "contact_methods": _contact,
    "identifiers": _identifier,
    "entity_addresses": _address,
    "citations": _citation,
}

# Every touch-triggered ancillary table, on every parent its trigger dispatches
# to. A pair the schema rejects (a CHECK on entity_type) is not listed.
_CASES = [
    ("links", "organization"),
    ("links", "person"),
    ("links", "role"),
    ("links", "role_assignment"),
    ("links", "jurisdiction"),
    ("contact_methods", "organization"),
    ("contact_methods", "person"),
    ("contact_methods", "role_assignment"),
    ("identifiers", "organization"),
    ("identifiers", "person"),
    ("identifiers", "role_assignment"),
    ("identifiers", "jurisdiction"),
    ("entity_addresses", "organization"),
    ("entity_addresses", "person"),
    ("citations", "organization"),
    ("citations", "person"),
    ("citations", "role"),
    ("citations", "role_assignment"),
]


@pytest.mark.parametrize(("table", "entity_type"), _CASES)
async def test_ancillary_insert_changes_parent_etag(client, db, api_key, table, entity_type):
    create, path = _PARENTS[entity_type]
    entity_id = await create(db)
    url = path.format(entity_id)
    headers = {"X-API-Key": api_key}

    r1 = await client.get(url, headers=headers)
    assert r1.status_code == 200, r1.text
    etag1 = r1.headers["etag"]

    await db.execute("SELECT pg_sleep(0.002)")
    await _ANCILLARY[table](db, entity_type, entity_id)

    r2 = await client.get(url, headers=headers)
    assert r2.headers["etag"] != etag1, f"{table} write left the {entity_type} ETag unchanged"

    r3 = await client.get(url, headers={**headers, "If-None-Match": etag1})
    assert r3.status_code == 200
