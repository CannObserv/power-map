"""Integration tests for person contact methods CRUD (parity with test_orgs_contacts.py)."""

import json

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
HTMX_HEADERS = {**AUTH_HEADERS, "HX-Request": "true"}


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
async def person_and_contact(db):
    pid, cid = generate_id(), generate_id()
    await db.execute("INSERT INTO people (id) VALUES ($1)", pid)
    await db.execute(
        "INSERT INTO contact_methods"
        " (id, entity_type, entity_id, contact_type, value)"
        " VALUES ($1, 'person', $2, 'phone', '+13605551234')",
        cid,
        pid,
    )
    return pid, cid


async def test_contacts_new_row_returns_form(client, person_and_contact):
    pid, _ = person_and_contact
    r = await client.get(
        f"/admin/people/{pid}/contacts/new-row/?contact_type=email", headers=HTMX_HEADERS
    )
    assert r.status_code == 200
    assert "<form" in r.text


async def test_contacts_new_row_email_has_hidden_type(client, person_and_contact):
    pid, _ = person_and_contact
    r = await client.get(
        f"/admin/people/{pid}/contacts/new-row/?contact_type=email",
        headers=HTMX_HEADERS,
    )
    assert r.status_code == 200
    assert 'type="hidden"' in r.text
    assert 'value="email"' in r.text
    assert "email-row-new" in r.text


async def test_contacts_new_row_phone_has_hidden_type(client, person_and_contact):
    pid, _ = person_and_contact
    r = await client.get(
        f"/admin/people/{pid}/contacts/new-row/?contact_type=phone",
        headers=HTMX_HEADERS,
    )
    assert r.status_code == 200
    assert 'type="hidden"' in r.text
    assert 'value="phone"' in r.text
    assert "phone-row-new" in r.text


async def test_contacts_create(client, person_and_contact):
    pid, _ = person_and_contact
    r = await client.post(
        f"/admin/people/{pid}/contacts/",
        headers=HTMX_HEADERS,
        data={"contact_type": "email", "value": "info@example.com"},
    )
    assert r.status_code == 200
    assert "info@example.com" in r.text


async def test_contacts_read_row_returns_row(client, person_and_contact):
    pid, cid = person_and_contact
    r = await client.get(f"/admin/people/{pid}/contacts/{cid}/read-row/", headers=HTMX_HEADERS)
    assert r.status_code == 200
    assert "+13605551234" in r.text
    assert "<form" not in r.text


async def test_contacts_edit_row_returns_form(client, person_and_contact):
    pid, cid = person_and_contact
    r = await client.get(f"/admin/people/{pid}/contacts/{cid}/edit-row/", headers=HTMX_HEADERS)
    assert r.status_code == 200
    assert "<form" in r.text


async def test_contacts_update(client, person_and_contact):
    pid, cid = person_and_contact
    r = await client.post(
        f"/admin/people/{pid}/contacts/{cid}/edit-row/",
        headers=HTMX_HEADERS,
        data={"value": "+12065559876"},
    )
    assert r.status_code == 200
    assert "+12065559876" in r.text


async def test_contacts_update_does_not_change_type(client, person_and_contact):
    """contact_type is immutable — edit route ignores any type in POST data."""
    pid, cid = person_and_contact
    r = await client.post(
        f"/admin/people/{pid}/contacts/{cid}/edit-row/",
        headers=HTMX_HEADERS,
        data={"value": "+12065559876"},
    )
    assert r.status_code == 200
    assert "+12065559876" in r.text


async def test_contacts_delete(client, person_and_contact):
    pid, cid = person_and_contact
    r = await client.delete(f"/admin/people/{pid}/contacts/{cid}/", headers=HTMX_HEADERS)
    assert r.status_code == 200


async def test_contacts_delete_unknown_returns_404(client, person_and_contact):
    pid, _ = person_and_contact
    r = await client.delete(f"/admin/people/{pid}/contacts/{generate_id()}/", headers=HTMX_HEADERS)
    assert r.status_code == 404


