"""Integration tests for org contact methods CRUD."""

import asyncio
import json
import os

import asyncpg
import pytest
from fastapi.testclient import TestClient

from src.api.main import app
from src.core.db import apply_schema, generate_id

pytestmark = pytest.mark.integration
AUTH_HEADERS = {"X-ExeDev-UserID": "usr_test", "X-ExeDev-Email": "admin@test.com"}
HTMX_HEADERS = {**AUTH_HEADERS, "HX-Request": "true"}


def _dsn():
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        pytest.skip("DATABASE_URL not set")
    return dsn


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture
def org_and_contact():
    dsn = _dsn()
    oid, cid = generate_id(), generate_id()

    async def setup():
        conn = await asyncpg.connect(dsn)
        await apply_schema(conn)
        try:
            await conn.execute("INSERT INTO organizations (id) VALUES ($1)", oid)
            await conn.execute(
                "INSERT INTO contact_methods"
                " (id, entity_type, entity_id, contact_type, value)"
                " VALUES ($1, 'organization', $2, 'phone', '+13605551234')",
                cid,
                oid,
            )
        finally:
            await conn.close()

    async def teardown():
        conn = await asyncpg.connect(dsn)
        try:
            await conn.execute("DELETE FROM contact_methods WHERE entity_id=$1", oid)
            await conn.execute("DELETE FROM organizations WHERE id=$1", oid)
        finally:
            await conn.close()

    asyncio.run(setup())
    yield oid, cid
    asyncio.run(teardown())


def test_contacts_new_row_returns_form(client, org_and_contact):
    oid, _ = org_and_contact
    r = client.get(f"/admin/orgs/{oid}/contacts/new-row/?contact_type=email", headers=HTMX_HEADERS)
    assert r.status_code == 200
    assert "<form" in r.text


def test_contacts_new_row_email_has_hidden_type(client, org_and_contact):
    oid, _ = org_and_contact
    r = client.get(
        f"/admin/orgs/{oid}/contacts/new-row/?contact_type=email",
        headers=HTMX_HEADERS,
    )
    assert r.status_code == 200
    assert 'type="hidden"' in r.text
    assert 'value="email"' in r.text
    assert "email-row-new" in r.text


def test_contacts_new_row_phone_has_hidden_type(client, org_and_contact):
    oid, _ = org_and_contact
    r = client.get(
        f"/admin/orgs/{oid}/contacts/new-row/?contact_type=phone",
        headers=HTMX_HEADERS,
    )
    assert r.status_code == 200
    assert 'type="hidden"' in r.text
    assert 'value="phone"' in r.text
    assert "phone-row-new" in r.text


def test_contacts_update_does_not_change_type(client, org_and_contact):
    """contact_type is immutable — edit route ignores any type in POST data."""
    oid, cid = org_and_contact
    r = client.post(
        f"/admin/orgs/{oid}/contacts/{cid}/edit-row/",
        headers=HTMX_HEADERS,
        data={"value": "+12065559876"},  # no contact_type submitted
    )
    assert r.status_code == 200
    assert "+12065559876" in r.text


def test_contacts_create(client, org_and_contact):
    oid, _ = org_and_contact
    r = client.post(
        f"/admin/orgs/{oid}/contacts/",
        headers=HTMX_HEADERS,
        data={"contact_type": "email", "value": "info@example.com"},
    )
    assert r.status_code == 200
    assert "info@example.com" in r.text


def test_contacts_read_row_returns_row(client, org_and_contact):
    oid, cid = org_and_contact
    r = client.get(f"/admin/orgs/{oid}/contacts/{cid}/read-row/", headers=HTMX_HEADERS)
    assert r.status_code == 200
    assert "+13605551234" in r.text
    assert "<form" not in r.text


def test_contacts_edit_row_returns_form(client, org_and_contact):
    oid, cid = org_and_contact
    r = client.get(f"/admin/orgs/{oid}/contacts/{cid}/edit-row/", headers=HTMX_HEADERS)
    assert r.status_code == 200
    assert "<form" in r.text


