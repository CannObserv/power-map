"""Integration tests for role merge on org detail page."""
import asyncio
import json
import os

import asyncpg
import pytest
from fastapi.testclient import TestClient

from src.api.main import app
from src.core.db import apply_schema, generate_id

pytestmark = pytest.mark.integration

AUTH_HEADERS = {
    "X-ExeDev-UserID": "usr_test",
    "X-ExeDev-Email": "admin@test.com",
}
HTMX_HEADERS = {**AUTH_HEADERS, "HX-Request": "true"}


def _get_dsn() -> str:
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        pytest.skip("DATABASE_URL not set")
    return dsn


async def _aconnect(dsn: str) -> asyncpg.Connection:
    conn = await asyncpg.connect(dsn)
    await apply_schema(conn)
    return conn


# ── Fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture
def client():
    """TestClient with default raise_server_exceptions=True for integration tests."""
    with TestClient(app) as c:
        yield c


@pytest.fixture
def role_pair():
    """Two roles on the same org, yield (org_id, role_a, role_b), teardown."""
    dsn = _get_dsn()
    org_id = generate_id()
    role_a = generate_id()
    role_b = generate_id()

    async def setup():
        conn = await _aconnect(dsn)
        try:
            await conn.execute("INSERT INTO organizations (id) VALUES ($1)", org_id)
            await conn.execute(
                "INSERT INTO organization_names (id, organization_id, name, is_canonical)"
                " VALUES ($1, $2, 'Test Org', TRUE)",
                generate_id(), org_id,
            )
            for rid, title in [(role_a, "Director"), (role_b, "Exec Director")]:
                await conn.execute(
                    "INSERT INTO roles (id, organization_id, title) VALUES ($1, $2, $3)",
                    rid, org_id, title,
                )
        finally:
            await conn.close()

    async def teardown():
        conn = await asyncpg.connect(dsn)
        try:
            await conn.execute(
                "DELETE FROM role_assignments WHERE role_id = ANY($1::text[])",
                [role_a, role_b],
            )
            await conn.execute(
                "DELETE FROM roles WHERE organization_id = $1", org_id,
            )
            await conn.execute(
                "DELETE FROM organization_names WHERE organization_id = $1", org_id,
            )
            await conn.execute("DELETE FROM organizations WHERE id = $1", org_id)
        finally:
            await conn.close()

    asyncio.run(setup())
    yield org_id, role_a, role_b
    asyncio.run(teardown())


@pytest.fixture
def role_pair_with_assignments():
    """Two roles with overlapping assignments (same person+start_date conflict)."""
    dsn = _get_dsn()
    org_id = generate_id()
    role_a = generate_id()
    role_b = generate_id()
    person_id = generate_id()
    unique_person_id = generate_id()

    async def setup():
        conn = await _aconnect(dsn)
        try:
            await conn.execute("INSERT INTO organizations (id) VALUES ($1)", org_id)
            await conn.execute(
                "INSERT INTO organization_names (id, organization_id, name, is_canonical)"
                " VALUES ($1, $2, 'Test Org', TRUE)",
                generate_id(), org_id,
            )
            for rid, title in [(role_a, "Director"), (role_b, "Exec Director")]:
                await conn.execute(
                    "INSERT INTO roles (id, organization_id, title) VALUES ($1, $2, $3)",
                    rid, org_id, title,
                )
            for pid in [person_id, unique_person_id]:
                await conn.execute("INSERT INTO people (id) VALUES ($1)", pid)
                await conn.execute(
                    "INSERT INTO person_names (id, person_id, name, is_canonical)"
                    " VALUES ($1, $2, 'Test Person', TRUE)",
                    generate_id(), pid,
                )
            # Shared person assigned to both roles with same start_date (conflict)
            await conn.execute(
                "INSERT INTO role_assignments (id, person_id, role_id, start_date)"
                " VALUES ($1, $2, $3, '2024-01-01')",
                generate_id(), person_id, role_a,
            )
            await conn.execute(
                "INSERT INTO role_assignments (id, person_id, role_id, start_date)"
                " VALUES ($1, $2, $3, '2024-01-01')",
                generate_id(), person_id, role_b,
            )
            # Unique person only on role_b (should be reassigned)
            await conn.execute(
                "INSERT INTO role_assignments (id, person_id, role_id, start_date)"
                " VALUES ($1, $2, $3, '2024-06-01')",
                generate_id(), unique_person_id, role_b,
            )
        finally:
            await conn.close()

    async def teardown():
        conn = await asyncpg.connect(dsn)
        try:
            await conn.execute(
                "DELETE FROM role_assignments WHERE role_id = ANY($1::text[])",
                [role_a, role_b],
            )
            await conn.execute(
                "DELETE FROM roles WHERE organization_id = $1", org_id,
            )
            for pid in [person_id, unique_person_id]:
                await conn.execute(
                    "DELETE FROM person_names WHERE person_id = $1", pid,
                )
                await conn.execute("DELETE FROM people WHERE id = $1", pid)
            await conn.execute(
                "DELETE FROM organization_names WHERE organization_id = $1", org_id,
            )
            await conn.execute("DELETE FROM organizations WHERE id = $1", org_id)
        finally:
            await conn.close()

    asyncio.run(setup())
    yield org_id, role_a, role_b, person_id, unique_person_id
    asyncio.run(teardown())


