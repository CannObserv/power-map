"""Integration tests for person addresses — temporal validity window (#181)."""

import re
from unittest.mock import AsyncMock, MagicMock, patch

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
async def person_and_address(db):
    pid = generate_id()
    aid = generate_id()
    eaid = generate_id()

    await db.execute("INSERT INTO people (id) VALUES ($1)", pid)
    await db.execute(
        "INSERT INTO addresses (id, address_line_1, city, region, postal_code, country)"
        " VALUES ($1, '123 Main St', 'Olympia', 'WA', '98501', 'US')",
        aid,
    )
    await db.execute(
        "INSERT INTO entity_addresses"
        " (id, entity_type, entity_id, address_id, address_type)"
        " VALUES ($1, 'person', $2, $3, 'mailing')",
        eaid,
        pid,
        aid,
    )

    yield pid, eaid


async def test_address_form_row_type_and_label_lead_panel(client, person_and_address):
    """#181 follow-up: type + label on the first line, ahead of country and validity."""
    pid, _ = person_and_address
    r = await client.get(f"/admin/people/{pid}/addresses/new-row/", headers=HTMX_HEADERS)
    assert r.status_code == 200
    type_pos = r.text.index('name="address_type"')
    label_pos = r.text.index('name="display_name"')
    assert type_pos < r.text.index('name="country"')
    assert label_pos < r.text.index('name="country"')
    assert type_pos < r.text.index('name="valid_from"')
    assert label_pos < r.text.index('name="valid_from"')


async def test_address_form_row_scopes_country_swap_target(client, person_and_address):
    """CR round 1: country swap target is row-scoped; no page-global hx-include."""
    pid, eaid = person_and_address
    new = await client.get(f"/admin/people/{pid}/addresses/new-row/", headers=HTMX_HEADERS)
    assert new.status_code == 200
    assert 'id="address-structured-fields-new"' in new.text
    assert 'hx-target="#address-structured-fields-new"' in new.text
    assert 'hx-include="[name=' not in new.text
    assert 'id="address-country-input"' not in new.text
    edit = await client.get(f"/admin/people/{pid}/addresses/{eaid}/edit-row/", headers=HTMX_HEADERS)
    assert edit.status_code == 200
    assert f'id="address-structured-fields-{eaid}"' in edit.text
    assert f'hx-target="#address-structured-fields-{eaid}"' in edit.text


async def test_address_form_row_country_swap_includes_form_values(client, person_and_address):
    """#258/#282: country swap carries the form's current values; the edit row targets
    the addr_id via its hx-post path (no redundant hidden addr_id field)."""
    pid, eaid = person_and_address
    new = await client.get(f"/admin/people/{pid}/addresses/new-row/", headers=HTMX_HEADERS)
    assert new.status_code == 200
    assert 'hx-include="closest form"' in new.text
    assert 'name="addr_id"' not in new.text
    edit = await client.get(f"/admin/people/{pid}/addresses/{eaid}/edit-row/", headers=HTMX_HEADERS)
    assert edit.status_code == 200
    assert 'hx-include="closest form"' in edit.text
    # #282: addr_id lives in the POST path, not a redundant hidden field
    assert 'name="addr_id"' not in edit.text
    assert f"/admin/people/{pid}/addresses/{eaid}/edit-row/" in edit.text


async def test_country_format_preserves_current_values(client, person_and_address):
    """#258: the fields partial echoes in-progress values instead of blanking them."""
    pid, eaid = person_and_address
    with patch(
        "src.api.admin._addresses_shared.get_country_format",
        new=AsyncMock(
            return_value={
                "country": "CA",
                "fields": [
                    {"key": "address_line_1", "label": "Address line 1", "required": True},
                    {"key": "address_line_2", "label": "Apt/suite", "required": False},
                    {"key": "city", "label": "City", "required": True},
                    {"key": "region", "label": "Province", "required": True},
                    {"key": "postal_code", "label": "Postal code", "required": False},
                ],
            }
        ),
    ):
        r = await client.get(
            f"/admin/people/{pid}/addresses/country-format/",
            params={
                "country": "CA",
                "address_line_1": "123 Main St",
                "address_line_2": "Suite 4",
                "city": "Olympia",
                "region": "WA",
                "postal_code": "98501",
                "addr_id": eaid,
            },
            headers=HTMX_HEADERS,
        )
    assert r.status_code == 200
    assert 'value="123 Main St"' in r.text
    assert 'value="Suite 4"' in r.text
    assert 'value="Olympia"' in r.text
    assert 'value="WA"' in r.text
    assert 'value="98501"' in r.text
    assert f"address-line-2-opt-{eaid}" in r.text


