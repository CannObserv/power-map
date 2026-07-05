"""Integration tests for admin roles views.

Requires DATABASE_URL. Run with:
    DATABASE_URL=<dsn> uv run pytest tests/api/admin/test_roles.py -m integration -v
"""

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient

from src.api.main import app
from src.core.db import generate_id

pytestmark = [
    pytest.mark.integration,
]

AUTH_HEADERS = {
    "X-ExeDev-UserID": "usr_test",
    "X-ExeDev-Email": "admin@test.com",
}


@pytest_asyncio.fixture(loop_scope="session")
async def db(db_pool):
    async with db_pool.acquire() as conn:
        yield conn


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


@pytest_asyncio.fixture(loop_scope="session")
async def org_id(db):
    oid = generate_id()
    await db.execute("INSERT INTO organizations (id) VALUES ($1)", oid)
    await db.execute(
        "INSERT INTO organization_names (id, organization_id, name, is_canonical)"
        " VALUES ($1, $2, 'Test Org', TRUE)",
        generate_id(),
        oid,
    )
    yield oid
    # Clean up children first to avoid FK violations: tests like
    # test_create_role_post_redirects POST a new role tied to this org via the
    # admin form, leaving an orphan row that the org delete would otherwise hit.
    await db.execute(
        "DELETE FROM role_assignments"
        " WHERE role_id IN (SELECT id FROM roles WHERE organization_id = $1)",
        oid,
    )
    await db.execute("DELETE FROM roles WHERE organization_id = $1", oid)
    await db.execute("DELETE FROM organization_acronyms WHERE organization_id = $1", oid)
    await db.execute("DELETE FROM organization_names WHERE organization_id = $1", oid)
    await db.execute("DELETE FROM organizations WHERE id = $1", oid)


@pytest_asyncio.fixture(loop_scope="session")
async def role_id(db, org_id):
    rid = generate_id()
    await db.execute(
        "INSERT INTO roles (id, organization_id, title) VALUES ($1, $2, 'Test Role')",
        rid,
        org_id,
    )
    yield rid
    await db.execute("DELETE FROM role_assignments WHERE role_id = $1", rid)
    await db.execute("DELETE FROM roles WHERE id = $1", rid)


@pytest_asyncio.fixture(loop_scope="session")
async def person_id(db):
    pid = generate_id()
    await db.execute("INSERT INTO people (id) VALUES ($1)", pid)
    await db.execute(
        "INSERT INTO person_names (id, person_id, name, is_canonical)"
        " VALUES ($1, $2, 'Test Person', TRUE)",
        generate_id(),
        pid,
    )
    yield pid
    await db.execute("DELETE FROM person_names WHERE person_id = $1", pid)
    await db.execute("DELETE FROM people WHERE id = $1", pid)


@pytest_asyncio.fixture(loop_scope="session")
async def seat_role(db, org_id):
    """A districted seat under `org_id`: role_type + jurisdiction + qualifier.

    Title deliberately excludes "State Representative" / "Position 1" so the
    display tests prove the join-sourced role_type/qualifier render, not the
    title echoing them.
    """
    jur_id = generate_id()
    rid = generate_id()
    type_id = await db.fetchval(
        "SELECT id FROM jurisdiction_types WHERE slug='legislative_district'"
    )
    rt_id = await db.fetchval("SELECT id FROM role_types WHERE slug='state_representative'")
    await db.execute(
        "INSERT INTO jurisdictions (id, slug, name, type_id) VALUES ($1,$2,$3,$4)",
        jur_id,
        f"ld-admin-{jur_id[-8:].lower()}",
        "Test Legislative District",
        type_id,
    )
    await db.execute(
        "INSERT INTO roles"
        " (id, organization_id, title, role_type_id, jurisdiction_id, qualifier)"
        " VALUES ($1,$2,$3,$4,$5,$6)",
        rid,
        org_id,
        "WA Rep Seat One",
        rt_id,
        jur_id,
        "Position 1",
    )
    yield {"role_id": rid, "jur_id": jur_id, "rt_id": rt_id}
    await db.execute("DELETE FROM role_assignments WHERE role_id = $1", rid)
    await db.execute("DELETE FROM roles WHERE id = $1", rid)
    await db.execute("DELETE FROM jurisdictions WHERE id = $1", jur_id)


