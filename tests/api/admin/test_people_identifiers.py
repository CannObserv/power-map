"""Integration tests for person identifiers CRUD (parity with test_orgs_identifiers.py)."""

import asyncio
import json
import os

import asyncpg
import pytest
from fastapi.testclient import TestClient

from src.api.main import app
from src.core.db import apply_schema, generate_id

pytestmark = pytest.mark.integration
AUTH_HEADERS = {"X-ExeDev-UserID": "usr_test", "X-ExeDev-Email": "admin@test.com"}
HTMX_HEADERS = {**AUTH_HEADERS, "HX-Request": "true"}


def _dsn():
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        pytest.skip("DATABASE_URL not set")
    return dsn


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture
def person_id_and_type():
    """Yields (person_id, identifier_type_id) for a person with an identifier type seeded."""
    dsn = _dsn()
    pid = generate_id()

    async def setup():
        conn = await asyncpg.connect(dsn)
        await apply_schema(conn)
        try:
            await conn.execute("INSERT INTO people (id) VALUES ($1)", pid)
        finally:
            await conn.close()

    async def teardown():
        conn = await asyncpg.connect(dsn)
        try:
            await conn.execute("DELETE FROM identifiers WHERE entity_id=$1", pid)
            await conn.execute("DELETE FROM people WHERE id=$1", pid)
        finally:
            await conn.close()

    asyncio.run(setup())

    async def get_type_id():
        conn = await asyncpg.connect(dsn)
        try:
            row = await conn.fetchrow(
                "SELECT id FROM entity_identifier_types WHERE entity_type='person' LIMIT 1"
            )
            if not row:
                pytest.skip("No person identifier types seeded")
            return row["id"]
        finally:
            await conn.close()

    type_id = asyncio.run(get_type_id())
    yield pid, type_id
    asyncio.run(teardown())


@pytest.fixture
def person_and_identifier(person_id_and_type):
    dsn = _dsn()
    pid, type_id = person_id_and_type
    iid = generate_id()

    async def setup():
        conn = await asyncpg.connect(dsn)
        await apply_schema(conn)
        try:
            await conn.execute(
                "INSERT INTO identifiers (id, entity_id, entity_identifier_type_id, value)"
                " VALUES ($1, $2, $3, 'TEST-123')",
                iid,
                pid,
                type_id,
            )
        finally:
            await conn.close()

    async def teardown():
        conn = await asyncpg.connect(dsn)
        try:
            await conn.execute("DELETE FROM identifiers WHERE id=$1", iid)
        finally:
            await conn.close()

    asyncio.run(setup())
    yield pid, iid, type_id
    asyncio.run(teardown())


def test_identifier_form_row_has_form_group(client, person_id_and_type):
    pid, _ = person_id_and_type
    r = client.get(f"/admin/people/{pid}/identifiers/new-row/", headers=HTMX_HEADERS)
    assert r.status_code == 200
    assert "form-group" in r.text


def test_identifiers_new_row_returns_form(client, person_id_and_type):
    pid, _ = person_id_and_type
    r = client.get(f"/admin/people/{pid}/identifiers/new-row/", headers=HTMX_HEADERS)
    assert r.status_code == 200
    assert "<form" in r.text


def test_identifiers_create(client, person_id_and_type):
    pid, type_id = person_id_and_type
    r = client.post(
        f"/admin/people/{pid}/identifiers/",
        headers=HTMX_HEADERS,
        data={"entity_identifier_type_id": type_id, "value": "UBI-999"},
    )
    assert r.status_code == 200
    assert "UBI-999" in r.text


def test_identifiers_read_row_returns_row(client, person_and_identifier):
    pid, iid, _ = person_and_identifier
    r = client.get(f"/admin/people/{pid}/identifiers/{iid}/read-row/", headers=HTMX_HEADERS)
    assert r.status_code == 200
    assert "TEST-123" in r.text
    assert "<form" not in r.text


def test_identifiers_edit_row_returns_form(client, person_and_identifier):
    pid, iid, _ = person_and_identifier
    r = client.get(f"/admin/people/{pid}/identifiers/{iid}/edit-row/", headers=HTMX_HEADERS)
    assert r.status_code == 200
    assert "<form" in r.text


def test_identifiers_update(client, person_and_identifier):
    pid, iid, type_id = person_and_identifier
    r = client.post(
        f"/admin/people/{pid}/identifiers/{iid}/edit-row/",
        headers=HTMX_HEADERS,
        data={"entity_identifier_type_id": type_id, "value": "UBI-456"},
    )
    assert r.status_code == 200
    assert "UBI-456" in r.text


def test_identifiers_delete(client, person_and_identifier):
    pid, iid, _ = person_and_identifier
    r = client.delete(f"/admin/people/{pid}/identifiers/{iid}/", headers=HTMX_HEADERS)
    assert r.status_code == 200


def test_identifiers_delete_unknown_returns_404(client, person_id_and_type):
    pid, _ = person_id_and_type
    r = client.delete(f"/admin/people/{pid}/identifiers/{generate_id()}/", headers=HTMX_HEADERS)
    assert r.status_code == 404


def test_identifiers_create_returns_success_flash(client, person_id_and_type):
    pid, type_id = person_id_and_type
    r = client.post(
        f"/admin/people/{pid}/identifiers/",
        headers=HTMX_HEADERS,
        data={"entity_identifier_type_id": type_id, "value": "FLASH-001"},
    )
    assert r.status_code == 200
    trigger = json.loads(r.headers["hx-trigger"])
    assert trigger["showFlash"]["level"] == "success"
    assert "FLASH-001" in trigger["showFlash"]["body"]


def test_identifiers_update_returns_success_flash(client, person_and_identifier):
    pid, iid, type_id = person_and_identifier
    r = client.post(
        f"/admin/people/{pid}/identifiers/{iid}/edit-row/",
        headers=HTMX_HEADERS,
        data={"entity_identifier_type_id": type_id, "value": "FLASH-002"},
    )
    assert r.status_code == 200
    trigger = json.loads(r.headers["hx-trigger"])
    assert trigger["showFlash"]["level"] == "success"
    assert "FLASH-002" in trigger["showFlash"]["body"]


def test_identifiers_delete_returns_info_flash(client, person_and_identifier):
    pid, iid, _ = person_and_identifier
    r = client.delete(f"/admin/people/{pid}/identifiers/{iid}/", headers=HTMX_HEADERS)
    assert r.status_code == 200
    trigger = json.loads(r.headers["hx-trigger"])
    assert trigger["showFlash"]["level"] == "info"
