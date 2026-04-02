"""Integration tests for people duplicate detection and dismiss routes."""
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


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture
def person_pair():
    """Insert two near-duplicate people (id_a < id_b), yield (id_a, id_b), teardown."""
    dsn = _get_dsn()
    id_a, id_b = generate_id(), generate_id()
    if id_a > id_b:
        id_a, id_b = id_b, id_a

    async def setup():
        conn = await _aconnect(dsn)
        try:
            for pid, name in [
                (id_a, "Jonathan Smithfield"),
                (id_b, "Jonathan Smithfield Jr"),   # deliberate near-match (similarity ~0.91)
            ]:
                await conn.execute("INSERT INTO people (id) VALUES ($1)", pid)
                await conn.execute(
                    "INSERT INTO person_names"
                    " (id, person_id, name, is_canonical)"
                    " VALUES ($1, $2, $3, TRUE)",
                    generate_id(), pid, name,
                )
        finally:
            await conn.close()

    async def teardown():
        conn = await asyncpg.connect(dsn)
        try:
            await conn.execute(
                "DELETE FROM duplicate_dismissals"
                " WHERE entity_a_id=$1 OR entity_b_id=$1"
                " OR entity_a_id=$2 OR entity_b_id=$2",
                id_a, id_b,
            )
            for pid in [id_a, id_b]:
                await conn.execute(
                    "DELETE FROM person_names WHERE person_id=$1", pid
                )
                await conn.execute("DELETE FROM people WHERE id=$1", pid)
        finally:
            await conn.close()

    asyncio.run(setup())
    yield id_a, id_b
    asyncio.run(teardown())


# ── List screen ─────────────────────────────────────────────────────────────

def test_people_list_sidebar_badge_visible(client, person_pair):
    """Sidebar shows Duplicates link with count when person_dup_count > 0."""
    response = client.get("/admin/people/", headers=AUTH_HEADERS)
    assert response.status_code == 200
    assert "Duplicates" in response.text
    # Badge count in the sidebar link text
    assert "(" in response.text  # crude check: count badge present


def test_people_list_shows_duplicate_banner(client, person_pair):
    response = client.get("/admin/people/", headers=AUTH_HEADERS)
    assert response.status_code == 200
    assert "possible duplicate" in response.text.lower()


# ── Duplicates review screen ─────────────────────────────────────────────────

def test_duplicates_list_returns_200(client, person_pair):
    response = client.get("/admin/people/duplicates/", headers=AUTH_HEADERS)
    assert response.status_code == 200
    assert "duplicate" in response.text.lower()


def test_duplicates_list_shows_pair(client, person_pair):
    response = client.get("/admin/people/duplicates/", headers=AUTH_HEADERS)
    assert "Jonathan Smithfield" in response.text


# ── Dismiss ──────────────────────────────────────────────────────────────────

def test_dismiss_pair_removes_from_list(client, person_pair):
    id_a, id_b = person_pair
    response = client.post(
        f"/admin/people/{id_a}/dismiss-duplicate/{id_b}/",
        headers=AUTH_HEADERS,
        follow_redirects=False,
    )
    assert response.status_code in (302, 303)
    response2 = client.get("/admin/people/duplicates/", headers=AUTH_HEADERS)
    assert response2.status_code == 200
    # Both person links reference id_a or id_b — check neither ID appears
    assert id_a not in response2.text and id_b not in response2.text


def test_dismiss_htmx_returns_200_with_region(client, person_pair):
    id_a, id_b = person_pair
    response = client.post(
        f"/admin/people/{id_a}/dismiss-duplicate/{id_b}/",
        headers=HTMX_HEADERS,
        follow_redirects=False,
    )
    assert response.status_code == 200
    assert "candidate" in response.text or "No duplicate" in response.text


def test_dismiss_htmx_sends_hx_trigger_flash(client, person_pair):
    id_a, id_b = person_pair
    response = client.post(
        f"/admin/people/{id_a}/dismiss-duplicate/{id_b}/",
        headers=HTMX_HEADERS,
        follow_redirects=False,
    )
    assert response.status_code == 200
    assert "HX-Trigger" in response.headers
    payload = json.loads(response.headers["HX-Trigger"])
    assert payload["showFlash"]["level"] == "info"
    assert "hx-swap-oob" not in response.text