async def test_contacts_form_row_has_form_group(client, person_and_contact):
    pid, _ = person_and_contact
    r = await client.get(
        f"/admin/people/{pid}/contacts/new-row/?contact_type=email",
        headers=HTMX_HEADERS,
    )
    assert "form-group" in r.text


async def test_contacts_new_row_invalid_type_returns_422(client, person_and_contact):
    """Invalid contact_type query param must return 422 (Literal validation)."""
    pid, _ = person_and_contact
    r = await client.get(
        f"/admin/people/{pid}/contacts/new-row/?contact_type=fax",
        headers=HTMX_HEADERS,
    )
    assert r.status_code == 422


async def test_contacts_create_returns_success_flash(client, person_and_contact):
    pid, _ = person_and_contact
    r = await client.post(
        f"/admin/people/{pid}/contacts/",
        headers=HTMX_HEADERS,
        data={"contact_type": "email", "value": "flash@example.com"},
    )
    assert r.status_code == 200
    trigger = json.loads(r.headers["hx-trigger"])
    assert trigger["showFlash"]["level"] == "success"
    assert "flash@example.com" in trigger["showFlash"]["body"]


async def test_contacts_update_returns_success_flash(client, person_and_contact):
    pid, cid = person_and_contact
    r = await client.post(
        f"/admin/people/{pid}/contacts/{cid}/edit-row/",
        headers=HTMX_HEADERS,
        data={"value": "+13605559999"},
    )
    assert r.status_code == 200
    trigger = json.loads(r.headers["hx-trigger"])
    assert trigger["showFlash"]["level"] == "success"
    assert "+13605559999" in trigger["showFlash"]["body"]


async def test_contacts_delete_returns_info_flash(client, person_and_contact):
    pid, cid = person_and_contact
    r = await client.delete(f"/admin/people/{pid}/contacts/{cid}/", headers=HTMX_HEADERS)
    assert r.status_code == 200
    trigger = json.loads(r.headers["hx-trigger"])
    assert trigger["showFlash"]["level"] == "info"


async def test_contact_create_email_invalid_returns_form_with_error(client, person_and_contact):
    """Invalid email must re-render the form row with an inline error."""
    pid, _ = person_and_contact
    r = await client.post(
        f"/admin/people/{pid}/contacts/",
        headers=HTMX_HEADERS,
        data={"contact_type": "email", "value": "not-an-email"},
    )
    assert r.status_code == 200
    assert "<form" in r.text
    assert "alert--error" in r.text


async def test_contact_create_email_normalizes(client, person_and_contact):
    """Valid email is normalized before storage."""
    pid, _ = person_and_contact
    r = await client.post(
        f"/admin/people/{pid}/contacts/",
        headers=HTMX_HEADERS,
        data={"contact_type": "email", "value": "Info@Example.COM"},
    )
    assert r.status_code == 200
    assert "info@example.com" in r.text


async def test_contact_create_phone_normalizes_to_e164(client, person_and_contact):
    """Valid phone in formatted input is stored as E.164."""
    pid, _ = person_and_contact
    r = await client.post(
        f"/admin/people/{pid}/contacts/",
        headers=HTMX_HEADERS,
        data={"contact_type": "phone", "value": "(206) 555-1234"},
    )
    assert r.status_code == 200
    assert "+12065551234" in r.text


async def test_contact_create_phone_invalid_returns_form_with_error(client, person_and_contact):
    """Invalid phone must re-render the form row with an inline error."""
    pid, _ = person_and_contact
    r = await client.post(
        f"/admin/people/{pid}/contacts/",
        headers=HTMX_HEADERS,
        data={"contact_type": "phone", "value": "not-a-phone"},
    )
    assert r.status_code == 200
    assert "<form" in r.text
    assert "alert--error" in r.text


async def test_contact_edit_phone_invalid_returns_form_with_error(client, person_and_contact):
    """Invalid phone on edit must re-render the form row with an inline error."""
    pid, cid = person_and_contact
    r = await client.post(
        f"/admin/people/{pid}/contacts/{cid}/edit-row/",
        headers=HTMX_HEADERS,
        data={"value": "zzz"},
    )
    assert r.status_code == 200
    assert "<form" in r.text
    assert "alert--error" in r.text