async def test_country_format_drops_field_absent_from_new_format(client, person_and_address):
    """#258 CR: a value present in the query but not in the new country's format is dropped."""
    pid, _ = person_and_address
    with patch(
        "src.api.admin._addresses_shared.get_country_format",
        new=AsyncMock(
            return_value={
                "country": "JP",
                "fields": [
                    {"key": "address_line_1", "label": "Address line 1", "required": True},
                    {"key": "city", "label": "City", "required": True},
                    {"key": "postal_code", "label": "Postal code", "required": False},
                ],
            }
        ),
    ):
        r = await client.get(
            f"/admin/people/{pid}/addresses/country-format/",
            params={"country": "JP", "region": "WA", "city": "Kyoto"},
            headers=HTMX_HEADERS,
        )
    assert r.status_code == 200
    assert 'value="Kyoto"' in r.text
    assert 'name="region"' not in r.text
    assert 'value="WA"' not in r.text


async def test_address_form_row_validity_labels(client, person_and_address):
    """#181 follow-up: visible 'Valid from' label + aria-hidden 'to' separator."""
    pid, eaid = person_and_address
    new = await client.get(f"/admin/people/{pid}/addresses/new-row/", headers=HTMX_HEADERS)
    assert new.status_code == 200
    assert '<label for="valid-from-new"' in new.text
    assert ">Valid from</label>" in new.text
    assert 'id="valid-from-new"' in new.text
    assert re.search(r'<span aria-hidden="true"[^>]*>\s*to</span>', new.text)
    assert 'aria-label="Valid until"' in new.text
    edit = await client.get(f"/admin/people/{pid}/addresses/{eaid}/edit-row/", headers=HTMX_HEADERS)
    assert edit.status_code == 200
    assert f'<label for="valid-from-{eaid}"' in edit.text
    assert f'id="valid-from-{eaid}"' in edit.text


async def test_addresses_create_with_validity_window(client, person_and_address, db):
    pid, existing_eaid = person_and_address
    r = await client.post(
        f"/admin/people/{pid}/addresses/",
        headers=HTMX_HEADERS,
        data={
            "address_line_1": "789 Window Way",
            "city": "Olympia",
            "region": "WA",
            "postal_code": "98501",
            "address_type": "physical",
            "valid_from": "2024-01-01",
            "valid_until": "2025-06-30",
            "mode": "save",
        },
    )
    assert r.status_code == 200
    assert "2024-01-01" in r.text
    assert "2025-06-30" in r.text

    row = await db.fetchrow(
        "SELECT ea.valid_from, ea.valid_until FROM entity_addresses ea"
        " WHERE ea.entity_id=$1 AND ea.id != $2",
        pid,
        existing_eaid,
    )
    assert row is not None
    assert str(row["valid_from"]) == "2024-01-01"
    assert str(row["valid_until"]) == "2025-06-30"


async def test_addresses_update_validity_window(client, person_and_address, db):
    pid, eaid = person_and_address
    r = await client.post(
        f"/admin/people/{pid}/addresses/{eaid}/edit-row/",
        headers=HTMX_HEADERS,
        data={
            "address_line_1": "123 Main St",
            "city": "Olympia",
            "region": "WA",
            "postal_code": "98501",
            "address_type": "mailing",
            "valid_from": "2020-05-01",
            "mode": "save",
        },
    )
    assert r.status_code == 200
    assert "2020-05-01" in r.text

    row = await db.fetchrow(
        "SELECT valid_from, valid_until FROM entity_addresses WHERE id=$1", eaid
    )
    assert str(row["valid_from"]) == "2020-05-01"
    assert row["valid_until"] is None


async def test_addresses_create_inverted_range_returns_error(client, person_and_address, db):
    pid, existing_eaid = person_and_address
    r = await client.post(
        f"/admin/people/{pid}/addresses/",
        headers=HTMX_HEADERS,
        data={
            "address_line_1": "1 Backwards Blvd",
            "city": "Olympia",
            "region": "WA",
            "postal_code": "98501",
            "address_type": "mailing",
            "valid_from": "2025-06-30",
            "valid_until": "2024-01-01",
            "mode": "save",
        },
    )
    assert r.status_code == 200
    assert "alert--error" in r.text
    assert "on or before" in r.text

    count = await db.fetchval(
        "SELECT count(*) FROM entity_addresses WHERE entity_id=$1 AND id != $2",
        pid,
        existing_eaid,
    )
    assert count == 0


