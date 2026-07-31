"""Integration tests for org names CRUD."""

import json
import re
from datetime import date

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
async def org_and_name(db):
    oid, nid = generate_id(), generate_id()
    await db.execute("INSERT INTO organizations (id) VALUES ($1)", oid)
    await db.execute(
        "INSERT INTO organization_names (id, organization_id, name, is_canonical)"
        " VALUES ($1, $2, 'Original Name', TRUE)",
        nid,
        oid,
    )
    return oid, nid


async def _insert_non_canonical(
    db, oid: str, nid: str, name: str = "Former Name", name_type: str = "legal"
) -> None:
    # name_type default matches the column default the pre-CR6 helper relied on.
    await db.execute(
        "INSERT INTO organization_names (id, organization_id, name, name_type, is_canonical)"
        " VALUES ($1, $2, $3, $4, FALSE)",
        nid,
        oid,
        name,
        name_type,
    )


async def _fetch_is_canonical(db, nid: str) -> bool | None:
    row = await db.fetchrow("SELECT is_canonical FROM organization_names WHERE id=$1", nid)
    return row["is_canonical"] if row else None


async def test_names_new_row_returns_form(client, org_and_name):
    oid, _ = org_and_name
    r = await client.get(f"/admin/orgs/{oid}/names/new-row/", headers=HTMX_HEADERS)
    assert r.status_code == 200
    assert "<form" in r.text


async def test_name_form_row_labels_effective_dates(client, org_and_name):
    """#259: visible 'Effective start' label + aria-hidden 'to'; row-scoped for new + edit."""
    oid, nid = org_and_name
    new = await client.get(f"/admin/orgs/{oid}/names/new-row/", headers=HTMX_HEADERS)
    assert new.status_code == 200
    assert '<label for="effective-start-new"' in new.text
    assert ">Effective start</label>" in new.text
    assert 'id="effective-start-new"' in new.text
    assert re.search(r'<span aria-hidden="true"[^>]*>\s*to</span>', new.text)
    assert 'aria-label="Effective end"' in new.text
    edit = await client.get(f"/admin/orgs/{oid}/names/{nid}/edit-row/", headers=HTMX_HEADERS)
    assert edit.status_code == 200
    assert f'<label for="effective-start-{nid}"' in edit.text
    assert f'id="effective-start-{nid}"' in edit.text


async def test_names_create(client, org_and_name):
    oid, _ = org_and_name
    r = await client.post(
        f"/admin/orgs/{oid}/names/",
        headers=HTMX_HEADERS,
        data={"name": "DBA Name", "name_type": "dba", "is_canonical": ""},
    )
    assert r.status_code == 200
    assert "DBA Name" in r.text


async def test_names_read_row_returns_row(client, org_and_name):
    oid, nid = org_and_name
    r = await client.get(f"/admin/orgs/{oid}/names/{nid}/read-row/", headers=HTMX_HEADERS)
    assert r.status_code == 200
    assert "Original Name" in r.text
    assert "<form" not in r.text


async def test_names_edit_row_returns_form(client, org_and_name):
    oid, nid = org_and_name
    r = await client.get(f"/admin/orgs/{oid}/names/{nid}/edit-row/", headers=HTMX_HEADERS)
    assert r.status_code == 200
    assert "Original Name" in r.text


async def test_names_update(client, org_and_name):
    oid, nid = org_and_name
    r = await client.post(
        f"/admin/orgs/{oid}/names/{nid}/edit-row/",
        headers=HTMX_HEADERS,
        data={"name": "Updated Name", "name_type": "legal", "is_canonical": "true"},
    )
    assert r.status_code == 200
    assert "Updated Name" in r.text


async def test_names_delete(client, org_and_name, db):
    oid, _ = org_and_name
    nid2 = generate_id()
    await _insert_non_canonical(db, oid, nid2)

    r = await client.delete(f"/admin/orgs/{oid}/names/{nid2}/", headers=HTMX_HEADERS)
    assert r.status_code == 200


