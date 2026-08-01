"""Integration tests for admin roles views.

Requires DATABASE_URL. Run with:
    DATABASE_URL=<dsn> uv run pytest tests/api/admin/test_roles.py -m integration -v
"""

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from src.api.admin.deps import get_db
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


@pytest_asyncio.fixture(loop_scope="session")
async def role_id(db, org_id):
    rid = generate_id()
    await db.execute(
        "INSERT INTO roles (id, organization_id, title) VALUES ($1, $2, 'Test Role')",
        rid,
        org_id,
    )
    yield rid


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


@pytest_asyncio.fixture(loop_scope="session")
async def structural_role(db, org_id):
    """A role with a jurisdiction under `org_id`: role_type + jurisdiction + qualifier.

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


async def test_role_detail_shows_structural_fields(client, structural_role):
    """Detail surfaces role_type display_name, jurisdiction name, qualifier."""
    r = await client.get(f"/admin/roles/{structural_role['role_id']}/", headers=AUTH_HEADERS)
    assert r.status_code == 200
    assert 'id="structural-details"' in r.text
    assert "State Representative" in r.text  # role_type display_name (join)
    assert "Test Legislative District" in r.text  # jurisdiction name (join)
    assert "Position 1" in r.text  # qualifier column


async def test_role_detail_plain_role_hides_structural_section(client, role_id):
    """A plain role (no structural fields) renders no structural section."""
    r = await client.get(f"/admin/roles/{role_id}/", headers=AUTH_HEADERS)
    assert r.status_code == 200
    assert 'id="structural-details"' not in r.text


async def test_roles_list_shows_structural_badge(client, structural_role):
    """Roles with a jurisdiction are visually flagged in the list."""
    r = await client.get("/admin/roles/?org_q=Test", headers=AUTH_HEADERS)
    assert r.status_code == 200
    assert "badge--role-type" in r.text


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


# ---------------------------------------------------------------------------
# Slice 2 — create-form structural block + jurisdiction typeahead
# ---------------------------------------------------------------------------


async def test_jurisdiction_search_returns_matches(client, wa_ld_jurisdiction):
    r = await client.get("/admin/jurisdictions/search/?q=District 999", headers=AUTH_HEADERS)
    assert r.status_code == 200
    assert "Legislative District 999" in r.text
    assert wa_ld_jurisdiction in r.text


async def test_create_structural_role_synthesizes_title(
    client, db, org_id, wa_ld_jurisdiction, rt_rep_id
):
    """Role-with-jurisdiction create with empty title → PM synthesizes the canonical WA title."""
    r = await client.post(
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


async def test_create_structural_role_missing_office_rejected(
    client, db, org_id, wa_ld_jurisdiction
):
    """jurisdiction without a role_type violates chk_role_jurisdiction_needs_role_type."""
    r = await client.post(
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
    assert "requires a role type" in r.text
    n = await db.fetchval("SELECT count(*) FROM roles WHERE jurisdiction_id=$1", wa_ld_jurisdiction)
    assert n == 0


async def test_create_qualifier_without_jurisdiction_rejected(client, org_id, rt_rep_id):
    """qualifier without a jurisdiction violates chk_role_qualifier_needs_jurisdiction."""
    r = await client.post(
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
    """A role without a jurisdiction still requires a title."""
    r = await client.post(
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
    assert "Title is required for a role without a jurisdiction" in r.text


async def test_create_structural_role_nonwa_requires_manual_title(
    client, org_id, rt_rep_id, nonwa_jurisdiction
):
    """Synthesis returns None for a non-WA jurisdiction → manual title required."""
    r = await client.post(
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


async def test_create_structural_role_nonwa_manual_title_ok(
    client, db, org_id, rt_rep_id, nonwa_jurisdiction
):
    """A supplied title is respected for an unsynthesizable role (fill-when-absent)."""
    r = await client.post(
        "/admin/roles/new/",
        headers=AUTH_HEADERS,
        follow_redirects=False,
        data={
            "organization_id": org_id,
            "title": "County Commissioner Seat",
            "role_type_id": rt_rep_id,
            "jurisdiction_id": nonwa_jurisdiction,
            # rt_rep_id is a requires_qualifier office (#273), so a districted seat
            # needs a qualifier; the non-WA jurisdiction still can't synthesize a
            # title, so the manual one is kept.
            "qualifier": "District 3",
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


async def test_create_positionless_districted_seat_shows_error(
    client, db, org_id, rt_rep_id, nonwa_jurisdiction
):
    """A requires_qualifier office + jurisdiction with no qualifier is rejected with
    a friendly message (not a 500 from the DB trigger), and nothing is created (#273)."""
    r = await client.post(
        "/admin/roles/new/",
        headers=AUTH_HEADERS,
        follow_redirects=False,
        data={
            "organization_id": org_id,
            "title": "Rep Seat",
            "role_type_id": rt_rep_id,
            "jurisdiction_id": nonwa_jurisdiction,
            "qualifier": "",
            "notes": "",
        },
    )
    assert r.status_code == 200  # form re-rendered with an error, not 303/500
    assert "qualifier" in r.text.lower()
    count = await db.fetchval(
        "SELECT count(*) FROM roles WHERE organization_id=$1 AND jurisdiction_id=$2",
        org_id,
        nonwa_jurisdiction,
    )
    assert count == 0


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
        response = await client.get(f"/admin/roles/{role_id}/", headers=AUTH_HEADERS)
        assert response.status_code == 200
        assert "Test Person" in response.text
    finally:
        await db.execute("DELETE FROM role_assignments WHERE id = $1", ra_id)


async def test_roles_list_returns_200(client):
    response = await client.get("/admin/roles/", headers=AUTH_HEADERS)
    assert response.status_code == 200
    assert "roles" in response.text.lower()


async def test_roles_list_redirects_unauthenticated(client):
    response = await client.get("/admin/roles/", follow_redirects=False)
    assert response.status_code in (302, 307)
    assert "/__exe.dev/login" in response.headers["location"]


async def test_role_detail_returns_200(client, role_id):
    response = await client.get(f"/admin/roles/{role_id}/", headers=AUTH_HEADERS)
    assert response.status_code == 200
    assert "Test Role" in response.text


async def test_role_detail_404_for_unknown(client):
    response = await client.get(f"/admin/roles/{generate_id()}/", headers=AUTH_HEADERS)
    assert response.status_code == 404


async def test_create_role_post_redirects(client, org_id):
    response = await client.post(
        "/admin/roles/new/",
        headers=AUTH_HEADERS,
        data={"organization_id": org_id, "title": "New Role", "notes": ""},
        follow_redirects=False,
    )
    assert response.status_code in (302, 303)


async def test_archive_role(client, role_id):
    response = await client.post(
        f"/admin/roles/{role_id}/archive/",
        headers=AUTH_HEADERS,
        follow_redirects=False,
    )
    assert response.status_code in (302, 303)


async def test_archive_role_redirects_with_flash_query(client, role_id):
    """Archive redirects to detail with ?flash=archived."""
    response = await client.post(
        f"/admin/roles/{role_id}/archive/",
        headers=AUTH_HEADERS,
        follow_redirects=False,
    )
    assert response.status_code in (302, 303)
    assert response.headers["location"] == f"/admin/roles/{role_id}/?flash=archived"


async def test_archived_flash_renders_on_role_detail(client, role_id):
    """Role detail with ?flash=archived renders the success flash."""
    response = await client.get(f"/admin/roles/{role_id}/?flash=archived", headers=AUTH_HEADERS)
    assert response.status_code == 200
    assert "Role archived." in response.text
    assert "flash--success" in response.text
    assert "HX-Replace-Url" in response.headers
    assert "flash" not in response.headers["HX-Replace-Url"]


async def test_role_detail_unknown_flash_key_ignored(client, role_id):
    """GET role detail with ?flash=bogus returns 200 with no flash and no HX-Replace-Url."""
    response = await client.get(f"/admin/roles/{role_id}/?flash=bogus", headers=AUTH_HEADERS)
    assert response.status_code == 200
    assert "flash--success" not in response.text
    assert "HX-Replace-Url" not in response.headers


async def test_archive_already_archived_role_returns_409(client, db, role_id):
    """Re-archiving an already-archived role is rejected with 409."""
    await db.execute("UPDATE roles SET archived_at = NOW() WHERE id = $1", role_id)
    response = await client.post(
        f"/admin/roles/{role_id}/archive/",
        headers=AUTH_HEADERS,
        follow_redirects=False,
    )
    assert response.status_code == 409
    assert response.json()["detail"] == "Role is already archived"


async def test_hard_delete_requires_archive(client, role_id):
    response = await client.delete(f"/admin/roles/{role_id}/", headers=AUTH_HEADERS)
    assert response.status_code == 409


async def test_hard_delete_archived_role(client, db, role_id):
    await db.execute("UPDATE roles SET archived_at = NOW() WHERE id = $1", role_id)
    response = await client.delete(
        f"/admin/roles/{role_id}/",
        headers={**AUTH_HEADERS, "HX-Request": "true"},
        follow_redirects=False,
    )
    assert response.status_code == 200


async def test_hard_delete_archived_role_htmx_redirects(client, db, role_id):
    """HTMX delete of archived role must return HX-Redirect to /admin/roles/."""
    await db.execute("UPDATE roles SET archived_at = NOW() WHERE id = $1", role_id)
    response = await client.delete(
        f"/admin/roles/{role_id}/",
        headers={**AUTH_HEADERS, "HX-Request": "true"},
    )
    assert response.status_code == 200
    assert response.headers.get("HX-Redirect") == "/admin/roles/"


async def test_hard_delete_archived_role_non_htmx_redirects(client, db, role_id):
    """Non-HTMX delete of archived role must 303-redirect to /admin/roles/."""
    await db.execute("UPDATE roles SET archived_at = NOW() WHERE id = $1", role_id)
    response = await client.delete(
        f"/admin/roles/{role_id}/",
        headers=AUTH_HEADERS,
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers.get("location") == "/admin/roles/?flash=removed"


async def test_hard_delete_archived_role_writes_tombstone(client, db, role_id):
    """Hard delete of an archived role writes a deleted_entities tombstone and
    propagates a 'deleted' entity_changes row (issue #277)."""
    await db.execute("UPDATE roles SET archived_at = NOW() WHERE id = $1", role_id)
    response = await client.delete(
        f"/admin/roles/{role_id}/",
        headers=AUTH_HEADERS,
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert (
        await db.fetchval(
            "SELECT 1 FROM deleted_entities WHERE entity_type='role' AND entity_id=$1",
            role_id,
        )
        == 1
    )
    assert (
        await db.fetchval(
            "SELECT 1 FROM entity_changes"
            " WHERE entity_type='role' AND entity_id=$1 AND change_kind='deleted'",
            role_id,
        )
        == 1
    )


async def test_roles_list_filters_by_org_name(client, role_id):
    response = await client.get("/admin/roles/?org_q=Test", headers=AUTH_HEADERS)
    assert response.status_code == 200
    assert "Test Role" in response.text


async def test_roles_list_org_filter_excludes_nonmatching(client, role_id):
    response = await client.get("/admin/roles/?org_q=NoSuchOrg", headers=AUTH_HEADERS)
    assert response.status_code == 200
    assert "Test Role" not in response.text


async def test_roles_list_org_filter_literal_percent(client, role_id):
    # A literal '%' in org_q must not act as a SQL wildcard
    response = await client.get("/admin/roles/?org_q=%25", headers=AUTH_HEADERS)
    assert response.status_code == 200
    assert "Test Role" not in response.text


async def test_roles_list_title_and_org_combined(client, role_id):
    response = await client.get("/admin/roles/?q=Test&org_q=Test", headers=AUTH_HEADERS)
    assert response.status_code == 200
    assert "Test Role" in response.text


async def test_roles_list_title_and_org_nonmatching_combo(client, role_id):
    response = await client.get("/admin/roles/?q=Test&org_q=NoSuchOrg", headers=AUTH_HEADERS)
    assert response.status_code == 200
    assert "Test Role" not in response.text


async def test_roles_list_htmx_boost_returns_full_page(client):
    """Boosted navigation must return the full page layout, not a bare rows partial."""
    response = await client.get(
        "/admin/roles/",
        headers={**AUTH_HEADERS, "HX-Request": "true", "HX-Boosted": "true"},
    )
    assert response.status_code == 200
    assert "admin-layout" in response.text


async def test_roles_list_htmx_request_returns_rows_partial(client):
    """Non-boosted HTMX request (filter/pagination) must return the rows partial only."""
    response = await client.get(
        "/admin/roles/",
        headers={**AUTH_HEADERS, "HX-Request": "true"},
    )
    assert response.status_code == 200
    assert "admin-layout" not in response.text


# ---------------------------------------------------------------------------
# Slice 3 — inline structural editor + title gating
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture(loop_scope="session")
async def wa_structural_role(db, org_id):
    """A synthesizable WA role (usa-wa-ld-998) for title re-synthesis tests."""
    jid = generate_id()
    rid = generate_id()
    type_id = await db.fetchval(
        "SELECT id FROM jurisdiction_types WHERE slug='legislative_district'"
    )
    rt_id = await db.fetchval("SELECT id FROM role_types WHERE slug='state_representative'")
    await db.execute(
        "INSERT INTO jurisdictions (id, slug, name, type_id) VALUES ($1,$2,$3,$4)",
        jid,
        "usa-wa-ld-998",
        "Legislative District 998",
        type_id,
    )
    await db.execute(
        "INSERT INTO roles"
        " (id, organization_id, title, role_type_id, jurisdiction_id, qualifier)"
        " VALUES ($1,$2,$3,$4,$5,$6)",
        rid,
        org_id,
        "Washington State Representative, LD-998, Position 1",
        rt_id,
        jid,
        "Position 1",
    )
    yield {"role_id": rid, "jur_id": jid, "rt_id": rt_id}


async def test_structural_inline_edit_form_renders(client, structural_role):
    r = await client.get(
        f"/admin/roles/{structural_role['role_id']}/inline/structural/edit/", headers=AUTH_HEADERS
    )
    assert r.status_code == 200
    assert "State Representative" in r.text  # office <option>
    assert 'name="jurisdiction_id"' in r.text
    assert 'name="qualifier"' in r.text


async def test_structural_inline_edit_form_has_jurisdiction_clear(client, structural_role):
    """#358: jurisdiction picker exposes a "×" clear button wired to the factory."""
    r = await client.get(
        f"/admin/roles/{structural_role['role_id']}/inline/structural/edit/", headers=AUTH_HEADERS
    )
    assert r.status_code == 200
    assert 'id="structural-jurisdiction-clear"' in r.text
    assert "data-typeahead-clear" in r.text
    assert "clearButtonId: 'structural-jurisdiction-clear'" in r.text


async def test_structural_inline_clears_jurisdiction_keeping_role_type(client, db, structural_role):
    """#358: an empty jurisdiction_id nulls the jurisdiction while the role type stays."""
    r = await client.post(
        f"/admin/roles/{structural_role['role_id']}/inline/structural/",
        data={
            "role_type_id": structural_role["rt_id"],
            "jurisdiction_id": "",
            "qualifier": "",
        },
        headers=AUTH_HEADERS,
    )
    assert r.status_code == 200
    row = await db.fetchrow(
        "SELECT role_type_id, jurisdiction_id FROM roles WHERE id=$1",
        structural_role["role_id"],
    )
    assert row["jurisdiction_id"] is None
    assert row["role_type_id"] == structural_role["rt_id"]


async def test_structural_inline_update_qualifier_persists(client, db, structural_role):
    """Non-WA role: qualifier updates; unsynthesizable title left untouched."""
    r = await client.post(
        f"/admin/roles/{structural_role['role_id']}/inline/structural/",
        headers={**AUTH_HEADERS, "HX-Request": "true"},
        data={
            "role_type_id": structural_role["rt_id"],
            "jurisdiction_id": structural_role["jur_id"],
            "qualifier": "Position 2",
        },
    )
    assert r.status_code == 200
    row = await db.fetchrow(
        "SELECT qualifier, title FROM roles WHERE id=$1", structural_role["role_id"]
    )
    assert row["qualifier"] == "Position 2"
    assert row["title"] == "WA Rep Seat One"


async def test_structural_inline_wa_title_resynthesized(client, db, wa_structural_role):
    """WA role: changing the tuple regenerates the curated title."""
    r = await client.post(
        f"/admin/roles/{wa_structural_role['role_id']}/inline/structural/",
        headers={**AUTH_HEADERS, "HX-Request": "true"},
        data={
            "role_type_id": wa_structural_role["rt_id"],
            "jurisdiction_id": wa_structural_role["jur_id"],
            "qualifier": "Position 2",
        },
    )
    assert r.status_code == 200
    row = await db.fetchrow(
        "SELECT title, qualifier FROM roles WHERE id=$1", wa_structural_role["role_id"]
    )
    assert row["qualifier"] == "Position 2"
    assert row["title"] == "Washington State Representative, LD-998, Position 2"


async def test_structural_inline_qualifier_without_jurisdiction_rejected(
    client, db, structural_role
):
    r = await client.post(
        f"/admin/roles/{structural_role['role_id']}/inline/structural/",
        headers={**AUTH_HEADERS, "HX-Request": "true"},
        data={
            "role_type_id": structural_role["rt_id"],
            "jurisdiction_id": "",
            "qualifier": "Position 9",
        },
    )
    assert r.status_code == 200
    row = await db.fetchrow(
        "SELECT qualifier, jurisdiction_id FROM roles WHERE id=$1", structural_role["role_id"]
    )
    assert row["qualifier"] == "Position 1"  # unchanged
    assert row["jurisdiction_id"] == structural_role["jur_id"]


async def test_structural_inline_positionless_seat_rejected(client, db, structural_role):
    """Editing a requires_qualifier office to drop its qualifier is rejected with a
    friendly error (not a raw trigger 500), leaving the seat unchanged (#273)."""
    r = await client.post(
        f"/admin/roles/{structural_role['role_id']}/inline/structural/",
        headers={**AUTH_HEADERS, "HX-Request": "true"},
        data={
            "role_type_id": structural_role["rt_id"],
            "jurisdiction_id": structural_role["jur_id"],
            "qualifier": "",
        },
    )
    assert r.status_code == 200
    row = await db.fetchrow("SELECT qualifier FROM roles WHERE id=$1", structural_role["role_id"])
    assert row["qualifier"] == "Position 1"  # unchanged


async def test_structural_title_edit_post_rejected(client, db, structural_role):
    """A curated role's title is PM-owned — the manual title editor refuses to change it."""
    r = await client.post(
        f"/admin/roles/{structural_role['role_id']}/inline/title/",
        headers={**AUTH_HEADERS, "HX-Request": "true"},
        data={"title": "Hand Edited Title"},
    )
    assert r.status_code == 200
    title = await db.fetchval("SELECT title FROM roles WHERE id=$1", structural_role["role_id"])
    assert title == "WA Rep Seat One"


async def test_structural_detail_hides_title_edit_button(client, structural_role, role_id):
    structural = await client.get(
        f"/admin/roles/{structural_role['role_id']}/", headers=AUTH_HEADERS
    )
    plain = await client.get(f"/admin/roles/{role_id}/", headers=AUTH_HEADERS)
    assert "inline/title/edit/" not in structural.text
    assert "inline/title/edit/" in plain.text


# ---------------------------------------------------------------------------
# CR round 1 — follow-up fixes
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture(loop_scope="session")
async def promotable_role(db, org_id):
    """A fresh plain role that a test may convert to a role with a jurisdiction
    (isolated, not shared)."""
    rid = generate_id()
    await db.execute(
        "INSERT INTO roles (id, organization_id, title) VALUES ($1,$2,'Promotable Role')",
        rid,
        org_id,
    )
    yield rid


@pytest_asyncio.fixture(loop_scope="session")
async def demotable_role(db, org_id):
    """A fresh WA role (usa-wa-ld-997) that a test may demote to a plain role."""
    jid = generate_id()
    rid = generate_id()
    type_id = await db.fetchval(
        "SELECT id FROM jurisdiction_types WHERE slug='legislative_district'"
    )
    rt_id = await db.fetchval("SELECT id FROM role_types WHERE slug='state_representative'")
    await db.execute(
        "INSERT INTO jurisdictions (id, slug, name, type_id) VALUES ($1,$2,$3,$4)",
        jid,
        "usa-wa-ld-997",
        "Legislative District 997",
        type_id,
    )
    await db.execute(
        "INSERT INTO roles"
        " (id, organization_id, title, role_type_id, jurisdiction_id, qualifier)"
        " VALUES ($1,$2,$3,$4,$5,$6)",
        rid,
        org_id,
        "Washington State Representative, LD-997, Position 1",
        rt_id,
        jid,
        "Position 1",
    )
    yield {"role_id": rid, "jur_id": jid, "rt_id": rt_id}


async def test_structural_inline_error_preserves_cleared_jurisdiction(client, structural_role):
    """On a validation error the re-rendered form reflects the *submitted* values —
    a cleared jurisdiction must not be silently restored from the DB (#264 CR-1)."""
    r = await client.post(
        f"/admin/roles/{structural_role['role_id']}/inline/structural/",
        headers={**AUTH_HEADERS, "HX-Request": "true"},
        data={
            "role_type_id": structural_role["rt_id"],
            "jurisdiction_id": "",  # cleared
            "qualifier": "Position 9",
        },
    )
    assert r.status_code == 200
    assert structural_role["jur_id"] not in r.text  # old jurisdiction not restored
    assert "Position 9" in r.text  # submitted qualifier preserved


async def test_structural_inline_add_to_plain_role(
    client, db, promotable_role, rt_rep_id, wa_ld_jurisdiction
):
    """Adding structural fields to a plain role sets the tuple and synthesizes the WA title."""
    r = await client.post(
        f"/admin/roles/{promotable_role}/inline/structural/",
        headers={**AUTH_HEADERS, "HX-Request": "true"},
        data={
            "role_type_id": rt_rep_id,
            "jurisdiction_id": wa_ld_jurisdiction,
            "qualifier": "Position 7",
        },
    )
    assert r.status_code == 200
    row = await db.fetchrow(
        "SELECT role_type_id, jurisdiction_id, qualifier, title FROM roles WHERE id=$1",
        promotable_role,
    )
    assert row["role_type_id"] == rt_rep_id
    assert row["jurisdiction_id"] == wa_ld_jurisdiction
    assert row["qualifier"] == "Position 7"
    assert row["title"] == "Washington State Representative, LD-999, Position 7"


async def test_structural_inline_demote_to_plain(client, db, demotable_role):
    """Clearing the office demotes a role with a jurisdiction to a plain role (all three
    columns NULL), retains the old title, and the flash flags that the title was kept
    (#264 CR-1)."""
    r = await client.post(
        f"/admin/roles/{demotable_role['role_id']}/inline/structural/",
        headers={**AUTH_HEADERS, "HX-Request": "true"},
        data={"role_type_id": "", "jurisdiction_id": "", "qualifier": ""},
    )
    assert r.status_code == 200
    row = await db.fetchrow(
        "SELECT role_type_id, jurisdiction_id, qualifier, title FROM roles WHERE id=$1",
        demotable_role["role_id"],
    )
    assert row["role_type_id"] is None
    assert row["jurisdiction_id"] is None
    assert row["qualifier"] is None
    assert row["title"] == "Washington State Representative, LD-997, Position 1"  # retained
    assert "retained" in r.headers.get("HX-Trigger", "")


async def test_create_wa_structural_role_ignores_supplied_title(
    client, db, org_id, wa_ld_jurisdiction, rt_rep_id
):
    """A supplied title is ignored for a fully-qualified WA role — PM always
    synthesizes the canonical title (#264 CR-1, directive 5)."""
    r = await client.post(
        "/admin/roles/new/",
        headers=AUTH_HEADERS,
        follow_redirects=False,
        data={
            "organization_id": org_id,
            "title": "My Custom Override",
            "role_type_id": rt_rep_id,
            "jurisdiction_id": wa_ld_jurisdiction,
            "qualifier": "Position 5",
            "notes": "",
        },
    )
    assert r.status_code == 303
    title = await db.fetchval(
        "SELECT title FROM roles WHERE organization_id=$1 AND jurisdiction_id=$2 AND qualifier=$3",
        org_id,
        wa_ld_jurisdiction,
        "Position 5",
    )
    assert title == "Washington State Representative, LD-999, Position 5"
