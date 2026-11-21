"""Activity › Key-written names (#847): review the person names an API key wrote.

`/admin/activity/names/` lists every `person_names` row whose `source_key_id`
is set, newest first, filterable by key (any owner's key, by label). A
non-public row keeps its place but withholds its text and type: the SQL never
returns them, and the row points the curator at the person page, where the
disclosure toggle shows the name (docs/NAMES.md § Visibility rule, #847
exception).

The test DB is shared, so every assertion either filters on a key this module
created or looks only for rows this module seeded.
"""

import hashlib
import os
import re
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from src.api.admin.activity_names import query_key_written_names
from src.api.admin.deps import get_db
from src.api.main import app
from src.core.db import generate_id

pytestmark = [pytest.mark.integration]

AUTH_HEADERS = {"X-ExeDev-UserID": "usr_847_viewer", "X-ExeDev-Email": "viewer847@test.com"}
HTMX_HEADERS = {**AUTH_HEADERS, "HX-Request": "true"}
_BASE = "/admin/activity/names/"
_T0 = datetime(2026, 11, 1, 12, 0, tzinfo=UTC)


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


async def _make_key(db, label: str) -> str:
    """A key owned by a fresh app user — never the viewing admin (#847: any owner)."""
    uid, kid = generate_id(), generate_id()
    raw = "pm_" + os.urandom(8).hex()
    await db.execute("INSERT INTO app_users (id, email) VALUES ($1,$2)", uid, f"{uid}@847.test")
    await db.execute(
        "INSERT INTO api_keys (id, user_id, label, key_prefix, key_hash) VALUES ($1,$2,$3,$4,$5)",
        kid,
        uid,
        label,
        raw[:8],
        hashlib.sha256(raw.encode()).hexdigest(),
    )
    return kid


async def _make_person(db, canonical: str, *, archived: bool = False) -> str:
    pid = generate_id()
    await db.execute(
        "INSERT INTO people (id, archived_at) VALUES ($1, $2)",
        pid,
        datetime.now(UTC) if archived else None,
    )
    await db.execute(
        "INSERT INTO person_names (id, person_id, name, name_type, is_canonical)"
        " VALUES ($1, $2, $3, 'legal', TRUE)",
        generate_id(),
        pid,
        canonical,
    )
    return pid


async def _stamp_name(
    db,
    person_id: str,
    name: str,
    key_id: str | None,
    *,
    name_type: str = "variant",
    created_at: datetime = _T0,
    name_id: str | None = None,
) -> str:
    nid = name_id or generate_id()
    await db.execute(
        "INSERT INTO person_names"
        " (id, person_id, name, name_type, is_canonical, source_key_id, created_at)"
        " VALUES ($1, $2, $3, $4, FALSE, $5, $6)",
        nid,
        person_id,
        name,
        name_type,
        key_id,
        created_at,
    )
    return nid


def _row_ids(html: str) -> list[str]:
    """Name-row ids in render order (`data-name-id` on each review row)."""
    return re.findall(r'data-name-id="([^"]+)"', html)


@pytest_asyncio.fixture(loop_scope="session")
async def seeded(db):
    """Two keys (owned by other users), people, and a spread of names."""
    k1 = await _make_key(db, "Review Key 847-A")
    k2 = await _make_key(db, "Review Key 847-B")
    k_idle = await _make_key(db, "Idle Key 847")
    p1 = await _make_person(db, "Pat Reviewee 847")
    p2 = await _make_person(db, "Archived Reviewee 847", archived=True)
    ids = {"k1": k1, "k2": k2, "k_idle": k_idle, "p1": p1, "p2": p2}
    ids["old"] = await _stamp_name(db, p1, "Old Variant 847", k1, created_at=_T0)
    ids["new"] = await _stamp_name(
        db, p1, "New Variant 847", k1, created_at=_T0 + timedelta(days=2)
    )
    ids["archived_person_name"] = await _stamp_name(
        db, p2, "Variant On Archived 847", k1, created_at=_T0 + timedelta(days=1)
    )
    ids["other_key"] = await _stamp_name(db, p1, "Other Key Variant 847", k2)
    # The trigger turns a key-sent deadname into legal_only, as on the observation path.
    ids["deadname"] = await _stamp_name(
        db, p1, "Secret Deadname 847", k1, name_type="deadname", created_at=_T0 + timedelta(hours=1)
    )
    ids["unstamped"] = await _stamp_name(db, p1, "Admin Added 847", None)
    return ids