async def test_role_detail_shows_seat_fields(client, seat_role):
    """Seat detail surfaces role_type display_name, jurisdiction name, qualifier."""
    r = client.get(f"/admin/roles/{seat_role['role_id']}/", headers=AUTH_HEADERS)
    assert r.status_code == 200
    assert 'id="seat-details"' in r.text
    assert "State Representative" in r.text  # role_type display_name (join)
    assert "Test Legislative District" in r.text  # jurisdiction name (join)
    assert "Position 1" in r.text  # qualifier column


async def test_role_detail_plain_role_hides_seat_section(client, role_id):
    """A plain role (no seat fields) renders no seat section."""
    r = client.get(f"/admin/roles/{role_id}/", headers=AUTH_HEADERS)
    assert r.status_code == 200
    assert 'id="seat-details"' not in r.text


async def test_roles_list_shows_seat_badge(client, seat_role):
    """Seats are visually flagged in the list."""
    r = client.get("/admin/roles/?org_q=Test", headers=AUTH_HEADERS)
    assert r.status_code == 200
    assert "badge--seat" in r.text


@pytest_asyncio.fixture(loop_scope="session")
async def rt_rep_id(db):
    """role_types.id for the seeded state_representative office."""
    return await db.fetchval("SELECT id FROM role_types WHERE slug='state_representative'")


@pytest_asyncio.fixture(loop_scope="session")
async def wa_ld_jurisdiction(db):
    """A WA legislative district; slug usa-wa-ld-999 drives title synthesis."""
    jid = generate_id()
    type_id = await db.fetchval(
        "SELECT id FROM jurisdiction_types WHERE slug='legislative_district'"
    )
    await db.execute(
        "INSERT INTO jurisdictions (id, slug, name, type_id) VALUES ($1,$2,$3,$4)",
        jid,
        "usa-wa-ld-999",
        "Legislative District 999",
        type_id,
    )
    yield jid
    # Drop any seats POSTed onto this district before removing it (FK).
    await db.execute(
        "DELETE FROM role_assignments WHERE role_id IN"
        " (SELECT id FROM roles WHERE jurisdiction_id=$1)",
        jid,
    )
    await db.execute("DELETE FROM roles WHERE jurisdiction_id=$1", jid)
    await db.execute("DELETE FROM jurisdictions WHERE id=$1", jid)


@pytest_asyncio.fixture(loop_scope="session")
async def nonwa_jurisdiction(db):
    """A jurisdiction whose slug is NOT usa-wa-ld-N → title synthesis returns None."""
    jid = generate_id()
    type_id = await db.fetchval(
        "SELECT id FROM jurisdiction_types WHERE slug='legislative_district'"
    )
    await db.execute(
        "INSERT INTO jurisdictions (id, slug, name, type_id) VALUES ($1,$2,$3,$4)",
        jid,
        f"test-nonwa-{jid[-8:].lower()}",
        "Nonsynthesizable County",
        type_id,
    )
    yield jid
    await db.execute(
        "DELETE FROM role_assignments WHERE role_id IN"
        " (SELECT id FROM roles WHERE jurisdiction_id=$1)",
        jid,
    )
    await db.execute("DELETE FROM roles WHERE jurisdiction_id=$1", jid)
    await db.execute("DELETE FROM jurisdictions WHERE id=$1", jid)


# ---------------------------------------------------------------------------
# Slice 2 — create-form seat block + jurisdiction typeahead
# ---------------------------------------------------------------------------