async def test_names_delete_unknown_returns_404(client, org_and_name):
    oid, _ = org_and_name
    r = await client.delete(f"/admin/orgs/{oid}/names/{generate_id()}/", headers=HTMX_HEADERS)
    assert r.status_code == 404


async def test_names_create_demotes_existing_canonical(client, org_and_name, db):
    """Creating a canonical name must demote the current canonical."""
    oid, nid = org_and_name
    r = await client.post(
        f"/admin/orgs/{oid}/names/",
        headers=HTMX_HEADERS,
        data={"name": "New Canonical", "name_type": "legal", "is_canonical": "true"},
    )
    assert r.status_code == 200

    is_canonical = await _fetch_is_canonical(db, nid)
    assert is_canonical is False, "original canonical must be demoted"


async def test_names_update_promotes_and_demotes(client, org_and_name, db):
    """Editing a non-canonical name to canonical must demote the existing one."""
    oid, canonical_nid = org_and_name
    other_nid = generate_id()
    await _insert_non_canonical(db, oid, other_nid)

    r = await client.post(
        f"/admin/orgs/{oid}/names/{other_nid}/edit-row/",
        headers=HTMX_HEADERS,
        data={"name": "Former Name", "name_type": "former", "is_canonical": "true"},
    )
    assert r.status_code == 200

    orig_canonical = await _fetch_is_canonical(db, canonical_nid)
    new_canonical = await _fetch_is_canonical(db, other_nid)
    assert orig_canonical is False, "old canonical must be demoted"
    assert new_canonical is True, "edited row must be promoted"


async def test_names_edit_returns_tbody(client, org_and_name, db):
    """Edit response must return all rows (tbody innerHTML), not just the edited row."""
    oid, nid = org_and_name
    nid2 = generate_id()
    await _insert_non_canonical(db, oid, nid2, name="Second Name")

    r = await client.post(
        f"/admin/orgs/{oid}/names/{nid}/edit-row/",
        headers=HTMX_HEADERS,
        data={"name": "Original Name", "name_type": "legal", "is_canonical": "true"},
    )
    assert r.status_code == 200
    # Both rows must be present — only a full tbody response includes both
    assert f'id="name-row-{nid}"' in r.text
    assert f'id="name-row-{nid2}"' in r.text
    assert "<table" not in r.text  # not the full table


async def test_names_form_row_canonical_toggle_has_aria_label(client, org_and_name):
    oid, _ = org_and_name
    r = await client.get(f"/admin/orgs/{oid}/names/new-row/", headers=HTMX_HEADERS)
    assert r.status_code == 200
    assert 'aria-label="Canonical"' in r.text


async def test_names_create_returns_success_flash(client, org_and_name):
    oid, _ = org_and_name
    r = await client.post(
        f"/admin/orgs/{oid}/names/",
        headers=HTMX_HEADERS,
        data={"name": "Flash Test Name", "name_type": "dba", "is_canonical": ""},
    )
    assert r.status_code == 200
    trigger = json.loads(r.headers["hx-trigger"])
    assert trigger["showFlash"]["level"] == "success"
    assert "Flash Test Name" in trigger["showFlash"]["body"]


async def test_names_update_returns_success_flash(client, org_and_name):
    oid, nid = org_and_name
    r = await client.post(
        f"/admin/orgs/{oid}/names/{nid}/edit-row/",
        headers=HTMX_HEADERS,
        data={"name": "Updated Name", "name_type": "legal", "is_canonical": "true"},
    )
    assert r.status_code == 200
    trigger = json.loads(r.headers["hx-trigger"])
    assert trigger["showFlash"]["level"] == "success"
    assert "Updated Name" in trigger["showFlash"]["body"]


async def test_names_delete_returns_info_flash(client, org_and_name, db):
    oid, _ = org_and_name
    nid2 = generate_id()
    await _insert_non_canonical(db, oid, nid2, name="Delete Me")

    r = await client.delete(f"/admin/orgs/{oid}/names/{nid2}/", headers=HTMX_HEADERS)
    assert r.status_code == 200
    trigger = json.loads(r.headers["hx-trigger"])
    assert trigger["showFlash"]["level"] == "success"


