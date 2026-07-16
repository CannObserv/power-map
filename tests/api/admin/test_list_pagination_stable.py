"""Stable offset pagination for admin list queries (#297).

Admin list queries paginate with LIMIT/OFFSET. If the ORDER BY lacks a unique
final key, Postgres may return tied rows in a different order per page, so an
admin paging the list skips and duplicates rows. Each test seeds rows that tie
on the query's sort key, pages the query at a small page size, and asserts the
enumeration is complete and duplicate-free.
"""

import re

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from src.api.admin.deps import get_db
from src.api.admin.orgs_queries import query_orgs_rows
from src.api.admin.people_queries import query_people_rows
from src.api.admin.role_assignments import _LIST_ORDER, _LIST_SELECT
from src.api.admin.roles_queries import query_roles_rows
from src.api.main import app
from src.core.db import generate_id

pytestmark = pytest.mark.integration

AUTH_HEADERS = {
    "X-ExeDev-UserID": "usr_test",
    "X-ExeDev-Email": "admin@test.com",
}


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


async def _paginate_query(query_fn, /, **kwargs):
    """Enumerate row ids across all pages of an admin query helper.

    query_fn(**kwargs, page=n, page_size=…) → (rows, count, pctx). Returns the
    ordered list of row ``id`` values collected across every page.
    """
    db = kwargs.pop("db")
    page_size = kwargs.pop("page_size")
    collected: list[str] = []
    page = 1
    while True:
        rows, count, _ = await query_fn(db, page=page, page_size=page_size, **kwargs)
        collected.extend(r["id"] for r in rows)
        if page * page_size >= count:
            break
        page += 1
    return collected


async def test_admin_orgs_list_stable_under_tied_name(db):
    """query_orgs_rows: active orgs sharing a display_name tie on ORDER BY dn.display_name."""
    token = "Zzyzxorgadmin"
    org_ids = sorted(generate_id() for _ in range(50))
    for oid in reversed(org_ids):
        await db.execute("INSERT INTO organizations (id) VALUES ($1)", oid)
        await db.execute(
            "INSERT INTO organization_names"
            " (id, organization_id, name, name_type, is_canonical)"
            " VALUES ($1,$2,$3,'legal',TRUE)",
            generate_id(),
            oid,
            token,
        )

    collected = await _paginate_query(query_orgs_rows, db=db, q=token, status="active", page_size=3)
    assert len(collected) == len(org_ids)
    assert set(collected) == set(org_ids)
    assert collected == sorted(org_ids)  # tied name → o.id ascending


async def test_admin_people_list_stable_under_tied_name(db):
    """query_people_rows: active people sharing a name tie on ORDER BY n.sort_key."""
    token = "Zzyzxpersonadmin"
    person_ids = sorted(generate_id() for _ in range(50))
    for pid in reversed(person_ids):
        await db.execute("INSERT INTO people (id) VALUES ($1)", pid)
        await db.execute(
            "INSERT INTO person_names"
            " (id, person_id, name, name_type, is_canonical, visibility)"
            " VALUES ($1,$2,$3,'legal',TRUE,'public')",
            generate_id(),
            pid,
            token,
        )

    collected = await _paginate_query(
        query_people_rows, db=db, q=token, status="active", page_size=3
    )
    assert len(collected) == len(person_ids)
    assert set(collected) == set(person_ids)
    assert collected == sorted(person_ids)  # tied name → p.id ascending


async def test_admin_roles_list_stable_under_tied_org_and_title(db):
    """query_roles_rows: roles under same-named orgs + same title tie on (display_name, title)."""
    token = "Zzyzxroleadmin"
    org_name = "ZzyzxroleadminOrg"
    role_ids = sorted(generate_id() for _ in range(50))
    for rid in reversed(role_ids):
        oid = generate_id()
        await db.execute("INSERT INTO organizations (id) VALUES ($1)", oid)
        # Same org display_name across all → dn.display_name ties.
        await db.execute(
            "INSERT INTO organization_names"
            " (id, organization_id, name, name_type, is_canonical)"
            " VALUES ($1,$2,$3,'legal',TRUE)",
            generate_id(),
            oid,
            org_name,
        )
        # Same title across all (allowed: uq_role_org_title is per-org) → r.title ties.
        await db.execute(
            "INSERT INTO roles (id, organization_id, title) VALUES ($1,$2,$3)",
            rid,
            oid,
            token,
        )

    collected = await _paginate_query(
        query_roles_rows, db=db, q=token, org_q="", status="active", page_size=3
    )
    assert len(collected) == len(role_ids)
    assert set(collected) == set(role_ids)
    assert collected == sorted(role_ids)  # tied (name, title) → r.id ascending


async def test_admin_role_assignments_list_stable_under_tied_sort_key(db):
    """_LIST_SELECT + _LIST_ORDER: archived assignments tying on (is_current, name, start_date)."""
    person_id = generate_id()
    org_id = generate_id()
    role_id = generate_id()
    await db.execute("INSERT INTO people (id) VALUES ($1)", person_id)
    await db.execute("INSERT INTO organizations (id) VALUES ($1)", org_id)
    await db.execute(
        "INSERT INTO roles (id, organization_id, title) VALUES ($1,$2,'Director')", role_id, org_id
    )
    # Archived rows aren't covered by the active-row unique index, so they can
    # tie fully on (person, role, start_date) — and thus on the list sort key.
    ra_ids = sorted(generate_id() for _ in range(50))
    for aid in reversed(ra_ids):
        await db.execute(
            "INSERT INTO role_assignments (id, person_id, role_id, start_date, archived_at)"
            " VALUES ($1,$2,$3,DATE '2023-01-01',NOW())",
            aid,
            person_id,
            role_id,
        )

    sql = f"{_LIST_SELECT} WHERE ra.person_id = $1 {_LIST_ORDER} LIMIT $2 OFFSET $3"
    page_size = 3
    collected: list[str] = []
    offset = 0
    while True:
        rows = await db.fetch(sql, person_id, page_size, offset)
        collected.extend(r["id"] for r in rows)
        if len(rows) < page_size:
            break
        offset += page_size
    assert len(collected) == len(ra_ids)
    assert set(collected) == set(ra_ids)
    assert collected == sorted(ra_ids)  # full tie → ra.id ascending


async def test_admin_imports_batches_list_stable_under_tied_imported_at(db, client):
    """imports_list handler: batches sharing imported_at enumerate completely across pages.

    PAGE_SIZE is 50, so seed 55 batches at one imported_at to force a page
    boundary; batch ids are extractable from the row hrefs.
    """
    batch_ids = [generate_id() for _ in range(55)]
    for bid in batch_ids:
        await db.execute(
            "INSERT INTO import_batches"
            " (id, source_file, file_hash, imported_at, row_count, loaded_count, error_count)"
            " VALUES ($1, 'tie.csv', $2, TIMESTAMPTZ '2026-07-07T00:23:12.311563Z', 0, 0, 0)",
            bid,
            bid,  # unique file_hash per row (NOT NULL); irrelevant to ordering
        )

    href_re = re.compile(r"/admin/imports/([0-9A-HJKMNP-TV-Z]{26})/")
    collected: list[str] = []
    for page in (1, 2):
        r = await client.get("/admin/imports/", params={"page": page}, headers=AUTH_HEADERS)
        assert r.status_code == 200
        collected.extend(href_re.findall(r.text))

    seeded = set(batch_ids)
    seen_seeded = [b for b in collected if b in seeded]
    # Complete and duplicate-free across the two pages.
    assert set(seen_seeded) == seeded
    assert len(seen_seeded) == len(batch_ids)
