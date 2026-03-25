"""Integration tests for org names CRUD."""

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
def org_and_name():
    dsn = _dsn()
    oid, nid = generate_id(), generate_id()

    async def setup():
        conn = await asyncpg.connect(dsn)
        await apply_schema(conn)
        try:
            await conn.execute("INSERT INTO organizations (id) VALUES ($1)", oid)
            await conn.execute(
                "INSERT INTO organization_names (id, organization_id, name, is_canonical)"
                " VALUES ($1, $2, 'Original Name', TRUE)",
                nid,
                oid,
            )
        finally:
            await conn.close()

    async def teardown():
        conn = await asyncpg.connect(dsn)
        try:
            await conn.execute("DELETE FROM organization_names WHERE organization_id=$1", oid)
            await conn.execute("DELETE FROM organizations WHERE id=$1", oid)
        finally:
            await conn.close()

    asyncio.run(setup())
    yield oid, nid
    asyncio.run(teardown())


def test_names_new_row_returns_form(client, org_and_name):
    oid, _ = org_and_name
    r = client.get(f"/admin/orgs/{oid}/names/new-row/", headers=HTMX_HEADERS)
    assert r.status_code == 200
    assert "<form" in r.text


def test_names_create(client, org_and_name):
    oid, _ = org_and_name
    r = client.post(
        f"/admin/orgs/{oid}/names/",
        headers=HTMX_HEADERS,
        data={"name": "DBA Name", "name_type": "dba", "is_canonical": ""},
    )
    assert r.status_code == 200
    assert "DBA Name" in r.text


def test_names_read_row_returns_row(client, org_and_name):
    oid, nid = org_and_name
    r = client.get(f"/admin/orgs/{oid}/names/{nid}/read-row/", headers=HTMX_HEADERS)
    assert r.status_code == 200
    assert "Original Name" in r.text
    assert "<form" not in r.text


def test_names_edit_row_returns_form(client, org_and_name):
    oid, nid = org_and_name
    r = client.get(f"/admin/orgs/{oid}/names/{nid}/edit-row/", headers=HTMX_HEADERS)
    assert r.status_code == 200
    assert "Original Name" in r.text


def test_names_update(client, org_and_name):
    oid, nid = org_and_name
    r = client.post(
        f"/admin/orgs/{oid}/names/{nid}/edit-row/",
        headers=HTMX_HEADERS,
        data={"name": "Updated Name", "name_type": "legal", "is_canonical": "true"},
    )
    assert r.status_code == 200
    assert "Updated Name" in r.text


def test_names_delete(client, org_and_name):
    dsn = _dsn()
    oid, _ = org_and_name
    nid2 = generate_id()

    async def add():
        conn = await asyncpg.connect(dsn)
        await apply_schema(conn)
        try:
            await conn.execute(
                "INSERT INTO organization_names (id, organization_id, name, is_canonical)"
                " VALUES ($1, $2, 'Former Name', FALSE)",
                nid2,
                oid,
            )
        finally:
            await conn.close()

    asyncio.run(add())
    r = client.delete(f"/admin/orgs/{oid}/names/{nid2}/", headers=HTMX_HEADERS)
    assert r.status_code == 200


def test_names_delete_unknown_returns_404(client, org_and_name):
    oid, _ = org_and_name
    r = client.delete(f"/admin/orgs/{oid}/names/{generate_id()}/", headers=HTMX_HEADERS)
    assert r.status_code == 404


def test_names_create_demotes_existing_canonical(client, org_and_name):
    """Creating a canonical name must demote the current canonical."""
    oid, nid = org_and_name
    r = client.post(
        f"/admin/orgs/{oid}/names/",
        headers=HTMX_HEADERS,
        data={"name": "New Canonical", "name_type": "legal", "is_canonical": "true"},
    )
    assert r.status_code == 200
    dsn = _dsn()

    async def check():
        conn = await asyncpg.connect(dsn)
        await apply_schema(conn)
        try:
            original = await conn.fetchrow(
                "SELECT is_canonical FROM organization_names WHERE id=$1", nid
            )
            return original["is_canonical"]
        finally:
            await conn.close()

    assert asyncio.run(check()) is False, "original canonical must be demoted"