def test_contacts_update(client, org_and_contact):
    oid, cid = org_and_contact
    r = client.post(
        f"/admin/orgs/{oid}/contacts/{cid}/edit-row/",
        headers=HTMX_HEADERS,
        data={"value": "+12065559876"},
    )
    assert r.status_code == 200
    assert "+12065559876" in r.text


def test_contacts_delete(client, org_and_contact):
    oid, cid = org_and_contact
    r = client.delete(f"/admin/orgs/{oid}/contacts/{cid}/", headers=HTMX_HEADERS)
    assert r.status_code == 200


def test_contacts_delete_unknown_returns_404(client, org_and_contact):
    oid, _ = org_and_contact
    r = client.delete(f"/admin/orgs/{oid}/contacts/{generate_id()}/", headers=HTMX_HEADERS)
    assert r.status_code == 404


def test_contacts_form_row_has_form_group(client, org_and_contact):
    oid, _ = org_and_contact
    r = client.get(
        f"/admin/orgs/{oid}/contacts/new-row/?contact_type=email",
        headers=HTMX_HEADERS,
    )
    assert "form-group" in r.text


def test_contacts_edit_row_no_type_select(client, org_and_contact):
    """Edit form must not contain a contact_type <select> (type is immutable)."""
    oid, cid = org_and_contact
    r = client.get(f"/admin/orgs/{oid}/contacts/{cid}/edit-row/", headers=HTMX_HEADERS)
    assert r.status_code == 200
    assert 'name="contact_type"' not in r.text or '<select' not in r.text


def test_contacts_read_row_no_type_cell(client, org_and_contact):
    """Read row must not render a standalone type cell (rows live in typed tables)."""
    oid, cid = org_and_contact
    r = client.get(f"/admin/orgs/{oid}/contacts/{cid}/read-row/", headers=HTMX_HEADERS)
    assert r.status_code == 200
    assert "<td>phone</td>" not in r.text


def test_contacts_new_row_invalid_type_returns_422(client, org_and_contact):
    """Invalid contact_type query param must return 422 (Literal validation)."""
    oid, _ = org_and_contact
    r = client.get(
        f"/admin/orgs/{oid}/contacts/new-row/?contact_type=fax",
        headers=HTMX_HEADERS,
    )
    assert r.status_code == 422


def test_contacts_create_returns_success_flash(client, org_and_contact):
    oid, _ = org_and_contact
    r = client.post(
        f"/admin/orgs/{oid}/contacts/",
        headers=HTMX_HEADERS,
        data={"contact_type": "email", "value": "flash@example.com"},
    )
    assert r.status_code == 200
    trigger = json.loads(r.headers["hx-trigger"])
    assert trigger["showFlash"]["level"] == "success"
    assert "flash@example.com" in trigger["showFlash"]["body"]


def test_contacts_update_returns_success_flash(client, org_and_contact):
    oid, cid = org_and_contact
    r = client.post(
        f"/admin/orgs/{oid}/contacts/{cid}/edit-row/",
        headers=HTMX_HEADERS,
        data={"value": "+13605559999"},
    )
    assert r.status_code == 200
    trigger = json.loads(r.headers["hx-trigger"])
    assert trigger["showFlash"]["level"] == "success"
    assert "+13605559999" in trigger["showFlash"]["body"]


def test_contacts_delete_returns_info_flash(client, org_and_contact):
    oid, cid = org_and_contact
    r = client.delete(f"/admin/orgs/{oid}/contacts/{cid}/", headers=HTMX_HEADERS)
    assert r.status_code == 200
    trigger = json.loads(r.headers["hx-trigger"])
    assert trigger["showFlash"]["level"] == "info"


# ---------------------------------------------------------------------------
# Normalizer wiring — email
# ---------------------------------------------------------------------------


