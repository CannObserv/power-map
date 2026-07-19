# tests/api/admin/test_list_status_discoverability.py
"""Integration tests for #306 — admin list search must not silently hide matches.

Covers, per admin list (orgs / people / roles / jurisdictions):
- ``status="all"`` as a first-class validated value (rows across every status);
- unknown ``status`` falling back to ``active`` instead of no-filter;
- ``hidden_matches`` — per-status counts of search matches outside the current
  status filter, returned by the query helper and rendered as a "Show all"
  affordance in the list region template.
"""

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from src.api.admin.deps import get_db
from src.api.admin.jurisdictions_queries import query_jurisdictions_rows
from src.api.admin.orgs_queries import query_orgs_rows
from src.api.admin.people_queries import query_people_rows
from src.api.admin.role_assignments_queries import query_role_assignments_rows
from src.api.admin.roles_queries import query_roles_rows
from src.api.main import app
from src.core.db import generate_id

pytestmark = [pytest.mark.integration]

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


async def _seed_org(db, marker: str, *, active: bool = True, archived: bool = False) -> str:
    oid = generate_id()
    await db.execute(
        "INSERT INTO organizations (id, active, archived_at)"
        " VALUES ($1, $2, CASE WHEN $3 THEN NOW() END)",
        oid,
        active,
        archived,
    )
    await db.execute(
        "INSERT INTO organization_names (id, organization_id, name, is_canonical)"
        " VALUES ($1, $2, $3, TRUE)",
        generate_id(),
        oid,
        f"Testorg {marker}",
    )
    return oid


@pytest_asyncio.fixture(loop_scope="session")
async def org_trio(db):
    """One org per status (active / inactive / archived) sharing a search marker."""
    marker = f"zz{generate_id()[-10:].lower()}"
    ids = {
        "active": await _seed_org(db, marker),
        "inactive": await _seed_org(db, marker, active=False),
        "archived": await _seed_org(db, marker, archived=True),
    }
    return {"marker": marker, "ids": ids}


# ── Orgs: query helper ───────────────────────────────────────────────────────


async def test_orgs_status_all_returns_every_status(db, org_trio):
    rows, count, _, hidden = await query_orgs_rows(
        db, q=org_trio["marker"], status="all", page=1, page_size=50
    )
    assert {r["id"] for r in rows} == set(org_trio["ids"].values())
    assert count == 3
    assert hidden == []


async def test_orgs_unknown_status_falls_back_to_active(db, org_trio):
    rows, count, _, _ = await query_orgs_rows(
        db, q=org_trio["marker"], status="banana", page=1, page_size=50
    )
    assert {r["id"] for r in rows} == {org_trio["ids"]["active"]}
    assert count == 1


async def test_orgs_search_reports_hidden_matches(db, org_trio):
    _, count, _, hidden = await query_orgs_rows(
        db, q=org_trio["marker"], status="active", page=1, page_size=50
    )
    assert count == 1
    assert hidden == [{"status": "inactive", "count": 1}, {"status": "archived", "count": 1}]


async def test_orgs_no_search_no_hidden_matches(db, org_trio):
    _, _, _, hidden = await query_orgs_rows(db, q="", status="active", page=1, page_size=50)
    assert hidden == []


async def test_orgs_hidden_matches_omit_empty_statuses(db):
    marker = f"zz{generate_id()[-10:].lower()}"
    await _seed_org(db, marker)
    await _seed_org(db, marker, archived=True)
    _, _, _, hidden = await query_orgs_rows(db, q=marker, status="active", page=1, page_size=50)
    assert hidden == [{"status": "archived", "count": 1}]


# ── Orgs: route + template ───────────────────────────────────────────────────


async def test_orgs_list_renders_hidden_matches_affordance(client, org_trio):
    r = await client.get(f"/admin/orgs/?q={org_trio['marker']}", headers=AUTH_HEADERS)
    assert r.status_code == 200
    assert "2 more matches" in r.text
    assert "Show all" in r.text
    assert "status=all" in r.text


async def test_orgs_list_status_all_shows_everything_without_affordance(client, org_trio):
    r = await client.get(f"/admin/orgs/?q={org_trio['marker']}&status=all", headers=AUTH_HEADERS)
    assert r.status_code == 200
    for oid in org_trio["ids"].values():
        assert oid in r.text
    assert "more match" not in r.text


async def test_orgs_list_dropdown_offers_all_option(client, org_trio):
    r = await client.get("/admin/orgs/", headers=AUTH_HEADERS)
    assert r.status_code == 200
    assert 'value="all"' in r.text


# ── People (two-valued status axis) ──────────────────────────────────────────


async def _seed_person(db, marker: str, *, archived: bool = False) -> str:
    pid = generate_id()
    await db.execute(
        "INSERT INTO people (id, archived_at) VALUES ($1, CASE WHEN $2 THEN NOW() END)",
        pid,
        archived,
    )
    await db.execute(
        "INSERT INTO person_names (id, person_id, name, is_canonical, visibility)"
        " VALUES ($1, $2, $3, TRUE, 'public')",
        generate_id(),
        pid,
        f"Testperson {marker}",
    )
    return pid