async def test_name_edit_sole_uncanonical_is_blocked(client, org_and_name, db):
    """Unchecking canonical on the only name must be blocked with an error flash."""
    oid, nid = org_and_name

    r = await client.post(
        f"/admin/orgs/{oid}/names/{nid}/edit-row/",
        headers=HTMX_HEADERS,
        data={"name": "Original Name", "name_type": "legal", "is_canonical": ""},
    )
    assert r.status_code == 200
    trigger = json.loads(r.headers["hx-trigger"])
    assert trigger["showFlash"]["level"] == "warning"

    is_canonical = await _fetch_is_canonical(db, nid)
    assert is_canonical is True, "sole name must remain canonical after blocked edit"


async def test_name_delete_promotes_sole_remaining_non_canonical(client, org_and_name, db):
    """Deleting the canonical name when one non-canonical remains must auto-promote it."""
    oid, canonical_nid = org_and_name
    non_canonical_nid = generate_id()
    await _insert_non_canonical(db, oid, non_canonical_nid)

    # Delete the canonical name
    r = await client.delete(f"/admin/orgs/{oid}/names/{canonical_nid}/", headers=HTMX_HEADERS)
    assert r.status_code == 200

    is_canonical = await _fetch_is_canonical(db, non_canonical_nid)
    assert is_canonical is True, "sole remaining name must be auto-promoted to canonical"


async def test_name_delete_promotes_when_several_names_remain(client, org_and_name, db):
    """Deleting the canonical with several names left must not strand the org (CR6 #52).

    The old hook promoted only when exactly one name remained — the same
    shortcut CR5 #45 removed on the person side — so a three-name org whose
    canonical was deleted rendered blank (v_org_display_names joins only
    is_canonical=TRUE; the only fallback is a canonical acronym). `legal`
    outranks `dba` in the org ladder.
    """
    oid, canonical_nid = org_and_name
    legal_nid, dba_nid = generate_id(), generate_id()
    await _insert_non_canonical(db, oid, legal_nid, name="Acme Holdings", name_type="legal")
    await _insert_non_canonical(db, oid, dba_nid, name="Acme", name_type="dba")

    r = await client.delete(f"/admin/orgs/{oid}/names/{canonical_nid}/", headers=HTMX_HEADERS)
    assert r.status_code == 200

    assert await _fetch_is_canonical(db, legal_nid) is True, "top-ladder name must be promoted"
    assert await _fetch_is_canonical(db, dba_nid) is False
    assert (
        await db.fetchval(
            "SELECT display_name FROM v_org_display_names WHERE organization_id=$1", oid
        )
        == "Acme Holdings"
    )


async def test_name_delete_response_reflects_promoted_canonical(client, org_and_name, db):
    """Delete response must include the promoted name's canonical badge in returned rows."""
    oid, canonical_nid = org_and_name
    non_canonical_nid = generate_id()
    await _insert_non_canonical(db, oid, non_canonical_nid)

    r = await client.delete(f"/admin/orgs/{oid}/names/{canonical_nid}/", headers=HTMX_HEADERS)
    assert r.status_code == 200
    # Response must contain the remaining row (tbody replacement)
    assert f'id="name-row-{non_canonical_nid}"' in r.text
    # Must show the canonical badge (Yes) for the promoted name
    assert "badge--active" in r.text


async def test_name_delete_last_name_blocked_when_no_acronym(client, org_and_name, db):
    """Deleting the last name when there is no canonical acronym must be blocked."""
    oid, nid = org_and_name
    r = await client.delete(f"/admin/orgs/{oid}/names/{nid}/", headers=HTMX_HEADERS)
    assert r.status_code == 200
    trigger = json.loads(r.headers["hx-trigger"])
    assert trigger["showFlash"]["level"] == "warning"
    # Name must still exist in the DB
    row = await db.fetchrow("SELECT id FROM organization_names WHERE id=$1", nid)
    assert row is not None, "name must not be deleted"