@pytest.fixture
def role_pair_with_notes():
    """Two roles where both have notes."""
    dsn = _get_dsn()
    org_id = generate_id()
    role_a = generate_id()
    role_b = generate_id()

    async def setup():
        conn = await _aconnect(dsn)
        try:
            await conn.execute("INSERT INTO organizations (id) VALUES ($1)", org_id)
            await conn.execute(
                "INSERT INTO organization_names (id, organization_id, name, is_canonical)"
                " VALUES ($1, $2, 'Test Org', TRUE)",
                generate_id(), org_id,
            )
            await conn.execute(
                "INSERT INTO roles (id, organization_id, title, notes)"
                " VALUES ($1, $2, 'Director', 'Winner notes')",
                role_a, org_id,
            )
            await conn.execute(
                "INSERT INTO roles (id, organization_id, title, notes)"
                " VALUES ($1, $2, 'Exec Director', 'Loser notes')",
                role_b, org_id,
            )
        finally:
            await conn.close()

    async def teardown():
        conn = await asyncpg.connect(dsn)
        try:
            await conn.execute(
                "DELETE FROM roles WHERE organization_id = $1", org_id,
            )
            await conn.execute(
                "DELETE FROM organization_names WHERE organization_id = $1", org_id,
            )
            await conn.execute("DELETE FROM organizations WHERE id = $1", org_id)
        finally:
            await conn.close()

    asyncio.run(setup())
    yield org_id, role_a, role_b
    asyncio.run(teardown())


# ── Merge: hard delete ──────────────────────────────────────────────────────


def test_merge_hard_deletes_loser(client, role_pair):
    org_id, role_a, role_b = role_pair
    response = client.post(
        f"/admin/orgs/{org_id}/roles/{role_a}/merge/{role_b}/",
        headers=AUTH_HEADERS,
        follow_redirects=False,
    )
    assert response.status_code == 303

    async def check():
        conn = await asyncpg.connect(_get_dsn())
        try:
            return await conn.fetchrow("SELECT id FROM roles WHERE id=$1", role_b)
        finally:
            await conn.close()

    assert asyncio.run(check()) is None


# ── Merge: role_assignments ─────────────────────────────────────────────────


def test_merge_deletes_conflicting_assignments(client, role_pair_with_assignments):
    org_id, role_a, role_b, person_id, _ = role_pair_with_assignments
    client.post(
        f"/admin/orgs/{org_id}/roles/{role_a}/merge/{role_b}/",
        headers=AUTH_HEADERS,
        follow_redirects=False,
    )

    async def check():
        conn = await asyncpg.connect(_get_dsn())
        try:
            return await conn.fetch(
                "SELECT id FROM role_assignments WHERE person_id=$1 AND role_id=$2",
                person_id, role_a,
            )
        finally:
            await conn.close()

    rows = asyncio.run(check())
    assert len(rows) == 1  # only winner's original assignment remains


def test_merge_reassigns_unique_assignments(client, role_pair_with_assignments):
    org_id, role_a, role_b, _, unique_person_id = role_pair_with_assignments
    client.post(
        f"/admin/orgs/{org_id}/roles/{role_a}/merge/{role_b}/",
        headers=AUTH_HEADERS,
        follow_redirects=False,
    )

    async def check():
        conn = await asyncpg.connect(_get_dsn())
        try:
            return await conn.fetchrow(
                "SELECT id FROM role_assignments WHERE person_id=$1 AND role_id=$2",
                unique_person_id, role_a,
            )
        finally:
            await conn.close()

    assert asyncio.run(check()) is not None


# ── Merge: notes ────────────────────────────────────────────────────────────


def test_merge_appends_loser_notes_to_winner(client, role_pair_with_notes):
    org_id, role_a, role_b = role_pair_with_notes
    client.post(
        f"/admin/orgs/{org_id}/roles/{role_a}/merge/{role_b}/",
        headers=AUTH_HEADERS,
        follow_redirects=False,
    )

    async def check():
        conn = await asyncpg.connect(_get_dsn())
        try:
            return await conn.fetchval("SELECT notes FROM roles WHERE id=$1", role_a)
        finally:
            await conn.close()

    notes = asyncio.run(check())
    assert "Winner notes" in notes
    assert "Loser notes" in notes
    assert "Merged from" in notes


def test_merge_skips_notes_when_loser_has_none(client, role_pair):
    org_id, role_a, role_b = role_pair
    client.post(
        f"/admin/orgs/{org_id}/roles/{role_a}/merge/{role_b}/",
        headers=AUTH_HEADERS,
        follow_redirects=False,
    )

    async def check():
        conn = await asyncpg.connect(_get_dsn())
        try:
            return await conn.fetchval("SELECT notes FROM roles WHERE id=$1", role_a)
        finally:
            await conn.close()

    assert asyncio.run(check()) is None


# ── Merge: provenance survives ──────────────────────────────────────────────