@pytest_asyncio.fixture(loop_scope="session")
async def person_pair(db):
    marker = f"zz{generate_id()[-10:].lower()}"
    ids = {
        "active": await _seed_person(db, marker),
        "archived": await _seed_person(db, marker, archived=True),
    }
    return {"marker": marker, "ids": ids}


async def test_people_status_all_returns_every_status(db, person_pair):
    rows, count, _, hidden = await query_people_rows(
        db, q=person_pair["marker"], status="all", page=1, page_size=50
    )
    assert {r["id"] for r in rows} == set(person_pair["ids"].values())
    assert count == 2
    assert hidden == []


async def test_people_unknown_status_falls_back_to_active(db, person_pair):
    rows, _, _, _ = await query_people_rows(
        db, q=person_pair["marker"], status="banana", page=1, page_size=50
    )
    assert {r["id"] for r in rows} == {person_pair["ids"]["active"]}


async def test_people_search_reports_hidden_matches(db, person_pair):
    _, count, _, hidden = await query_people_rows(
        db, q=person_pair["marker"], status="active", page=1, page_size=50
    )
    assert count == 1
    assert hidden == [{"status": "archived", "count": 1}]


async def test_people_list_renders_hidden_matches_affordance(client, person_pair):
    r = await client.get(f"/admin/people/?q={person_pair['marker']}", headers=AUTH_HEADERS)
    assert r.status_code == 200
    assert "1 more match" in r.text
    assert "Show all" in r.text
    assert "status=all" in r.text


async def test_people_list_dropdown_offers_all_option(client, person_pair):
    r = await client.get("/admin/people/", headers=AUTH_HEADERS)
    assert r.status_code == 200
    assert 'value="all"' in r.text


# ── Roles (two-valued status axis + org_q second filter) ─────────────────────


@pytest_asyncio.fixture(loop_scope="session")
async def role_pair(db):
    marker = f"zz{generate_id()[-10:].lower()}"
    oid = await _seed_org(db, f"{marker} roleshost")
    ids = {}
    for key, archived in (("active", False), ("archived", True)):
        rid = generate_id()
        await db.execute(
            "INSERT INTO roles (id, organization_id, title, archived_at)"
            " VALUES ($1, $2, $3, CASE WHEN $4 THEN NOW() END)",
            rid,
            oid,
            f"Testrole {marker} {key}",
            archived,
        )
        ids[key] = rid
    return {"marker": marker, "ids": ids, "org_id": oid}


async def test_roles_status_all_returns_every_status(db, role_pair):
    rows, count, _, hidden = await query_roles_rows(
        db, q=role_pair["marker"], org_q="", status="all", page=1, page_size=50
    )
    assert {r["id"] for r in rows} == set(role_pair["ids"].values())
    assert count == 2
    assert hidden == []


async def test_roles_unknown_status_falls_back_to_active(db, role_pair):
    rows, _, _, _ = await query_roles_rows(
        db, q=role_pair["marker"], org_q="", status="banana", page=1, page_size=50
    )
    assert {r["id"] for r in rows} == {role_pair["ids"]["active"]}


async def test_roles_search_reports_hidden_matches(db, role_pair):
    _, count, _, hidden = await query_roles_rows(
        db, q=role_pair["marker"], org_q="", status="active", page=1, page_size=50
    )
    assert count == 1
    assert hidden == [{"status": "archived", "count": 1}]


async def test_roles_org_q_search_also_reports_hidden_matches(db, role_pair):
    """org_q alone is a search too — hidden matches must be reported for it."""
    _, _, _, hidden = await query_roles_rows(
        db, q="", org_q=f"{role_pair['marker']} roleshost", status="active", page=1, page_size=50
    )
    assert hidden == [{"status": "archived", "count": 1}]


async def test_roles_list_renders_hidden_matches_affordance(client, role_pair):
    r = await client.get(f"/admin/roles/?q={role_pair['marker']}", headers=AUTH_HEADERS)
    assert r.status_code == 200
    assert "1 more match" in r.text
    assert "Show all" in r.text
    assert "status=all" in r.text


async def test_roles_list_dropdown_offers_all_option(client, role_pair):
    r = await client.get("/admin/roles/", headers=AUTH_HEADERS)
    assert r.status_code == 200
    assert 'value="all"' in r.text


# ── Jurisdictions (three-valued status axis + type filter) ───────────────────