# ---- Rendering & filtering ---------------------------------------------------


async def test_page_lists_a_keys_names_newest_first(client, seeded):
    r = await client.get(_BASE, params={"key": seeded["k1"]}, headers=AUTH_HEADERS)
    assert r.status_code == 200
    assert _row_ids(r.text) == [
        seeded["new"],
        seeded["archived_person_name"],
        seeded["deadname"],
        seeded["old"],
    ]
    assert "Other Key Variant 847" not in r.text
    assert "Admin Added 847" not in r.text


async def test_created_at_ties_break_on_id_descending(client, db):
    """#297: the ORDER BY ends on a unique column, so a tie has one order."""
    k = await _make_key(db, "Tie Key 847")
    p = await _make_person(db, "Tie Person 847")
    lo = await _stamp_name(db, p, "Tie A 847", k, name_id="01TIE847AAAAAAAAAAAAAAAAAA")
    hi = await _stamp_name(db, p, "Tie B 847", k, name_id="01TIE847ZZZZZZZZZZZZZZZZZZ")
    r = await client.get(_BASE, params={"key": k}, headers=AUTH_HEADERS)
    assert _row_ids(r.text) == [hi, lo]


async def test_all_keys_lists_every_stamped_name_and_no_unstamped_one(client, seeded):
    r = await client.get(_BASE, params={"page_size": 500}, headers=AUTH_HEADERS)
    assert r.status_code == 200
    ids = _row_ids(r.text)
    for k in ("new", "old", "archived_person_name", "other_key", "deadname"):
        assert seeded[k] in ids
    assert seeded["unstamped"] not in ids


async def test_row_shows_name_type_person_link_key_and_added(client, seeded):
    r = await client.get(_BASE, params={"key": seeded["k1"]}, headers=AUTH_HEADERS)
    assert "New Variant 847" in r.text
    assert "variant" in r.text
    assert f'href="/admin/people/{seeded["p1"]}/"' in r.text
    assert "Pat Reviewee 847" in r.text
    assert "Review Key 847-A" in r.text
    assert "2026-11-03" in r.text


async def test_archived_person_keeps_its_row_with_a_badge(client, seeded):
    """No status axis (#306 trap): an archived person's names still list, badged."""
    r = await client.get(_BASE, params={"key": seeded["k1"]}, headers=AUTH_HEADERS)
    row = _row_html(r.text, seeded["archived_person_name"])
    assert "Variant On Archived 847" in row
    assert "Archived" in row


async def test_citation_count_sits_beside_the_key(client, db, seeded):
    for i in range(2):
        await db.execute(
            "INSERT INTO citations (id, entity_type, entity_id, url)"
            " VALUES ($1, 'person_name', $2, $3)",
            generate_id(),
            seeded["new"],
            f"https://example.org/847/{i}",
        )
    r = await client.get(_BASE, params={"key": seeded["k1"]}, headers=AUTH_HEADERS)
    assert re.search(r"data-citation-count=\"2\"", _row_html(r.text, seeded["new"]))
    assert 'data-citation-count="0"' in _row_html(r.text, seeded["old"])


# ---- Non-public names (#847 exception to the NAMES.md visibility rule) -------


async def test_non_public_name_withholds_text_and_type(client, seeded):
    r = await client.get(_BASE, params={"key": seeded["k1"]}, headers=AUTH_HEADERS)
    assert "Secret Deadname 847" not in r.text
    # The type is withheld too; the layout's script names mention "deadname",
    # so look inside the table only.
    table = re.search(r'<table[^>]*id="key-names-table".*?</table>', r.text, re.S).group(0)
    assert "deadname" not in table
    assert "legal_only" not in table
    row = _row_html(r.text, seeded["deadname"])
    assert "Not public" in row
    assert f'href="/admin/people/{seeded["p1"]}/"' in row