def test_merge_preserves_assignment_provenance(client, role_pair_with_assignments):
    """Provenance tracks assignment IDs, not role IDs — merge must not break them."""
    org_id, role_a, role_b, _, unique_person_id = role_pair_with_assignments
    dsn = _get_dsn()

    # Get the assignment ID that will be reassigned (unique_person on role_b)
    async def get_assignment_id():
        conn = await asyncpg.connect(dsn)
        try:
            return await conn.fetchval(
                "SELECT id FROM role_assignments WHERE person_id=$1 AND role_id=$2",
                unique_person_id, role_b,
            )
        finally:
            await conn.close()

    assignment_id = asyncio.run(get_assignment_id())

    client.post(
        f"/admin/orgs/{org_id}/roles/{role_a}/merge/{role_b}/",
        headers=AUTH_HEADERS,
        follow_redirects=False,
    )

    # Assignment still exists with same ID, now pointing to winner role
    async def check():
        conn = await asyncpg.connect(dsn)
        try:
            return await conn.fetchrow(
                "SELECT id, role_id FROM role_assignments WHERE id=$1",
                assignment_id,
            )
        finally:
            await conn.close()

    row = asyncio.run(check())
    assert row is not None
    assert row["role_id"] == role_a


# ── Merge: guards ───────────────────────────────────────────────────────────


def test_merge_returns_404_for_unknown_role(client, role_pair):
    org_id, role_a, _ = role_pair
    response = client.post(
        f"/admin/orgs/{org_id}/roles/{role_a}/merge/nonexistent-id/",
        headers=AUTH_HEADERS,
        follow_redirects=False,
    )
    assert response.status_code == 404


def test_merge_returns_409_for_archived_role(client, role_pair):
    org_id, role_a, role_b = role_pair
    dsn = _get_dsn()

    async def archive():
        conn = await asyncpg.connect(dsn)
        try:
            await conn.execute(
                "UPDATE roles SET archived_at = NOW() WHERE id = $1", role_b,
            )
        finally:
            await conn.close()

    asyncio.run(archive())

    response = client.post(
        f"/admin/orgs/{org_id}/roles/{role_a}/merge/{role_b}/",
        headers=AUTH_HEADERS,
        follow_redirects=False,
    )
    assert response.status_code == 409


def test_merge_returns_409_for_cross_org_roles(client, role_pair):
    org_id, role_a, role_b = role_pair
    dsn = _get_dsn()

    other_org_id = generate_id()
    other_role_id = generate_id()

    async def setup():
        conn = await asyncpg.connect(dsn)
        try:
            await conn.execute(
                "INSERT INTO organizations (id) VALUES ($1)", other_org_id,
            )
            await conn.execute(
                "INSERT INTO organization_names (id, organization_id, name, is_canonical)"
                " VALUES ($1, $2, 'Other Org', TRUE)",
                generate_id(), other_org_id,
            )
            await conn.execute(
                "INSERT INTO roles (id, organization_id, title) VALUES ($1, $2, 'Other Role')",
                other_role_id, other_org_id,
            )
        finally:
            await conn.close()

    asyncio.run(setup())

    response = client.post(
        f"/admin/orgs/{org_id}/roles/{role_a}/merge/{other_role_id}/",
        headers=AUTH_HEADERS,
        follow_redirects=False,
    )
    assert response.status_code == 409

    # cleanup
    async def cleanup():
        conn = await asyncpg.connect(dsn)
        try:
            await conn.execute("DELETE FROM roles WHERE id=$1", other_role_id)
            await conn.execute(
                "DELETE FROM organization_names WHERE organization_id=$1",
                other_org_id,
            )
            await conn.execute(
                "DELETE FROM organizations WHERE id=$1", other_org_id,
            )
        finally:
            await conn.close()

    asyncio.run(cleanup())


# ── Merge: HTMX response ───────────────────────────────────────────────────


def test_merge_htmx_returns_200_with_tbody(client, role_pair):
    org_id, role_a, role_b = role_pair
    response = client.post(
        f"/admin/orgs/{org_id}/roles/{role_a}/merge/{role_b}/",
        headers=HTMX_HEADERS,
        follow_redirects=False,
    )
    assert response.status_code == 200
    # Should contain the remaining role in tbody
    assert "Director" in response.text


def test_merge_htmx_sends_flash_trigger(client, role_pair):
    org_id, role_a, role_b = role_pair
    response = client.post(
        f"/admin/orgs/{org_id}/roles/{role_a}/merge/{role_b}/",
        headers=HTMX_HEADERS,
        follow_redirects=False,
    )
    assert response.status_code == 200
    assert "HX-Trigger" in response.headers
    payload = json.loads(response.headers["HX-Trigger"])
    assert payload["showFlash"]["level"] == "success"
    assert "Exec Director" in payload["showFlash"]["body"]


def test_merge_non_htmx_redirects_to_org_detail(client, role_pair):
    org_id, role_a, role_b = role_pair
    response = client.post(
        f"/admin/orgs/{org_id}/roles/{role_a}/merge/{role_b}/",
        headers=AUTH_HEADERS,
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"] == f"/admin/orgs/{org_id}/"