async def test_jurisdiction_search_returns_matches(client, wa_ld_jurisdiction):
    r = client.get("/admin/jurisdictions/search/?q=District 999", headers=AUTH_HEADERS)
    assert r.status_code == 200
    assert "Legislative District 999" in r.text
    assert wa_ld_jurisdiction in r.text


async def test_create_seat_synthesizes_title(client, db, org_id, wa_ld_jurisdiction, rt_rep_id):
    """Seat create with empty title → PM synthesizes the canonical WA seat title."""
    r = client.post(
        "/admin/roles/new/",
        headers=AUTH_HEADERS,
        follow_redirects=False,
        data={
            "organization_id": org_id,
            "title": "",
            "role_type_id": rt_rep_id,
            "jurisdiction_id": wa_ld_jurisdiction,
            "qualifier": "Position 1",
            "notes": "",
        },
    )
    assert r.status_code == 303
    row = await db.fetchrow(
        "SELECT title, role_type_id, jurisdiction_id, qualifier FROM roles"
        " WHERE organization_id=$1 AND jurisdiction_id=$2",
        org_id,
        wa_ld_jurisdiction,
    )
    assert row["role_type_id"] == rt_rep_id
    assert row["qualifier"] == "Position 1"
    assert row["title"] == "Washington State Representative, LD-999, Position 1"


async def test_create_seat_missing_office_rejected(client, db, org_id, wa_ld_jurisdiction):
    """jurisdiction without a role_type violates chk_role_districted_needs_type."""
    r = client.post(
        "/admin/roles/new/",
        headers=AUTH_HEADERS,
        follow_redirects=False,
        data={
            "organization_id": org_id,
            "title": "",
            "role_type_id": "",
            "jurisdiction_id": wa_ld_jurisdiction,
            "qualifier": "",
            "notes": "",
        },
    )
    assert r.status_code == 200
    assert "needs an office" in r.text
    n = await db.fetchval("SELECT count(*) FROM roles WHERE jurisdiction_id=$1", wa_ld_jurisdiction)
    assert n == 0


async def test_create_qualifier_without_jurisdiction_rejected(client, org_id, rt_rep_id):
    """qualifier without a jurisdiction violates chk_role_qualifier_needs_jurisdiction."""
    r = client.post(
        "/admin/roles/new/",
        headers=AUTH_HEADERS,
        follow_redirects=False,
        data={
            "organization_id": org_id,
            "title": "Some Role",
            "role_type_id": rt_rep_id,
            "jurisdiction_id": "",
            "qualifier": "Position 2",
            "notes": "",
        },
    )
    assert r.status_code == 200
    assert "requires a jurisdiction" in r.text


async def test_create_plain_role_requires_title(client, org_id):
    """A non-seat role still requires a title."""
    r = client.post(
        "/admin/roles/new/",
        headers=AUTH_HEADERS,
        follow_redirects=False,
        data={
            "organization_id": org_id,
            "title": "",
            "role_type_id": "",
            "jurisdiction_id": "",
            "qualifier": "",
            "notes": "",
        },
    )
    assert r.status_code == 200
    assert "Title is required for a non-seat" in r.text


async def test_create_seat_nonwa_requires_manual_title(
    client, org_id, rt_rep_id, nonwa_jurisdiction
):
    """Synthesis returns None for a non-WA jurisdiction → manual title required."""
    r = client.post(
        "/admin/roles/new/",
        headers=AUTH_HEADERS,
        follow_redirects=False,
        data={
            "organization_id": org_id,
            "title": "",
            "role_type_id": rt_rep_id,
            "jurisdiction_id": nonwa_jurisdiction,
            "qualifier": "",
            "notes": "",
        },
    )
    assert r.status_code == 200
    assert "Could not auto-generate" in r.text


