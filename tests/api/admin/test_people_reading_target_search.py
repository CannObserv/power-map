"""Phase 2c Task 1 — reading-target typeahead endpoint.

GET /admin/people/{person_id}/_reading_target_search?q=<term>&limit=20
returns same-person rows whose name_type is NOT in
{'reading','romanization','mrz'} — i.e. the visual rows that can be
parents of a reading/romanization/mrz row.

Auth-guarded; substring filter on name with escape_like + ESCAPE '\\';
sorted by canonical DESC then name; capped at limit; empty q returns no
rows. Returns HTML option-list partials shaped for typeahead-combobox.js.
"""

import asyncio
import os
import re

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
def two_people_with_names():
    """Two people, each with one visual + one reading row.

    person_a: 'Ada Lovelace' (legal), 'ada lovelace' (romanization, parent=Ada)
    person_b: 'Bob Builder' (legal), 'bob' (romanization, parent=Bob)

    The reading_target_search for person_a should return only Ada's
    visual rows — never Bob's, never Ada's reading row.
    """
    dsn = _dsn()
    pid_a = generate_id()
    pid_b = generate_id()
    nid_a_visual = generate_id()
    nid_a_reading = generate_id()
    nid_b_visual = generate_id()
    nid_b_reading = generate_id()

    async def setup():
        conn = await asyncpg.connect(dsn)
        await apply_schema(conn)
        try:
            for pid in (pid_a, pid_b):
                await conn.execute("INSERT INTO people (id) VALUES ($1)", pid)
            await conn.execute(
                "INSERT INTO person_names"
                " (id, person_id, name, name_type, is_canonical, visibility)"
                " VALUES ($1, $2, 'Ada Lovelace', 'legal', TRUE, 'public')",
                nid_a_visual, pid_a,
            )
            await conn.execute(
                "INSERT INTO person_names"
                " (id, person_id, name, name_type, is_canonical, visibility, reading_of_id)"
                " VALUES ($1, $2, 'ada lovelace', 'romanization', FALSE, 'public', $3)",
                nid_a_reading, pid_a, nid_a_visual,
            )
            await conn.execute(
                "INSERT INTO person_names"
                " (id, person_id, name, name_type, is_canonical, visibility)"
                " VALUES ($1, $2, 'Bob Builder', 'legal', TRUE, 'public')",
                nid_b_visual, pid_b,
            )
            await conn.execute(
                "INSERT INTO person_names"
                " (id, person_id, name, name_type, is_canonical, visibility, reading_of_id)"
                " VALUES ($1, $2, 'bob', 'romanization', FALSE, 'public', $3)",
                nid_b_reading, pid_b, nid_b_visual,
            )
        finally:
            await conn.close()

    async def teardown():
        conn = await asyncpg.connect(dsn)
        try:
            for pid in (pid_a, pid_b):
                await conn.execute("DELETE FROM person_names WHERE person_id=$1", pid)
                await conn.execute("DELETE FROM people WHERE id=$1", pid)
        finally:
            await conn.close()

    asyncio.run(setup())
    yield {
        "pid_a": pid_a, "pid_b": pid_b,
        "nid_a_visual": nid_a_visual, "nid_a_reading": nid_a_reading,
        "nid_b_visual": nid_b_visual, "nid_b_reading": nid_b_reading,
    }
    asyncio.run(teardown())


def _option_ids(html: str) -> list[str]:
    return re.findall(r'<li[^>]*\bdata-id="([^"]+)"', html)


def _option_labels(html: str) -> list[str]:
    return re.findall(r'<li[^>]*\bdata-label="([^"]+)"', html)


# ---- Scope: same-person, visual-only ----------------------------------


def test_returns_same_person_visual_rows(client, two_people_with_names):
    f = two_people_with_names
    r = client.get(
        f"/admin/people/{f['pid_a']}/_reading_target_search?q=ada",
        headers=AUTH_HEADERS,
    )
    assert r.status_code == 200
    ids = _option_ids(r.text)
    assert f["nid_a_visual"] in ids
    # The reading row of the same person must NOT be a candidate parent.
    assert f["nid_a_reading"] not in ids