@pytest_asyncio.fixture(loop_scope="session")
async def jurisdiction_trio(db):
    marker = f"zz{generate_id()[-10:].lower()}"
    county_type = await db.fetchval("SELECT id FROM jurisdiction_types WHERE slug='county'")
    ids = {}
    for key, extra in (
        ("active", ""),
        ("superseded", ", superseded_at"),
        ("archived", ", archived_at"),
    ):
        jid = generate_id()
        cols = f"id, slug, name, type_id{extra}"
        vals = "$1, $2, $3, $4" + (", NOW()" if extra else "")
        await db.execute(
            f"INSERT INTO jurisdictions ({cols}) VALUES ({vals})",
            jid,
            f"test-{marker}-{key}",
            f"Testville {marker} {key.capitalize()}",
            county_type,
        )
        ids[key] = jid
    return {"marker": marker, "ids": ids}


async def test_jurisdictions_status_all_returns_every_status(db, jurisdiction_trio):
    rows, count, _, hidden = await query_jurisdictions_rows(
        db, q=jurisdiction_trio["marker"], status="all", type_slug=None, page=1, page_size=50
    )
    assert {r["id"] for r in rows} == set(jurisdiction_trio["ids"].values())
    assert count == 3
    assert hidden == []


async def test_jurisdictions_search_reports_hidden_matches(db, jurisdiction_trio):
    _, count, _, hidden = await query_jurisdictions_rows(
        db, q=jurisdiction_trio["marker"], status="active", type_slug=None, page=1, page_size=50
    )
    assert count == 1
    assert hidden == [
        {"status": "superseded", "count": 1},
        {"status": "archived", "count": 1},
    ]


async def test_jurisdictions_hidden_matches_respect_type_filter(db, jurisdiction_trio):
    """A type filter constrains the hidden-match counts like any search filter."""
    _, _, _, hidden = await query_jurisdictions_rows(
        db, q=jurisdiction_trio["marker"], status="active", type_slug="city", page=1, page_size=50
    )
    assert hidden == []


async def test_jurisdictions_list_renders_hidden_matches_affordance(client, jurisdiction_trio):
    r = await client.get(
        f"/admin/jurisdictions/?q={jurisdiction_trio['marker']}", headers=AUTH_HEADERS
    )
    assert r.status_code == 200
    assert "2 more matches" in r.text
    assert "Show all" in r.text
    assert "status=all" in r.text


async def test_jurisdictions_list_dropdown_offers_all_option(client, jurisdiction_trio):
    r = await client.get("/admin/jurisdictions/", headers=AUTH_HEADERS)
    assert r.status_code == 200
    assert 'value="all"' in r.text


# ── Role assignments (two-valued status axis, tri-tsv search) ────────────────


@pytest_asyncio.fixture(loop_scope="session")
async def assignment_pair(db):
    """Active + archived assignment for one person whose name carries the marker."""
    marker = f"zz{generate_id()[-10:].lower()}"
    pid = await _seed_person(db, marker)
    oid = await _seed_org(db, f"{marker} rahost")
    rid = generate_id()
    await db.execute(
        "INSERT INTO roles (id, organization_id, title) VALUES ($1, $2, $3)",
        rid,
        oid,
        f"Testrole {marker} seat",
    )
    ids = {}
    for key, archived in (("active", False), ("archived", True)):
        aid = generate_id()
        await db.execute(
            "INSERT INTO role_assignments (id, person_id, role_id, archived_at)"
            " VALUES ($1, $2, $3, CASE WHEN $4 THEN NOW() END)",
            aid,
            pid,
            rid,
            archived,
        )
        ids[key] = aid
    return {"marker": marker, "ids": ids}


async def test_role_assignments_status_all_returns_every_status(db, assignment_pair):
    rows, count, _, hidden = await query_role_assignments_rows(
        db, q=assignment_pair["marker"], status="all", page=1, page_size=50
    )
    assert {r["id"] for r in rows} == set(assignment_pair["ids"].values())
    assert count == 2
    assert hidden == []


async def test_role_assignments_unknown_status_falls_back_to_active(db, assignment_pair):
    rows, _, _, _ = await query_role_assignments_rows(
        db, q=assignment_pair["marker"], status="banana", page=1, page_size=50
    )
    assert {r["id"] for r in rows} == {assignment_pair["ids"]["active"]}


async def test_role_assignments_search_reports_hidden_matches(db, assignment_pair):
    _, count, _, hidden = await query_role_assignments_rows(
        db, q=assignment_pair["marker"], status="active", page=1, page_size=50
    )
    assert count == 1
    assert hidden == [{"status": "archived", "count": 1}]


async def test_role_assignments_list_renders_hidden_matches_affordance(client, assignment_pair):
    r = await client.get(
        f"/admin/role-assignments/?q={assignment_pair['marker']}", headers=AUTH_HEADERS
    )
    assert r.status_code == 200
    assert "1 more match" in r.text
    assert "Show all" in r.text
    assert "status=all" in r.text


async def test_role_assignments_list_dropdown_offers_all_option(client, assignment_pair):
    r = await client.get("/admin/role-assignments/", headers=AUTH_HEADERS)
    assert r.status_code == 200
    assert 'value="all"' in r.text