async def test_create_seat_nonwa_manual_title_ok(client, db, org_id, rt_rep_id, nonwa_jurisdiction):
    """A supplied title is respected for an unsynthesizable seat (fill-when-absent)."""
    r = client.post(
        "/admin/roles/new/",
        headers=AUTH_HEADERS,
        follow_redirects=False,
        data={
            "organization_id": org_id,
            "title": "County Commissioner Seat",
            "role_type_id": rt_rep_id,
            "jurisdiction_id": nonwa_jurisdiction,
            "qualifier": "",
            "notes": "",
        },
    )
    assert r.status_code == 303
    row = await db.fetchrow(
        "SELECT title FROM roles WHERE organization_id=$1 AND jurisdiction_id=$2",
        org_id,
        nonwa_jurisdiction,
    )
    assert row["title"] == "County Commissioner Seat"


async def test_role_detail_shows_person_name(client, db, role_id, person_id):
    """Role detail assignment list must show canonical name via v_person_display_names."""
    ra_id = generate_id()
    await db.execute(
        "INSERT INTO role_assignments (id, person_id, role_id, is_current)"
        " VALUES ($1, $2, $3, TRUE)",
        ra_id,
        person_id,
        role_id,
    )
    try:
        response = client.get(f"/admin/roles/{role_id}/", headers=AUTH_HEADERS)
        assert response.status_code == 200
        assert "Test Person" in response.text
    finally:
        await db.execute("DELETE FROM role_assignments WHERE id = $1", ra_id)


async def test_roles_list_returns_200(client):
    response = client.get("/admin/roles/", headers=AUTH_HEADERS)
    assert response.status_code == 200
    assert "roles" in response.text.lower()


async def test_roles_list_redirects_unauthenticated(client):
    response = client.get("/admin/roles/", follow_redirects=False)
    assert response.status_code in (302, 307)
    assert "/__exe.dev/login" in response.headers["location"]


async def test_role_detail_returns_200(client, role_id):
    response = client.get(f"/admin/roles/{role_id}/", headers=AUTH_HEADERS)
    assert response.status_code == 200
    assert "Test Role" in response.text


async def test_role_detail_404_for_unknown(client):
    response = client.get(f"/admin/roles/{generate_id()}/", headers=AUTH_HEADERS)
    assert response.status_code == 404


async def test_create_role_post_redirects(client, org_id):
    response = client.post(
        "/admin/roles/new/",
        headers=AUTH_HEADERS,
        data={"organization_id": org_id, "title": "New Role", "notes": ""},
        follow_redirects=False,
    )
    assert response.status_code in (302, 303)


async def test_archive_role(client, role_id):
    response = client.post(
        f"/admin/roles/{role_id}/archive/",
        headers=AUTH_HEADERS,
        follow_redirects=False,
    )
    assert response.status_code in (302, 303)


async def test_archive_role_redirects_with_flash_query(client, role_id):
    """Archive redirects to detail with ?flash=archived."""
    response = client.post(
        f"/admin/roles/{role_id}/archive/",
        headers=AUTH_HEADERS,
        follow_redirects=False,
    )
    assert response.status_code in (302, 303)
    assert response.headers["location"] == f"/admin/roles/{role_id}/?flash=archived"


async def test_archived_flash_renders_on_role_detail(client, role_id):
    """Role detail with ?flash=archived renders the success flash."""
    response = client.get(f"/admin/roles/{role_id}/?flash=archived", headers=AUTH_HEADERS)
    assert response.status_code == 200
    assert "Role archived." in response.text
    assert "flash--success" in response.text
    assert "HX-Replace-Url" in response.headers
    assert "flash" not in response.headers["HX-Replace-Url"]


async def test_role_detail_unknown_flash_key_ignored(client, role_id):
    """GET role detail with ?flash=bogus returns 200 with no flash and no HX-Replace-Url."""
    response = client.get(f"/admin/roles/{role_id}/?flash=bogus", headers=AUTH_HEADERS)
    assert response.status_code == 200
    assert "flash--success" not in response.text
    assert "HX-Replace-Url" not in response.headers


