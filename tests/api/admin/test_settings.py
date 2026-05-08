"""Integration tests for admin settings views."""

import os
import re

import asyncpg
import pytest
from fastapi.testclient import TestClient

from src.api.main import app
from src.core.db import apply_schema, generate_id
from src.core.types import ORG_NAME_TYPES, PERSON_NAME_TYPES

pytestmark = pytest.mark.integration

AUTH_HEADERS = {
    "X-ExeDev-UserID": "usr_test",
    "X-ExeDev-Email": "admin@test.com",
}


@pytest.fixture
async def db():
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        pytest.skip("DATABASE_URL not set")
    conn = await asyncpg.connect(dsn)
    try:
        await apply_schema(conn)
        yield conn
    finally:
        await conn.close()


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


# --- Landing page ---

def test_settings_landing_returns_200(client):
    response = client.get("/admin/settings/", headers=AUTH_HEADERS)
    assert response.status_code == 200
    # Verify all 6 cards render
    for label in ("Link Types", "Identifier Types", "Organization Name Types",
                  "Person Name Types", "Address Types", "API Keys"):
        assert label in response.text
    # Read-only chips present
    assert "legal" in response.text
    assert "mailing" in response.text
    # Sidebar active section
    assert 'aria-current="page"' in response.text


def test_settings_landing_redirects_unauthenticated(client):
    response = client.get("/admin/settings/", follow_redirects=False)
    assert response.status_code in (302, 307)
    assert "/__exe.dev/login" in response.headers["location"]


def _name_types_section(response_text: str, heading: str) -> str:
    """Return the HTML between the named card's <h2> and its next </div>
    so the assertion only sees that card's badges (not unrelated 'legal'
    occurrences in other cards or elsewhere on the page).
    """
    # Locate the card by its <h2> text, then walk to the closing </div>
    # of the badges flex row that immediately follows the heading.
    h2 = re.search(rf">{re.escape(heading)}</h2>", response_text)
    assert h2 is not None, f"settings card {heading!r} not found"
    after = response_text[h2.end():]
    # Take a generous window — enough to span the badges row without
    # leaking into the next card.
    return after[:1000]


def test_settings_landing_renders_every_person_name_type_as_badge(client):
    """Every value in PERSON_NAME_TYPES must surface as a badge in the
    Person Name Types card. Guards against the pre-#135 rot where the
    settings page hardcoded only 5 of the 12 (then current) types and
    drifted silently as new types were added."""
    response = client.get("/admin/settings/", headers=AUTH_HEADERS)
    assert response.status_code == 200
    block = _name_types_section(response.text, "Person Name Types")
    for t in PERSON_NAME_TYPES:
        assert f'class="badge badge--inactive">{t}</span>' in block, (
            f"person_names type {t!r} missing from settings badge list"
        )


def test_settings_landing_renders_every_org_name_type_as_badge(client):
    response = client.get("/admin/settings/", headers=AUTH_HEADERS)
    assert response.status_code == 200
    block = _name_types_section(response.text, "Organization Name Types")
    for t in ORG_NAME_TYPES:
        assert f'class="badge badge--inactive">{t}</span>' in block, (
            f"organization_names type {t!r} missing from settings badge list"
        )


# --- Link Types page ---

def test_link_types_page_returns_200(client):
    response = client.get("/admin/settings/link-types/", headers=AUTH_HEADERS)
    assert response.status_code == 200
    assert "General" in response.text
    assert "Social" in response.text


def test_link_types_page_has_aria_current(client):
    """Link types sidebar item is marked aria-current on the link types page."""
    response = client.get("/admin/settings/link-types/", headers=AUTH_HEADERS)
    assert response.status_code == 200
    assert 'aria-current="page"' in response.text


def test_link_types_page_redirects_unauthenticated(client):
    response = client.get("/admin/settings/link-types/", follow_redirects=False)
    assert response.status_code in (302, 307)


# --- Identifier Types page ---

def test_identifier_types_page_returns_200(client):
    response = client.get("/admin/settings/identifier-types/", headers=AUTH_HEADERS)
    assert response.status_code == 200


def test_identifier_types_page_has_aria_current(client):
    """Identifier types sidebar item is marked aria-current on the identifier types page."""
    response = client.get("/admin/settings/identifier-types/", headers=AUTH_HEADERS)
    assert response.status_code == 200
    assert 'aria-current="page"' in response.text


# --- Link Type new-row ---