async def test_name_delete_last_name_blocked_non_htmx_returns_409(client, org_and_name, db):
    """Non-HTMX delete of the last name (no canonical acronym) must return 409."""
    oid, nid = org_and_name
    r = await client.delete(f"/admin/orgs/{oid}/names/{nid}/", headers=AUTH_HEADERS)
    assert r.status_code == 409
    row = await db.fetchrow("SELECT id FROM organization_names WHERE id=$1", nid)
    assert row is not None, "name must not be deleted"


async def test_name_delete_last_name_allowed_when_canonical_acronym_exists(
    client, org_and_name, db
):
    """Deleting the last name is allowed when a canonical acronym exists."""
    oid, nid = org_and_name
    aid = generate_id()
    await db.execute(
        "INSERT INTO organization_acronyms (id, organization_id, acronym, is_canonical)"
        " VALUES ($1, $2, 'TEST', TRUE)",
        aid,
        oid,
    )

    r = await client.delete(f"/admin/orgs/{oid}/names/{nid}/", headers=HTMX_HEADERS)
    assert r.status_code == 200
    trigger = json.loads(r.headers["hx-trigger"])
    assert trigger["showFlash"]["level"] == "success"


# ---------------------------------------------------------------------------
# updateOrgHeader event tests
# ---------------------------------------------------------------------------


async def test_names_create_returns_update_org_header(client, org_and_name):
    """Creating a name must emit updateOrgHeader in HX-Trigger."""
    oid, _ = org_and_name
    r = await client.post(
        f"/admin/orgs/{oid}/names/",
        headers=HTMX_HEADERS,
        data={"name": "DBA Name", "name_type": "dba", "is_canonical": ""},
    )
    assert r.status_code == 200
    trigger = json.loads(r.headers["hx-trigger"])
    assert "updateOrgHeader" in trigger
    assert trigger["updateOrgHeader"]["display"] == "Original Name"


async def test_names_update_returns_update_org_header_with_new_display(client, org_and_name):
    """Updating the canonical name must emit updateOrgHeader with the new display value."""
    oid, nid = org_and_name
    r = await client.post(
        f"/admin/orgs/{oid}/names/{nid}/edit-row/",
        headers=HTMX_HEADERS,
        data={"name": "Renamed Corp", "name_type": "legal", "is_canonical": "true"},
    )
    assert r.status_code == 200
    trigger = json.loads(r.headers["hx-trigger"])
    assert "updateOrgHeader" in trigger
    assert trigger["updateOrgHeader"]["display"] == "Renamed Corp"


async def test_names_delete_returns_update_org_header(client, org_and_name, db):
    """Deleting a non-canonical name must emit updateOrgHeader with the remaining canonical."""
    oid, _ = org_and_name
    nid2 = generate_id()
    await _insert_non_canonical(db, oid, nid2)

    r = await client.delete(f"/admin/orgs/{oid}/names/{nid2}/", headers=HTMX_HEADERS)
    assert r.status_code == 200
    trigger = json.loads(r.headers["hx-trigger"])
    assert "updateOrgHeader" in trigger
    assert trigger["updateOrgHeader"]["display"] == "Original Name"


# ---------------------------------------------------------------------------
# Canonical edit guard tests
# ---------------------------------------------------------------------------


async def test_name_edit_uncanonical_with_multiple_names_blocked(client, org_and_name, db):
    """Unchecking canonical on the only canonical name (when others exist) must be blocked."""
    oid, canonical_nid = org_and_name
    other_nid = generate_id()
    await _insert_non_canonical(db, oid, other_nid)

    r = await client.post(
        f"/admin/orgs/{oid}/names/{canonical_nid}/edit-row/",
        headers=HTMX_HEADERS,
        data={"name": "Original Name", "name_type": "legal", "is_canonical": ""},
    )
    assert r.status_code == 200
    trigger = json.loads(r.headers["hx-trigger"])
    assert trigger["showFlash"]["level"] == "warning"

    is_canonical = await _fetch_is_canonical(db, canonical_nid)
    assert is_canonical is True, "canonical must not be changed"