def test_names_update_promotes_and_demotes(client, org_and_name):
    """Editing a non-canonical name to canonical must demote the existing one."""
    oid, canonical_nid = org_and_name
    dsn = _dsn()
    other_nid = generate_id()

    async def add_non_canonical():
        conn = await asyncpg.connect(dsn)
        await apply_schema(conn)
        try:
            await conn.execute(
                "INSERT INTO organization_names (id, organization_id, name, is_canonical)"
                " VALUES ($1, $2, 'Former Name', FALSE)",
                other_nid,
                oid,
            )
        finally:
            await conn.close()

    asyncio.run(add_non_canonical())
    r = client.post(
        f"/admin/orgs/{oid}/names/{other_nid}/edit-row/",
        headers=HTMX_HEADERS,
        data={"name": "Former Name", "name_type": "former", "is_canonical": "true"},
    )
    assert r.status_code == 200

    async def check():
        conn = await asyncpg.connect(dsn)
        try:
            orig = await conn.fetchrow(
                "SELECT is_canonical FROM organization_names WHERE id=$1", canonical_nid
            )
            promoted = await conn.fetchrow(
                "SELECT is_canonical FROM organization_names WHERE id=$1", other_nid
            )
            return orig["is_canonical"], promoted["is_canonical"]
        finally:
            await conn.close()

    orig_canonical, new_canonical = asyncio.run(check())
    assert orig_canonical is False, "old canonical must be demoted"
    assert new_canonical is True, "edited row must be promoted"


def test_names_edit_returns_tbody(client, org_and_name):
    """Edit response must return all rows (tbody innerHTML), not just the edited row."""
    oid, nid = org_and_name
    dsn = _dsn()
    nid2 = generate_id()

    async def add_second():
        conn = await asyncpg.connect(dsn)
        await apply_schema(conn)
        try:
            await conn.execute(
                "INSERT INTO organization_names (id, organization_id, name, is_canonical)"
                " VALUES ($1, $2, 'Second Name', FALSE)",
                nid2, oid,
            )
        finally:
            await conn.close()

    asyncio.run(add_second())
    r = client.post(
        f"/admin/orgs/{oid}/names/{nid}/edit-row/",
        headers=HTMX_HEADERS,
        data={"name": "Original Name", "name_type": "legal", "is_canonical": "true"},
    )
    assert r.status_code == 200
    # Both rows must be present — only a full tbody response includes both
    assert f'id="name-row-{nid}"' in r.text
    assert f'id="name-row-{nid2}"' in r.text
    assert "<table" not in r.text  # not the full table


def test_names_form_row_canonical_toggle_has_aria_label(client, org_and_name):
    oid, _ = org_and_name
    r = client.get(f"/admin/orgs/{oid}/names/new-row/", headers=HTMX_HEADERS)
    assert r.status_code == 200
    assert 'aria-label="Canonical"' in r.text


def test_names_create_returns_success_flash(client, org_and_name):
    oid, _ = org_and_name
    r = client.post(
        f"/admin/orgs/{oid}/names/",
        headers=HTMX_HEADERS,
        data={"name": "Flash Test Name", "name_type": "dba", "is_canonical": ""},
    )
    assert r.status_code == 200
    trigger = json.loads(r.headers["hx-trigger"])
    assert trigger["showFlash"]["level"] == "success"
    assert "Flash Test Name" in trigger["showFlash"]["body"]


def test_names_update_returns_success_flash(client, org_and_name):
    oid, nid = org_and_name
    r = client.post(
        f"/admin/orgs/{oid}/names/{nid}/edit-row/",
        headers=HTMX_HEADERS,
        data={"name": "Updated Name", "name_type": "legal", "is_canonical": "true"},
    )
    assert r.status_code == 200
    trigger = json.loads(r.headers["hx-trigger"])
    assert trigger["showFlash"]["level"] == "success"
    assert "Updated Name" in trigger["showFlash"]["body"]


def test_names_delete_returns_info_flash(client, org_and_name):
    dsn = _dsn()
    oid, _ = org_and_name
    nid2 = generate_id()

    async def add():
        conn = await asyncpg.connect(dsn)
        await apply_schema(conn)
        try:
            await conn.execute(
                "INSERT INTO organization_names (id, organization_id, name, is_canonical)"
                " VALUES ($1, $2, 'Delete Me', FALSE)",
                nid2,
                oid,
            )
        finally:
            await conn.close()

    asyncio.run(add())
    r = client.delete(f"/admin/orgs/{oid}/names/{nid2}/", headers=HTMX_HEADERS)
    assert r.status_code == 200
    trigger = json.loads(r.headers["hx-trigger"])
    assert trigger["showFlash"]["level"] == "info"