def test_link_type_new_row_general(client):
    response = client.get("/admin/settings/link-types/general/new-row/", headers=AUTH_HEADERS)
    assert response.status_code == 200
    assert "display_name" in response.text
    assert "slug" in response.text


def test_link_type_new_row_social(client):
    response = client.get("/admin/settings/link-types/social/new-row/", headers=AUTH_HEADERS)
    assert response.status_code == 200
    assert "display_name" in response.text


def test_link_type_new_row_invalid_scope(client):
    response = client.get("/admin/settings/link-types/bad/new-row/", headers=AUTH_HEADERS)
    assert response.status_code == 404


# --- Link Type create ---

async def test_create_general_link_type(client, db):
    slug = f"test-general-{generate_id()}"
    response = client.post(
        "/admin/settings/link-types/general/",
        headers=AUTH_HEADERS,
        data={"display_name": "Test General", "slug": slug},
    )
    assert response.status_code == 200
    assert slug in response.text
    await db.execute("DELETE FROM link_types WHERE slug=$1", slug)


async def test_create_social_link_type(client, db):
    slug = f"test-social-{generate_id()}"
    response = client.post(
        "/admin/settings/link-types/social/",
        headers=AUTH_HEADERS,
        data={"display_name": "Test Social", "slug": slug},
    )
    assert response.status_code == 200
    assert slug in response.text
    await db.execute("DELETE FROM link_types WHERE slug=$1", slug)


# --- Link Type edit-row ---

async def test_link_type_edit_row_get(client, db):
    lid = generate_id()
    await db.execute(
        "INSERT INTO link_types (id, display_name, slug, is_social) VALUES ($1, $2, $3, FALSE)",
        lid, "Edit Me", f"edit-me-{lid}",
    )
    try:
        response = client.get(
            f"/admin/settings/link-types/general/{lid}/edit-row/", headers=AUTH_HEADERS
        )
        assert response.status_code == 200
        assert "Edit Me" in response.text
    finally:
        await db.execute("DELETE FROM link_types WHERE id=$1", lid)


async def test_link_type_edit_row_post(client, db):
    lid = generate_id()
    await db.execute(
        "INSERT INTO link_types (id, display_name, slug, is_social) VALUES ($1, $2, $3, FALSE)",
        lid, "Before", f"before-{lid}",
    )
    try:
        response = client.post(
            f"/admin/settings/link-types/general/{lid}/edit-row/",
            headers=AUTH_HEADERS,
            data={"display_name": "After", "slug": f"after-{lid}"},
        )
        assert response.status_code == 200
        assert "After" in response.text
    finally:
        await db.execute("DELETE FROM link_types WHERE id=$1", lid)


async def test_link_type_read_row(client, db):
    lid = generate_id()
    await db.execute(
        "INSERT INTO link_types (id, display_name, slug, is_social) VALUES ($1, $2, $3, FALSE)",
        lid, "Read Me", f"read-me-{lid}",
    )
    try:
        response = client.get(
            f"/admin/settings/link-types/general/{lid}/read-row/", headers=AUTH_HEADERS
        )
        assert response.status_code == 200
        assert "Read Me" in response.text
    finally:
        await db.execute("DELETE FROM link_types WHERE id=$1", lid)


# --- Link Type delete ---

async def test_delete_general_link_type(client, db):
    lid = generate_id()
    await db.execute(
        "INSERT INTO link_types (id, display_name, slug, is_social) VALUES ($1, $2, $3, FALSE)",
        lid, "Delete Me", f"del-{lid}",
    )
    response = client.delete(
        f"/admin/settings/link-types/general/{lid}/", headers=AUTH_HEADERS
    )
    assert response.status_code == 200
    row = await db.fetchrow("SELECT id FROM link_types WHERE id=$1", lid)
    assert row is None


async def test_delete_link_type_in_use_htmx_returns_flash(client, db):
    """Delete of an in-use link type via HTMX returns 200 with error flash, row preserved."""
    lid = generate_id()
    await db.execute(
        "INSERT INTO link_types (id, display_name, slug, is_social) VALUES ($1, $2, $3, FALSE)",
        lid, "In Use Type", f"in-use-{lid}",
    )
    oid = generate_id()
    link_id = generate_id()
    await db.execute("INSERT INTO organizations (id, active) VALUES ($1, TRUE)", oid)
    await db.execute(
        "INSERT INTO links (id, entity_type, entity_id, url, link_type_id, is_active)"
        " VALUES ($1, 'organization', $2, 'https://example.com', $3, TRUE)",
        link_id, oid, lid,
    )
    try:
        response = client.delete(
            f"/admin/settings/link-types/general/{lid}/",
            headers={**AUTH_HEADERS, "HX-Request": "true"},
        )
        assert response.status_code == 200
        assert "HX-Trigger" in response.headers
        assert "showFlash" in response.headers["HX-Trigger"]
        # Row still exists
        row = await db.fetchrow("SELECT id FROM link_types WHERE id=$1", lid)
        assert row is not None
    finally:
        await db.execute("DELETE FROM links WHERE id=$1", link_id)
        await db.execute("DELETE FROM link_types WHERE id=$1", lid)
        await db.execute("DELETE FROM organizations WHERE id=$1", oid)