async def test_name_edit_uncanonical_non_htmx_redirects(client, org_and_name, db):
    """Non-HTMX path: unchecking canonical on only canonical (multiple names) must redirect."""
    oid, canonical_nid = org_and_name
    other_nid = generate_id()
    await _insert_non_canonical(db, oid, other_nid)

    r = await client.post(
        f"/admin/orgs/{oid}/names/{canonical_nid}/edit-row/",
        headers=AUTH_HEADERS,
        data={"name": "Original Name", "name_type": "legal", "is_canonical": ""},
        follow_redirects=False,
    )
    assert r.status_code == 303

    is_canonical = await _fetch_is_canonical(db, canonical_nid)
    assert is_canonical is True, "canonical must not be changed"


async def test_name_edit_non_canonical_row_can_stay_non_canonical(client, org_and_name, db):
    """Editing a non-canonical name without checking canonical must succeed."""
    oid, _ = org_and_name
    other_nid = generate_id()
    await _insert_non_canonical(db, oid, other_nid)

    r = await client.post(
        f"/admin/orgs/{oid}/names/{other_nid}/edit-row/",
        headers=HTMX_HEADERS,
        data={"name": "Renamed Former", "name_type": "former", "is_canonical": ""},
    )
    assert r.status_code == 200
    trigger = json.loads(r.headers["hx-trigger"])
    assert trigger["showFlash"]["level"] == "success"


# ---------------------------------------------------------------------------
# effective_start / effective_end (#239)
# ---------------------------------------------------------------------------


async def _fetch_effective(db, oid: str, name: str):
    row = await db.fetchrow(
        "SELECT effective_start, effective_end FROM organization_names"
        " WHERE organization_id=$1 AND name=$2",
        oid,
        name,
    )
    return (row["effective_start"], row["effective_end"]) if row else None


async def test_name_create_stores_effective_dates(client, org_and_name, db):
    """Creating a name with effective dates persists them on the new row."""
    oid, _ = org_and_name
    r = await client.post(
        f"/admin/orgs/{oid}/names/",
        headers=HTMX_HEADERS,
        data={
            "name": "Committee on Old Government",
            "name_type": "former",
            "is_canonical": "",
            "effective_start": "2019-01-01",
            "effective_end": "2023-01-09",
        },
    )
    assert r.status_code == 200
    assert await _fetch_effective(db, oid, "Committee on Old Government") == (
        date(2019, 1, 1),
        date(2023, 1, 9),
    )


async def test_name_edit_sets_and_clears_effective_dates(client, org_and_name, db):
    """Edit treats the form as source of truth: sets dates, then clears to NULL."""
    oid, nid = org_and_name
    # Set an open-ended interval (start only).
    r = await client.post(
        f"/admin/orgs/{oid}/names/{nid}/edit-row/",
        headers=HTMX_HEADERS,
        data={
            "name": "Original Name",
            "name_type": "legal",
            "is_canonical": "true",
            "effective_start": "2023-01-09",
            "effective_end": "",
        },
    )
    assert r.status_code == 200
    assert await _fetch_effective(db, oid, "Original Name") == (date(2023, 1, 9), None)

    # Empty inputs clear both back to NULL.
    r = await client.post(
        f"/admin/orgs/{oid}/names/{nid}/edit-row/",
        headers=HTMX_HEADERS,
        data={
            "name": "Original Name",
            "name_type": "legal",
            "is_canonical": "true",
            "effective_start": "",
            "effective_end": "",
        },
    )
    assert r.status_code == 200
    assert await _fetch_effective(db, oid, "Original Name") == (None, None)