async def test_archive_already_archived_role_returns_409(client, db, role_id):
    """Re-archiving an already-archived role is rejected with 409."""
    await db.execute("UPDATE roles SET archived_at = NOW() WHERE id = $1", role_id)
    response = client.post(
        f"/admin/roles/{role_id}/archive/",
        headers=AUTH_HEADERS,
        follow_redirects=False,
    )
    assert response.status_code == 409
    assert response.json()["detail"] == "Role is already archived"


async def test_hard_delete_requires_archive(client, role_id):
    response = client.delete(f"/admin/roles/{role_id}/", headers=AUTH_HEADERS)
    assert response.status_code == 409


async def test_hard_delete_archived_role(client, db, role_id):
    await db.execute("UPDATE roles SET archived_at = NOW() WHERE id = $1", role_id)
    response = client.delete(
        f"/admin/roles/{role_id}/",
        headers={**AUTH_HEADERS, "HX-Request": "true"},
        follow_redirects=False,
    )
    assert response.status_code == 200


async def test_hard_delete_archived_role_htmx_redirects(client, db, role_id):
    """HTMX delete of archived role must return HX-Redirect to /admin/roles/."""
    await db.execute("UPDATE roles SET archived_at = NOW() WHERE id = $1", role_id)
    response = client.delete(
        f"/admin/roles/{role_id}/",
        headers={**AUTH_HEADERS, "HX-Request": "true"},
    )
    assert response.status_code == 200
    assert response.headers.get("HX-Redirect") == "/admin/roles/"


async def test_hard_delete_archived_role_non_htmx_redirects(client, db, role_id):
    """Non-HTMX delete of archived role must 303-redirect to /admin/roles/."""
    await db.execute("UPDATE roles SET archived_at = NOW() WHERE id = $1", role_id)
    response = client.delete(
        f"/admin/roles/{role_id}/",
        headers=AUTH_HEADERS,
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers.get("location") == "/admin/roles/"


async def test_roles_list_filters_by_org_name(client, role_id):
    response = client.get("/admin/roles/?org_q=Test", headers=AUTH_HEADERS)
    assert response.status_code == 200
    assert "Test Role" in response.text


async def test_roles_list_org_filter_excludes_nonmatching(client, role_id):
    response = client.get("/admin/roles/?org_q=NoSuchOrg", headers=AUTH_HEADERS)
    assert response.status_code == 200
    assert "Test Role" not in response.text


async def test_roles_list_org_filter_literal_percent(client, role_id):
    # A literal '%' in org_q must not act as a SQL wildcard
    response = client.get("/admin/roles/?org_q=%25", headers=AUTH_HEADERS)
    assert response.status_code == 200
    assert "Test Role" not in response.text


async def test_roles_list_title_and_org_combined(client, role_id):
    response = client.get("/admin/roles/?q=Test&org_q=Test", headers=AUTH_HEADERS)
    assert response.status_code == 200
    assert "Test Role" in response.text


async def test_roles_list_title_and_org_nonmatching_combo(client, role_id):
    response = client.get("/admin/roles/?q=Test&org_q=NoSuchOrg", headers=AUTH_HEADERS)
    assert response.status_code == 200
    assert "Test Role" not in response.text


async def test_roles_list_htmx_boost_returns_full_page(client):
    """Boosted navigation must return the full page layout, not a bare rows partial."""
    response = client.get(
        "/admin/roles/",
        headers={**AUTH_HEADERS, "HX-Request": "true", "HX-Boosted": "true"},
    )
    assert response.status_code == 200
    assert "admin-layout" in response.text


async def test_roles_list_htmx_request_returns_rows_partial(client):
    """Non-boosted HTMX request (filter/pagination) must return the rows partial only."""
    response = client.get(
        "/admin/roles/",
        headers={**AUTH_HEADERS, "HX-Request": "true"},
    )
    assert response.status_code == 200
    assert "admin-layout" not in response.text
