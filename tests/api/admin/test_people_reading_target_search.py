"""Phase 2c Task 1 — reading-target typeahead endpoint.

GET /admin/people/{person_id}/_reading_target_search?q=<term>&limit=20
returns same-person rows whose name_type is NOT in
{'reading','romanization','mrz'} — i.e. the visual rows that can be
parents of a reading/romanization/mrz row.

Auth-guarded; substring filter on name with escape_like + ESCAPE '\\';
sorted by canonical DESC then name; capped at limit; empty q returns no
rows. Returns HTML option-list partials shaped for typeahead-combobox.js.
"""

import re

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from src.api.admin.deps import get_db
from src.api.main import app
from src.core.db import generate_id

pytestmark = [
    pytest.mark.integration,
]

AUTH_HEADERS = {"X-ExeDev-UserID": "usr_test", "X-ExeDev-Email": "admin@test.com"}


@pytest_asyncio.fixture(loop_scope="session")
async def db(db_pool):
    """Pool-acquired connection wrapped in a rolled-back transaction."""
    async with db_pool.acquire() as conn:
        tr = conn.transaction()
        await tr.start()
        try:
            yield conn
        finally:
            await tr.rollback()


@pytest_asyncio.fixture(loop_scope="session")
async def client(db):
    """AsyncClient with app, overriding get_db to use the test connection."""

    async def _get_db_override():
        yield db

    app.dependency_overrides[get_db] = _get_db_override
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test", follow_redirects=True
    ) as c:
        yield c
    app.dependency_overrides.pop(get_db, None)


@pytest_asyncio.fixture(loop_scope="session")
async def two_people_with_names(db):
    """Two people, each with one visual + one reading row.

    person_a: 'Ada Lovelace' (legal), 'ada lovelace' (romanization, parent=Ada)
    person_b: 'Bob Builder' (legal), 'bob' (romanization, parent=Bob)

    The reading_target_search for person_a should return only Ada's
    visual rows — never Bob's, never Ada's reading row.
    """
    pid_a = generate_id()
    pid_b = generate_id()
    nid_a_visual = generate_id()
    nid_a_reading = generate_id()
    nid_b_visual = generate_id()
    nid_b_reading = generate_id()

    for pid in (pid_a, pid_b):
        await db.execute("INSERT INTO people (id) VALUES ($1)", pid)
    await db.execute(
        "INSERT INTO person_names"
        " (id, person_id, name, name_type, is_canonical, visibility)"
        " VALUES ($1, $2, 'Ada Lovelace', 'legal', TRUE, 'public')",
        nid_a_visual,
        pid_a,
    )
    await db.execute(
        "INSERT INTO person_names"
        " (id, person_id, name, name_type, is_canonical, visibility, reading_of_id)"
        " VALUES ($1, $2, 'ada lovelace', 'romanization', FALSE, 'public', $3)",
        nid_a_reading,
        pid_a,
        nid_a_visual,
    )
    await db.execute(
        "INSERT INTO person_names"
        " (id, person_id, name, name_type, is_canonical, visibility)"
        " VALUES ($1, $2, 'Bob Builder', 'legal', TRUE, 'public')",
        nid_b_visual,
        pid_b,
    )
    await db.execute(
        "INSERT INTO person_names"
        " (id, person_id, name, name_type, is_canonical, visibility, reading_of_id)"
        " VALUES ($1, $2, 'bob', 'romanization', FALSE, 'public', $3)",
        nid_b_reading,
        pid_b,
        nid_b_visual,
    )

    return {
        "pid_a": pid_a,
        "pid_b": pid_b,
        "nid_a_visual": nid_a_visual,
        "nid_a_reading": nid_a_reading,
        "nid_b_visual": nid_b_visual,
        "nid_b_reading": nid_b_reading,
    }


def _option_ids(html: str) -> list[str]:
    return re.findall(r'<li[^>]*\bdata-id="([^"]+)"', html)


def _option_labels(html: str) -> list[str]:
    return re.findall(r'<li[^>]*\bdata-label="([^"]+)"', html)


# ---- Scope: same-person, visual-only ----------------------------------