async def test_non_public_text_never_leaves_the_database(db, seeded):
    """The query itself withholds the text: a template slip cannot leak it."""
    rows, _count, _pctx = await query_key_written_names(db, key=seeded["k1"], page=1, page_size=50)
    by_id = {r["id"]: r for r in rows}
    hidden = by_id[seeded["deadname"]]
    assert hidden["name"] is None
    assert hidden["name_type"] is None
    assert hidden["is_public"] is False
    assert by_id[seeded["new"]]["name"] == "New Variant 847"


async def test_hidden_name_is_withheld_too(client, db, seeded):
    await db.execute("UPDATE person_names SET visibility='hidden' WHERE id=$1", seeded["other_key"])
    r = await client.get(_BASE, params={"key": seeded["k2"]}, headers=AUTH_HEADERS)
    assert "Other Key Variant 847" not in r.text
    assert "Not public" in _row_html(r.text, seeded["other_key"])


# ---- Key dropdown -------------------------------------------------------------


async def test_dropdown_lists_keys_holding_names_from_any_owner(client, seeded):
    r = await client.get(_BASE, headers=AUTH_HEADERS)
    select = re.search(r'<select[^>]*name="key".*?</select>', r.text, re.S).group(0)
    assert f'value="{seeded["k1"]}"' in select
    assert f'value="{seeded["k2"]}"' in select
    assert f'value="{seeded["k_idle"]}"' not in select


async def test_selected_key_is_marked_selected(client, seeded):
    r = await client.get(_BASE, params={"key": seeded["k2"]}, headers=AUTH_HEADERS)
    assert re.search(rf'value="{seeded["k2"]}"\s+selected', r.text)


async def test_unknown_key_shows_an_empty_list_not_every_key(client, seeded):
    r = await client.get(_BASE, params={"key": "no-such-key"}, headers=AUTH_HEADERS)
    assert r.status_code == 200
    assert _row_ids(r.text) == []
    assert "data-empty-row" in r.text


async def test_key_label_is_escaped(client, db):
    k = await _make_key(db, "<b>Bold 847</b>")
    p = await _make_person(db, "Escape Person 847")
    await _stamp_name(db, p, "Escape Variant 847", k)
    r = await client.get(_BASE, params={"key": k}, headers=AUTH_HEADERS)
    assert "<b>Bold 847</b>" not in r.text
    assert "&lt;b&gt;Bold 847&lt;/b&gt;" in r.text


# ---- Pagination & HTMX region ---------------------------------------------------


async def test_pages_continue_in_order(client, db):
    k = await _make_key(db, "Paged Key 847")
    p = await _make_person(db, "Paged Person 847")
    ids = [
        await _stamp_name(db, p, f"Paged {i:02d} 847", k, created_at=_T0 + timedelta(minutes=i))
        for i in range(11)
    ]
    newest_first = list(reversed(ids))
    r1 = await client.get(_BASE, params={"key": k, "page_size": 10}, headers=AUTH_HEADERS)
    r2 = await client.get(
        _BASE, params={"key": k, "page_size": 10, "page": 2}, headers=AUTH_HEADERS
    )
    assert _row_ids(r1.text) == newest_first[:10]
    assert _row_ids(r2.text) == newest_first[10:]
    assert "Showing 11–11 of 11" in r2.text


async def test_htmx_request_returns_the_region_only(client, seeded):
    r = await client.get(_BASE, params={"key": seeded["k1"]}, headers=HTMX_HEADERS)
    assert r.status_code == 200
    assert "<html" not in r.text
    assert _row_ids(r.text)


async def test_sidebar_and_activity_index_link_the_page(client, seeded):
    r = await client.get(_BASE, headers=AUTH_HEADERS)
    assert re.search(r'href="/admin/activity/names/"[^>]*aria-current="page"', r.text)
    idx = await client.get("/admin/activity/", headers=AUTH_HEADERS)
    assert 'href="/admin/activity/names/"' in idx.text


async def test_redirects_unauthenticated(client):
    r = await client.get(_BASE, follow_redirects=False)
    assert r.status_code in (302, 307)
    assert "/__exe.dev/login" in r.headers["location"]


def _row_html(html: str, name_id: str) -> str:
    m = re.search(rf'<tr[^>]*data-name-id="{re.escape(name_id)}".*?</tr>', html, re.S)
    assert m, f"no review row for {name_id}"
    return m.group(0)
