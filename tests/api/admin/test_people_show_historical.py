"""Phase 2a Task 3 — person-detail `?show_historical=1` disclosure toggle.

The detail page hides legal_only / hidden rows by default; an explicit
toggle reveals them. This mirrors the visibility rule documented in
CONVENTIONS.md §"Person names — i18n & cultural awareness".
"""

import asyncio
import os

import asyncpg
import pytest
from fastapi.testclient import TestClient

from src.api.main import app
from src.core.db import apply_schema, generate_id

pytestmark = pytest.mark.integration

AUTH_HEADERS = {"X-ExeDev-UserID": "usr_test", "X-ExeDev-Email": "admin@test.com"}


def _dsn() -> str:
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        pytest.skip("DATABASE_URL not set")
    return dsn


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture
def person_with_mixed_visibility():
    """Person with one public, one legal_only, and one hidden name."""
    dsn = _dsn()
    pid = generate_id()
    public_id = generate_id()
    legal_only_id = generate_id()
    hidden_id = generate_id()

    async def setup():
        conn = await asyncpg.connect(dsn)
        await apply_schema(conn)
        try:
            await conn.execute("INSERT INTO people (id) VALUES ($1)", pid)
            await conn.execute(
                "INSERT INTO person_names"
                " (id, person_id, name, name_type, is_canonical, visibility)"
                " VALUES ($1, $2, 'Public Canon', 'legal', TRUE, 'public')",
                public_id, pid,
            )
            await conn.execute(
                "INSERT INTO person_names"
                " (id, person_id, name, name_type, is_canonical, visibility)"
                " VALUES ($1, $2, 'Legal Only Alt', 'former', FALSE, 'legal_only')",
                legal_only_id, pid,
            )
            await conn.execute(
                "INSERT INTO person_names"
                " (id, person_id, name, name_type, is_canonical, visibility)"
                " VALUES ($1, $2, 'Hidden Alias', 'alias', FALSE, 'hidden')",
                hidden_id, pid,
            )
        finally:
            await conn.close()

    async def teardown():
        conn = await asyncpg.connect(dsn)
        try:
            await conn.execute("DELETE FROM person_names WHERE person_id = $1", pid)
            await conn.execute("DELETE FROM people WHERE id = $1", pid)
        finally:
            await conn.close()

    asyncio.run(setup())
    yield pid
    asyncio.run(teardown())


@pytest.fixture
def person_public_only():
    """Person with only public-visibility names — toggle should not appear."""
    dsn = _dsn()
    pid = generate_id()

    async def setup():
        conn = await asyncpg.connect(dsn)
        await apply_schema(conn)
        try:
            await conn.execute("INSERT INTO people (id) VALUES ($1)", pid)
            await conn.execute(
                "INSERT INTO person_names"
                " (id, person_id, name, name_type, is_canonical, visibility)"
                " VALUES ($1, $2, 'Visible Only', 'legal', TRUE, 'public')",
                generate_id(), pid,
            )
        finally:
            await conn.close()

    async def teardown():
        conn = await asyncpg.connect(dsn)
        try:
            await conn.execute("DELETE FROM person_names WHERE person_id = $1", pid)
            await conn.execute("DELETE FROM people WHERE id = $1", pid)
        finally:
            await conn.close()

    asyncio.run(setup())
    yield pid
    asyncio.run(teardown())


# ---- default behaviour: historical hidden --------------------------------


def test_default_hides_legal_only_and_hidden_rows(client, person_with_mixed_visibility):
    pid = person_with_mixed_visibility
    r = client.get(f"/admin/people/{pid}/", headers=AUTH_HEADERS)
    assert r.status_code == 200
    assert "Public Canon" in r.text
    assert "Legal Only Alt" not in r.text
    assert "Hidden Alias" not in r.text


# ---- ?show_historical=1: full disclosure ---------------------------------


def test_show_historical_renders_all_rows(client, person_with_mixed_visibility):
    pid = person_with_mixed_visibility
    r = client.get(
        f"/admin/people/{pid}/?show_historical=1",
        headers=AUTH_HEADERS,
    )
    assert r.status_code == 200
    assert "Public Canon" in r.text
    assert "Legal Only Alt" in r.text
    assert "Hidden Alias" in r.text


# ---- toggle affordance ---------------------------------------------------


def test_toggle_link_visible_when_historical_rows_exist_and_off(
    client, person_with_mixed_visibility,
):
    pid = person_with_mixed_visibility
    r = client.get(f"/admin/people/{pid}/", headers=AUTH_HEADERS)
    assert r.status_code == 200
    # "Show" link present, count of hidden rows surfaced.
    assert "Show legal/historical names" in r.text
    assert "show_historical=1" in r.text


def test_toggle_link_visible_when_historical_rows_exist_and_on(
    client, person_with_mixed_visibility,
):
    pid = person_with_mixed_visibility
    r = client.get(
        f"/admin/people/{pid}/?show_historical=1",
        headers=AUTH_HEADERS,
    )
    assert r.status_code == 200
    # "Hide" link should now be present.
    assert "Hide legal/historical names" in r.text


def test_toggle_link_absent_when_no_historical_rows(client, person_public_only):
    """No legal_only/hidden names → no point in showing the toggle."""
    pid = person_public_only
    r = client.get(f"/admin/people/{pid}/", headers=AUTH_HEADERS)
    assert r.status_code == 200
    assert "Show legal/historical names" not in r.text
    assert "Hide legal/historical names" not in r.text


def test_toggle_link_carries_count_of_hidden_rows(client, person_with_mixed_visibility):
    """Toggle should surface the count so admins know what's behind it."""
    pid = person_with_mixed_visibility
    r = client.get(f"/admin/people/{pid}/", headers=AUTH_HEADERS)
    # Two hidden rows: legal_only + hidden.
    assert "(2)" in r.text or "2 hidden" in r.text or "2 historical" in r.text