async def test_name_create_start_after_end_flashes_error(client, org_and_name, db):
    """effective_start > effective_end is rejected with a flash, no row created."""
    oid, _ = org_and_name
    r = await client.post(
        f"/admin/orgs/{oid}/names/",
        headers=HTMX_HEADERS,
        data={
            "name": "Backwards Interval",
            "name_type": "former",
            "is_canonical": "",
            "effective_start": "2023-01-09",
            "effective_end": "2019-01-01",
        },
    )
    assert r.status_code == 200
    trigger = json.loads(r.headers["hx-trigger"])
    assert trigger["showFlash"]["level"] == "warning"
    assert await _fetch_effective(db, oid, "Backwards Interval") is None


async def test_name_edit_row_renders_effective_date_inputs(client, org_and_name, db):
    """The org name edit form exposes effective_start/effective_end inputs, pre-filled."""
    oid, nid = org_and_name
    await db.execute(
        "UPDATE organization_names SET effective_start=$1, effective_end=$2 WHERE id=$3",
        date(2019, 1, 1),
        date(2023, 1, 9),
        nid,
    )
    r = await client.get(f"/admin/orgs/{oid}/names/{nid}/edit-row/", headers=HTMX_HEADERS)
    assert r.status_code == 200
    assert 'name="effective_start"' in r.text
    assert 'name="effective_end"' in r.text
    assert "2019-01-01" in r.text
    assert "2023-01-09" in r.text


async def test_name_read_row_shows_effective_range(client, org_and_name):
    """After a mutation the rendered read row surfaces the effective date range."""
    oid, nid = org_and_name
    r = await client.post(
        f"/admin/orgs/{oid}/names/{nid}/edit-row/",
        headers=HTMX_HEADERS,
        data={
            "name": "Original Name",
            "name_type": "legal",
            "is_canonical": "true",
            "effective_start": "2023-01-09",
            "effective_end": "",
        },
    )
    assert r.status_code == 200
    assert "2023-01-09" in r.text


async def test_name_edit_start_after_end_flashes_error(client, org_and_name, db):
    """Edit path: effective_start > effective_end → flash error, row unchanged (#239 CR)."""
    oid, nid = org_and_name
    r = await client.post(
        f"/admin/orgs/{oid}/names/{nid}/edit-row/",
        headers=HTMX_HEADERS,
        data={
            "name": "Original Name",
            "name_type": "legal",
            "is_canonical": "true",
            "effective_start": "2023-01-09",
            "effective_end": "2019-01-01",
        },
    )
    assert r.status_code == 200
    trigger = json.loads(r.headers["hx-trigger"])
    assert trigger["showFlash"]["level"] == "warning"
    assert await _fetch_effective(db, oid, "Original Name") == (None, None)


async def test_name_create_invalid_date_flashes_error(client, org_and_name, db):
    """Create path: a malformed effective date → flash error, no row created (#239 CR)."""
    oid, _ = org_and_name
    r = await client.post(
        f"/admin/orgs/{oid}/names/",
        headers=HTMX_HEADERS,
        data={
            "name": "Bad Date Name",
            "name_type": "former",
            "is_canonical": "",
            "effective_start": "not-a-date",
            "effective_end": "",
        },
    )
    assert r.status_code == 200
    trigger = json.loads(r.headers["hx-trigger"])
    assert trigger["showFlash"]["level"] == "warning"
    assert await _fetch_effective(db, oid, "Bad Date Name") is None


async def test_name_edit_invalid_date_flashes_error(client, org_and_name, db):
    """Edit path: a malformed effective date → flash error, row unchanged (#239 CR)."""
    oid, nid = org_and_name
    r = await client.post(
        f"/admin/orgs/{oid}/names/{nid}/edit-row/",
        headers=HTMX_HEADERS,
        data={
            "name": "Original Name",
            "name_type": "legal",
            "is_canonical": "true",
            "effective_start": "not-a-date",
            "effective_end": "",
        },
    )
    assert r.status_code == 200
    trigger = json.loads(r.headers["hx-trigger"])
    assert trigger["showFlash"]["level"] == "warning"
    assert await _fetch_effective(db, oid, "Original Name") == (None, None)