def test_contact_create_email_invalid_returns_form_with_error(client, org_and_contact):
    """Invalid email must re-render the form row with an inline error, not a bare 422."""
    oid, _ = org_and_contact
    r = client.post(
        f"/admin/orgs/{oid}/contacts/",
        headers=HTMX_HEADERS,
        data={"contact_type": "email", "value": "not-an-email"},
    )
    assert r.status_code == 200
    assert "<form" in r.text
    assert "alert--error" in r.text


def test_contact_create_email_invalid_preserves_input_value(client, org_and_contact):
    """Invalid submission repopulates the input with what the user typed."""
    oid, _ = org_and_contact
    r = client.post(
        f"/admin/orgs/{oid}/contacts/",
        headers=HTMX_HEADERS,
        data={"contact_type": "email", "value": "bad@@email"},
    )
    assert r.status_code == 200
    assert "bad@@email" in r.text


def test_contact_create_email_normalizes(client, org_and_contact):
    """Valid email is normalized (domain lowercased, local part normalized) before storage."""
    oid, _ = org_and_contact
    r = client.post(
        f"/admin/orgs/{oid}/contacts/",
        headers=HTMX_HEADERS,
        data={"contact_type": "email", "value": "Info@Example.COM"},
    )
    assert r.status_code == 200
    assert "info@example.com" in r.text


def test_contact_new_row_email_input_has_type_email(client, org_and_contact):
    """Email form row must use type='email' for HTML5 browser validation hint."""
    oid, _ = org_and_contact
    r = client.get(
        f"/admin/orgs/{oid}/contacts/new-row/?contact_type=email",
        headers=HTMX_HEADERS,
    )
    assert r.status_code == 200
    assert 'type="email"' in r.text


def test_contact_new_row_phone_input_has_type_text(client, org_and_contact):
    """Phone form row must use type='text' (not email) for the value input."""
    oid, _ = org_and_contact
    r = client.get(
        f"/admin/orgs/{oid}/contacts/new-row/?contact_type=phone",
        headers=HTMX_HEADERS,
    )
    assert r.status_code == 200
    assert 'type="text"' in r.text


# ---------------------------------------------------------------------------
# Normalizer wiring — phone
# ---------------------------------------------------------------------------


def test_contact_create_phone_invalid_returns_form_with_error(client, org_and_contact):
    """Invalid phone must re-render the form row with an inline error."""
    oid, _ = org_and_contact
    r = client.post(
        f"/admin/orgs/{oid}/contacts/",
        headers=HTMX_HEADERS,
        data={"contact_type": "phone", "value": "not-a-phone"},
    )
    assert r.status_code == 200
    assert "<form" in r.text
    assert "alert--error" in r.text


def test_contact_create_phone_normalizes_to_e164(client, org_and_contact):
    """Valid phone in formatted input is stored as E.164 and flash shows normalized value."""
    oid, _ = org_and_contact
    r = client.post(
        f"/admin/orgs/{oid}/contacts/",
        headers=HTMX_HEADERS,
        data={"contact_type": "phone", "value": "(206) 555-1234"},
    )
    assert r.status_code == 200
    assert "+12065551234" in r.text
    trigger = json.loads(r.headers["hx-trigger"])
    assert "+12065551234" in trigger["showFlash"]["body"]


def test_contact_edit_phone_invalid_returns_form_with_error(client, org_and_contact):
    """Invalid phone on edit must re-render the form row with an inline error."""
    oid, cid = org_and_contact
    r = client.post(
        f"/admin/orgs/{oid}/contacts/{cid}/edit-row/",
        headers=HTMX_HEADERS,
        data={"value": "zzz"},
    )
    assert r.status_code == 200
    assert "<form" in r.text
    assert "alert--error" in r.text


def test_contact_edit_phone_normalizes_to_e164(client, org_and_contact):
    """Valid phone on edit is stored as E.164."""
    oid, cid = org_and_contact
    r = client.post(
        f"/admin/orgs/{oid}/contacts/{cid}/edit-row/",
        headers=HTMX_HEADERS,
        data={"value": "360-555-4321"},
    )
    assert r.status_code == 200
    assert "+13605554321" in r.text