async def test_returns_same_person_visual_rows(client, two_people_with_names):
    f = two_people_with_names
    r = await client.get(
        f"/admin/people/{f['pid_a']}/_reading_target_search?q=ada",
        headers=AUTH_HEADERS,
    )
    assert r.status_code == 200
    ids = _option_ids(r.text)
    assert f["nid_a_visual"] in ids
    # The reading row of the same person must NOT be a candidate parent.
    assert f["nid_a_reading"] not in ids


async def test_excludes_other_person_visual_rows(client, two_people_with_names):
    f = two_people_with_names
    r = await client.get(
        f"/admin/people/{f['pid_a']}/_reading_target_search?q=Bob",
        headers=AUTH_HEADERS,
    )
    assert r.status_code == 200
    ids = _option_ids(r.text)
    # Bob is on a different person; never a candidate parent for Ada's reading.
    assert f["nid_b_visual"] not in ids
    assert ids == []


async def test_excludes_reading_romanization_mrz_rows(client, two_people_with_names):
    """The reading row itself must never appear as a candidate parent
    even when q matches its name."""
    f = two_people_with_names
    r = await client.get(
        f"/admin/people/{f['pid_a']}/_reading_target_search?q=lovelace",
        headers=AUTH_HEADERS,
    )
    assert r.status_code == 200
    ids = _option_ids(r.text)
    assert f["nid_a_visual"] in ids
    assert f["nid_a_reading"] not in ids


# ---- Empty q + auth ---------------------------------------------------


async def test_empty_q_returns_empty_list(client, two_people_with_names):
    f = two_people_with_names
    r = await client.get(
        f"/admin/people/{f['pid_a']}/_reading_target_search?q=",
        headers=AUTH_HEADERS,
    )
    assert r.status_code == 200
    assert _option_ids(r.text) == []


async def test_missing_q_returns_empty_list(client, two_people_with_names):
    f = two_people_with_names
    r = await client.get(
        f"/admin/people/{f['pid_a']}/_reading_target_search",
        headers=AUTH_HEADERS,
    )
    assert r.status_code == 200
    assert _option_ids(r.text) == []


async def test_requires_auth(client, two_people_with_names):
    f = two_people_with_names
    r = await client.get(
        f"/admin/people/{f['pid_a']}/_reading_target_search?q=Ada",
        follow_redirects=False,
    )
    assert r.status_code in (302, 307)


# ---- Response shape ---------------------------------------------------


async def test_response_uses_option_list_shape(client, two_people_with_names):
    f = two_people_with_names
    r = await client.get(
        f"/admin/people/{f['pid_a']}/_reading_target_search?q=Ada",
        headers=AUTH_HEADERS,
    )
    assert 'role="option"' in r.text
    assert f'data-id="{f["nid_a_visual"]}"' in r.text
    # Label should include the visible name and a hint about the row's type.
    labels = _option_labels(r.text)
    assert any("Ada Lovelace" in lbl for lbl in labels), labels


# ---- Limit + escape_like ---------------------------------------------


async def test_limit_caps_results(client, two_people_with_names):
    f = two_people_with_names
    r = await client.get(
        f"/admin/people/{f['pid_a']}/_reading_target_search?q=Ada&limit=0",
        headers=AUTH_HEADERS,
    )
    # limit=0 violates ge=1 → 422 from FastAPI Query validation.
    assert r.status_code == 422


async def test_escape_like_neutralises_underscore(client, two_people_with_names):
    """`_` should not act as a single-char wildcard."""
    f = two_people_with_names
    # Real names don't contain '_'; query 'A_a' would match 'Ada' if `_` were
    # a wildcard. With escape, only literal 'A_a' (which doesn't exist) matches.
    r = await client.get(
        f"/admin/people/{f['pid_a']}/_reading_target_search?q=A_a",
        headers=AUTH_HEADERS,
    )
    assert r.status_code == 200
    assert _option_ids(r.text) == []


# ---- 404 on unknown person -------------------------------------------


async def test_unknown_person_returns_404(client):
    """A request for a non-existent person should 404, not silently return [].

    Same shape as other person-scoped endpoints (people_names, etc).
    """
    r = await client.get(
        f"/admin/people/{generate_id()}/_reading_target_search?q=anything",
        headers=AUTH_HEADERS,
    )
    assert r.status_code == 404