async def test_delete_link_type_in_use_non_htmx_returns_409(client, db):
    """Delete of an in-use link type without HTMX returns 409."""
    lid = generate_id()
    await db.execute(
        "INSERT INTO link_types (id, display_name, slug, is_social) VALUES ($1, $2, $3, FALSE)",
        lid, "In Use Type 409", f"in-use-409-{lid}",
    )
    oid = generate_id()
    link_id = generate_id()
    await db.execute("INSERT INTO organizations (id, active) VALUES ($1, TRUE)", oid)
    await db.execute(
        "INSERT INTO links (id, entity_type, entity_id, url, link_type_id, is_active)"
        " VALUES ($1, 'organization', $2, 'https://example.com', $3, TRUE)",
        link_id, oid, lid,
    )
    try:
        response = client.delete(
            f"/admin/settings/link-types/general/{lid}/", headers=AUTH_HEADERS
        )
        assert response.status_code == 409
    finally:
        await db.execute("DELETE FROM links WHERE id=$1", link_id)
        await db.execute("DELETE FROM link_types WHERE id=$1", lid)
        await db.execute("DELETE FROM organizations WHERE id=$1", oid)


async def test_create_link_type_non_htmx_redirects(client, db):
    """Non-HTMX POST create redirects to link-types page."""
    slug = f"test-nonhtmx-{generate_id()}"
    response = client.post(
        "/admin/settings/link-types/general/",
        headers=AUTH_HEADERS,  # no HX-Request header
        data={"display_name": "Non-HTMX Test", "slug": slug},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/admin/settings/link-types/"
    await db.execute("DELETE FROM link_types WHERE slug=$1", slug)


async def test_link_type_edit_row_post_non_htmx_redirects(client, db):
    """Non-HTMX POST edit redirects to link-types page."""
    lid = generate_id()
    await db.execute(
        "INSERT INTO link_types (id, display_name, slug, is_social) VALUES ($1, $2, $3, FALSE)",
        lid, "Non-HTMX Edit", f"nonhtmx-edit-{lid}",
    )
    try:
        response = client.post(
            f"/admin/settings/link-types/general/{lid}/edit-row/",
            headers=AUTH_HEADERS,  # no HX-Request header
            data={"display_name": "Non-HTMX Edited", "slug": f"nonhtmx-edited-{lid}"},
            follow_redirects=False,
        )
        assert response.status_code == 303
        assert response.headers["location"] == "/admin/settings/link-types/"
    finally:
        await db.execute("DELETE FROM link_types WHERE id=$1", lid)


async def test_identifier_type_edit_row_post_non_htmx_redirects(client, db):
    """Non-HTMX POST edit for identifier type redirects to identifier-types page."""
    iid = generate_id()
    await db.execute(
        "INSERT INTO entity_identifier_types (id, display_name, slug, full_name, entity_type)"
        " VALUES ($1, $2, $3, $4, $5)",
        iid, "Non-HTMX ID Edit", f"nonhtmx-id-{iid}", "Non-HTMX Full", "organization",
    )
    try:
        response = client.post(
            f"/admin/settings/identifier-types/{iid}/edit-row/",
            headers=AUTH_HEADERS,  # no HX-Request header
            data={
                "display_name": "Non-HTMX Edited ID",
                "slug": f"nonhtmx-edited-id-{iid}",
                "full_name": "Non-HTMX Edited Full",
                "entity_type": "person",
            },
            follow_redirects=False,
        )
        assert response.status_code == 303
        assert response.headers["location"] == "/admin/settings/identifier-types/"
    finally:
        await db.execute("DELETE FROM entity_identifier_types WHERE id=$1", iid)


def test_identifier_type_new_row(client):
    response = client.get("/admin/settings/identifier-types/new-row/", headers=AUTH_HEADERS)
    assert response.status_code == 200
    assert "display_name" in response.text
    assert "entity_type" in response.text


async def test_create_identifier_type_non_htmx_redirects(client, db):
    """Non-HTMX POST create for identifier type redirects to listing page."""
    slug = f"test-id-nonhtmx-{generate_id()}"
    response = client.post(
        "/admin/settings/identifier-types/",
        headers=AUTH_HEADERS,  # no HX-Request header
        data={
            "display_name": "Non-HTMX ID Create",
            "slug": slug,
            "full_name": "Non-HTMX ID Full",
            "entity_type": "organization",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/admin/settings/identifier-types/"
    await db.execute("DELETE FROM entity_identifier_types WHERE slug=$1", slug)


async def test_identifier_type_read_row(client, db):
    """GET read-row returns read partial (used by Cancel on edit form)."""
    iid = generate_id()
    await db.execute(
        "INSERT INTO entity_identifier_types (id, display_name, slug, full_name, entity_type)"
        " VALUES ($1, $2, $3, $4, $5)",
        iid, "Read Row ID", f"read-row-id-{iid}", "Read Row Full", "organization",
    )
    try:
        response = client.get(
            f"/admin/settings/identifier-types/{iid}/read-row/", headers=AUTH_HEADERS
        )
        assert response.status_code == 200
        assert "Read Row ID" in response.text
    finally:
        await db.execute("DELETE FROM entity_identifier_types WHERE id=$1", iid)


async def test_delete_identifier_type_in_use_htmx_returns_flash(client, db):
    """Delete in-use identifier type via HTMX returns 200 with error flash, row preserved."""
    iid = generate_id()
    await db.execute(
        "INSERT INTO entity_identifier_types (id, display_name, slug, full_name, entity_type)"
        " VALUES ($1, $2, $3, $4, $5)",
        iid, "In Use ID Type", f"in-use-id-{iid}", "In Use Full", "organization",
    )
    oid = generate_id()
    identifier_id = generate_id()
    await db.execute("INSERT INTO organizations (id, active) VALUES ($1, TRUE)", oid)
    await db.execute(
        "INSERT INTO identifiers (id, entity_id, entity_identifier_type_id, value)"
        " VALUES ($1, $2, $3, '99999')",
        identifier_id, oid, iid,
    )
    try:
        response = client.delete(
            f"/admin/settings/identifier-types/{iid}/",
            headers={**AUTH_HEADERS, "HX-Request": "true"},
        )
        assert response.status_code == 200
        assert "HX-Trigger" in response.headers
        assert "showFlash" in response.headers["HX-Trigger"]
        row = await db.fetchrow("SELECT id FROM entity_identifier_types WHERE id=$1", iid)
        assert row is not None
    finally:
        await db.execute("DELETE FROM identifiers WHERE id=$1", identifier_id)
        await db.execute("DELETE FROM entity_identifier_types WHERE id=$1", iid)
        await db.execute("DELETE FROM organizations WHERE id=$1", oid)


async def test_delete_identifier_type_in_use_non_htmx_returns_409(client, db):
    """Delete in-use identifier type without HTMX returns 409."""
    iid = generate_id()
    await db.execute(
        "INSERT INTO entity_identifier_types (id, display_name, slug, full_name, entity_type)"
        " VALUES ($1, $2, $3, $4, $5)",
        iid, "In Use ID 409", f"in-use-id-409-{iid}", "In Use Full 409", "organization",
    )
    oid = generate_id()
    identifier_id = generate_id()
    await db.execute("INSERT INTO organizations (id, active) VALUES ($1, TRUE)", oid)
    await db.execute(
        "INSERT INTO identifiers (id, entity_id, entity_identifier_type_id, value)"
        " VALUES ($1, $2, $3, '88888')",
        identifier_id, oid, iid,
    )
    try:
        response = client.delete(
            f"/admin/settings/identifier-types/{iid}/", headers=AUTH_HEADERS
        )
        assert response.status_code == 409
    finally:
        await db.execute("DELETE FROM identifiers WHERE id=$1", identifier_id)
        await db.execute("DELETE FROM entity_identifier_types WHERE id=$1", iid)
        await db.execute("DELETE FROM organizations WHERE id=$1", oid)


# --- Usage count ---

async def test_link_type_usage_count_shown(client, db):
    lid = generate_id()
    await db.execute(
        "INSERT INTO link_types (id, display_name, slug, is_social) VALUES ($1, $2, $3, FALSE)",
        lid, "Counted Type", f"counted-{lid}",
    )
    oid = generate_id()
    link_id = generate_id()
    await db.execute("INSERT INTO organizations (id, active) VALUES ($1, TRUE)", oid)
    await db.execute(
        "INSERT INTO links (id, entity_type, entity_id, url, link_type_id, is_active)"
        " VALUES ($1, 'organization', $2, 'https://example.com', $3, TRUE)",
        link_id, oid, lid,
    )
    try:
        response = client.get("/admin/settings/link-types/", headers=AUTH_HEADERS)
        assert response.status_code == 200
        assert ">1<" in response.text  # usage count visible
    finally:
        await db.execute("DELETE FROM links WHERE id=$1", link_id)
        await db.execute("DELETE FROM link_types WHERE id=$1", lid)
        await db.execute("DELETE FROM organizations WHERE id=$1", oid)


# --- Identifier Types ---

async def test_create_identifier_type(client, db):
    slug = f"test-id-{generate_id()}"
    response = client.post(
        "/admin/settings/identifier-types/",
        headers=AUTH_HEADERS,
        data={
            "display_name": "Test ID",
            "slug": slug,
            "full_name": "Test Identifier Full Name",
            "entity_type": "organization",
        },
    )
    assert response.status_code == 200
    assert slug in response.text
    await db.execute("DELETE FROM entity_identifier_types WHERE slug=$1", slug)


async def test_identifier_type_edit_row_get(client, db):
    iid = generate_id()
    await db.execute(
        "INSERT INTO entity_identifier_types (id, display_name, slug, full_name, entity_type)"
        " VALUES ($1, $2, $3, $4, $5)",
        iid, "Edit ID", f"edit-id-{iid}", "Edit ID Full", "organization",
    )
    try:
        response = client.get(
            f"/admin/settings/identifier-types/{iid}/edit-row/", headers=AUTH_HEADERS
        )
        assert response.status_code == 200
        assert "Edit ID" in response.text
    finally:
        await db.execute("DELETE FROM entity_identifier_types WHERE id=$1", iid)


async def test_identifier_type_edit_row_post(client, db):
    iid = generate_id()
    await db.execute(
        "INSERT INTO entity_identifier_types (id, display_name, slug, full_name, entity_type)"
        " VALUES ($1, $2, $3, $4, $5)",
        iid, "Before ID", f"before-id-{iid}", "Before Full", "organization",
    )
    try:
        response = client.post(
            f"/admin/settings/identifier-types/{iid}/edit-row/",
            headers=AUTH_HEADERS,
            data={
                "display_name": "After ID",
                "slug": f"after-id-{iid}",
                "full_name": "After Full",
                "entity_type": "person",
            },
        )
        assert response.status_code == 200
        assert "After ID" in response.text
    finally:
        await db.execute("DELETE FROM entity_identifier_types WHERE id=$1", iid)


async def test_delete_identifier_type(client, db):
    iid = generate_id()
    await db.execute(
        "INSERT INTO entity_identifier_types (id, display_name, slug, full_name, entity_type)"
        " VALUES ($1, $2, $3, $4, $5)",
        iid, "Delete ID", f"del-id-{iid}", "Delete Full", "organization",
    )
    response = client.delete(
        f"/admin/settings/identifier-types/{iid}/", headers=AUTH_HEADERS
    )
    assert response.status_code == 200
    row = await db.fetchrow("SELECT id FROM entity_identifier_types WHERE id=$1", iid)
    assert row is None


async def test_identifier_type_usage_count_shown(client, db):
    iid = generate_id()
    await db.execute(
        "INSERT INTO entity_identifier_types (id, display_name, slug, full_name, entity_type)"
        " VALUES ($1, $2, $3, $4, $5)",
        iid, "Counted ID", f"counted-id-{iid}", "Counted Full", "organization",
    )
    oid = generate_id()
    identifier_id = generate_id()
    await db.execute("INSERT INTO organizations (id, active) VALUES ($1, TRUE)", oid)
    await db.execute(
        "INSERT INTO identifiers (id, entity_id, entity_identifier_type_id, value)"
        " VALUES ($1, $2, $3, '12345')",
        identifier_id, oid, iid,
    )
    try:
        response = client.get("/admin/settings/identifier-types/", headers=AUTH_HEADERS)
        assert response.status_code == 200
        assert ">1<" in response.text
    finally:
        await db.execute("DELETE FROM identifiers WHERE id=$1", identifier_id)
        await db.execute("DELETE FROM entity_identifier_types WHERE id=$1", iid)
        await db.execute("DELETE FROM organizations WHERE id=$1", oid)