async def test_addresses_create_malformed_date_returns_format_error(client, person_and_address, db):
    """Malformed date input gets a format message, not the range-order message (#181 CR)."""
    pid, existing_eaid = person_and_address
    r = await client.post(
        f"/admin/people/{pid}/addresses/",
        headers=HTMX_HEADERS,
        data={
            "address_line_1": "1 Malformed Way",
            "city": "Olympia",
            "region": "WA",
            "postal_code": "98501",
            "address_type": "mailing",
            "valid_from": "01/02/2024",
            "mode": "save",
        },
    )
    assert r.status_code == 200
    assert "alert--error" in r.text
    assert "YYYY-MM-DD" in r.text
    assert "on or before" not in r.text

    count = await db.fetchval(
        "SELECT count(*) FROM entity_addresses WHERE entity_id=$1 AND id != $2",
        pid,
        existing_eaid,
    )
    assert count == 0


# ---------------------------------------------------------------------------
# Non-HTMX confirm-mode persistence (#280 — silent data loss)
# ---------------------------------------------------------------------------


@patch("src.api.admin.people_addresses._NORMALIZER")
async def test_address_create_confirm_non_htmx_persists_and_redirects(
    mock_normalizer, client, person_and_address, db
):
    """#280: a non-HTMX confirm submit persists the normalized address, no data loss."""
    pid, existing_eaid = person_and_address
    mock_normalizer.normalize = AsyncMock(
        return_value=MagicMock(
            skipped=False,
            value={
                "address_line_1": "123 MAIN ST",
                "address_line_2": None,
                "city": "SEATTLE",
                "region": "WA",
                "postal_code": "98101",
                "country": "US",
                "standardized": "123 MAIN ST SEATTLE WA 98101",
                "latitude": None,
                "longitude": None,
                "components": None,
            },
            validation_detail=None,
        )
    )
    r = await client.post(
        f"/admin/people/{pid}/addresses/",
        headers=AUTH_HEADERS,  # no HX-Request
        data={
            "address_line_1": "123 Main St",
            "city": "Seattle",
            "region": "WA",
            "postal_code": "98101",
            "address_type": "mailing",
        },
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert r.headers["location"] == f"/admin/people/{pid}/"

    row = await db.fetchrow(
        "SELECT a.address_line_1, a.city, a.region, a.postal_code, a.standardized"
        " FROM entity_addresses ea JOIN addresses a ON a.id = ea.address_id"
        " WHERE ea.entity_id=$1 AND ea.id != $2",
        pid,
        existing_eaid,
    )
    assert row is not None
    assert row["address_line_1"] == "123 MAIN ST"
    assert row["standardized"] == "123 MAIN ST SEATTLE WA 98101"


@patch("src.api.admin.people_addresses._NORMALIZER")
async def test_address_edit_confirm_non_htmx_persists_and_redirects(
    mock_normalizer, client, person_and_address, db
):
    """#280: a non-HTMX confirm edit submit persists the normalized address."""
    pid, eaid = person_and_address
    mock_normalizer.normalize = AsyncMock(
        return_value=MagicMock(
            skipped=False,
            value={
                "address_line_1": "123 MAIN ST",
                "address_line_2": None,
                "city": "OLYMPIA",
                "region": "WA",
                "postal_code": "98501",
                "country": "US",
                "standardized": "123 MAIN ST OLYMPIA WA 98501",
                "latitude": None,
                "longitude": None,
                "components": None,
            },
            validation_detail=None,
        )
    )
    r = await client.post(
        f"/admin/people/{pid}/addresses/{eaid}/edit-row/",
        headers=AUTH_HEADERS,  # no HX-Request
        data={
            "address_line_1": "123 Main St",
            "city": "Olympia",
            "region": "WA",
            "postal_code": "98501",
            "address_type": "mailing",
        },
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert r.headers["location"] == f"/admin/people/{pid}/"

    row = await db.fetchrow(
        "SELECT a.address_line_1, a.city, a.standardized"
        " FROM entity_addresses ea JOIN addresses a ON a.id = ea.address_id"
        " WHERE ea.id=$1",
        eaid,
    )
    assert row is not None
    assert row["address_line_1"] == "123 MAIN ST"
    assert row["standardized"] == "123 MAIN ST OLYMPIA WA 98501"