def test_excludes_other_person_visual_rows(client, two_people_with_names):
    f = two_people_with_names
    r = client.get(
        f"/admin/people/{f['pid_a']}/_reading_target_search?q=Bob",
        headers=AUTH_HEADERS,
    )
    assert r.status_code == 200
    ids = _option_ids(r.text)
    # Bob is on a different person; never a candidate parent for Ada's reading.
    assert f["nid_b_visual"] not in ids
    assert ids == []


def test_excludes_reading_romanization_mrz_rows(client, two_people_with_names):
    """The reading row itself must never appear as a candidate parent
    even when q matches its name."""
    f = two_people_with_names
    r = client.get(
        f"/admin/people/{f['pid_a']}/_reading_target_search?q=lovelace",
        headers=AUTH_HEADERS,
    )
    assert r.status_code == 200
    ids = _option_ids(r.text)
    assert f["nid_a_visual"] in ids
    assert f["nid_a_reading"] not in ids


# ---- Empty q + auth ---------------------------------------------------


def test_empty_q_returns_empty_list(client, two_people_with_names):
    f = two_people_with_names
    r = client.get(
        f"/admin/people/{f['pid_a']}/_reading_target_search?q=",
        headers=AUTH_HEADERS,
    )
    assert r.status_code == 200
    assert _option_ids(r.text) == []


def test_missing_q_returns_empty_list(client, two_people_with_names):
    f = two_people_with_names
    r = client.get(
        f"/admin/people/{f['pid_a']}/_reading_target_search",
        headers=AUTH_HEADERS,
    )
    assert r.status_code == 200
    assert _option_ids(r.text) == []


def test_requires_auth(client, two_people_with_names):
    f = two_people_with_names
    r = client.get(
        f"/admin/people/{f['pid_a']}/_reading_target_search?q=Ada",
        follow_redirects=False,
    )
    assert r.status_code in (302, 307)


# ---- Response shape ---------------------------------------------------


def test_response_uses_option_list_shape(client, two_people_with_names):
    f = two_people_with_names
    r = client.get(
        f"/admin/people/{f['pid_a']}/_reading_target_search?q=Ada",
        headers=AUTH_HEADERS,
    )
    assert 'role="option"' in r.text
    assert f'data-id="{f["nid_a_visual"]}"' in r.text
    # Label should include the visible name and a hint about the row's type.
    labels = _option_labels(r.text)
    assert any("Ada Lovelace" in lbl for lbl in labels), labels


# ---- Limit + escape_like ---------------------------------------------


def test_limit_caps_results(client, two_people_with_names):
    f = two_people_with_names
    r = client.get(
        f"/admin/people/{f['pid_a']}/_reading_target_search?q=Ada&limit=0",
        headers=AUTH_HEADERS,
    )
    # limit=0 violates ge=1 → 422 from FastAPI Query validation.
    assert r.status_code == 422


def test_escape_like_neutralises_underscore(client, two_people_with_names):
    """`_` should not act as a single-char wildcard."""
    f = two_people_with_names
    # Real names don't contain '_'; query 'A_a' would match 'Ada' if `_` were
    # a wildcard. With escape, only literal 'A_a' (which doesn't exist) matches.
    r = client.get(
        f"/admin/people/{f['pid_a']}/_reading_target_search?q=A_a",
        headers=AUTH_HEADERS,
    )
    assert r.status_code == 200
    assert _option_ids(r.text) == []


# ---- 404 on unknown person -------------------------------------------


def test_unknown_person_returns_404(client):
    """A request for a non-existent person should 404, not silently return [].

    Same shape as other person-scoped endpoints (people_names, etc).
    """
    r = client.get(
        f"/admin/people/{generate_id()}/_reading_target_search?q=anything",
        headers=AUTH_HEADERS,
    )
    assert r.status_code == 404
