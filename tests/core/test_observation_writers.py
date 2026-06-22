"""Integration tests for src.core.observation per-surface writers."""

import hashlib
import os
from datetime import date

import pytest
import pytest_asyncio

from src.api.public.schemas import (
    ObservationAcronym,
    ObservationAdditionalIdentifier,
    ObservationAddress,
    ObservationContactMethod,
    ObservationLink,
    ObservationOrgName,
    ObservationPersonName,
    ObservationPersonNameParts,
    ObservationRoleAssignment,
)
from src.core.db import generate_id
from src.core.normalizers import address as addr_mod
from src.core.observation import (
    IdentifierConflict,
    ObservationRejected,
    write_additional_identifiers,
    write_addresses,
    write_contact_methods,
    write_links,
    write_names,
    write_org_acronyms,
    write_org_parent,
    write_pronouns,
    write_role_assignments,
)

pytestmark = [
    pytest.mark.integration,
]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


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
async def api_key_id(db):
    """Insert an app_user + api_key; return the api_key_id."""
    uid = generate_id()
    kid = generate_id()
    raw_key = "pm_" + os.urandom(16).hex()
    key_hash = hashlib.sha256(raw_key.encode()).hexdigest()
    await db.execute(
        "INSERT INTO app_users (id, email) VALUES ($1, $2)", uid, "writer_test@test.com"
    )
    await db.execute(
        "INSERT INTO api_keys (id, user_id, label, key_prefix, key_hash) VALUES ($1,$2,$3,$4,$5)",
        kid,
        uid,
        "Writer Test",
        raw_key[:8],
        key_hash,
    )
    return kid


@pytest_asyncio.fixture(loop_scope="session")
async def person_id(db):
    pid = generate_id()
    await db.execute("INSERT INTO people (id) VALUES ($1)", pid)
    return pid


@pytest_asyncio.fixture(loop_scope="session")
async def org_id(db):
    oid = generate_id()
    await db.execute("INSERT INTO organizations (id) VALUES ($1)", oid)
    return oid


# ---------------------------------------------------------------------------
# write_names — person
# ---------------------------------------------------------------------------


async def test_write_names_appends_new_person_name(db, person_id, api_key_id):
    name = ObservationPersonName(name="Jane Doe", name_type="legal")
    await write_names(db, person_id, "person", api_key_id, [name])
    rows = await db.fetch(
        "SELECT name, source_key_id FROM person_names WHERE person_id=$1", person_id
    )
    assert len(rows) == 1
    assert rows[0]["name"] == "Jane Doe"
    assert rows[0]["source_key_id"] == api_key_id


async def test_write_names_exact_match_is_noop(db, person_id, api_key_id):
    name = ObservationPersonName(name="Jane Doe", name_type="legal")
    await write_names(db, person_id, "person", api_key_id, [name])
    await write_names(db, person_id, "person", api_key_id, [name])
    rows = await db.fetch("SELECT id FROM person_names WHERE person_id=$1", person_id)
    assert len(rows) == 1


async def test_write_names_parts_written_on_new_row(db, person_id, api_key_id):
    parts = ObservationPersonNameParts(given_names=["Jane"], family_names=["Doe"])
    name = ObservationPersonName(name="Jane Doe", name_type="legal", parts=parts)
    await write_names(db, person_id, "person", api_key_id, [name])
    row = await db.fetchrow(
        "SELECT pnp.given_names, pnp.family_names FROM person_names pn"
        " JOIN person_name_parts pnp ON pnp.person_name_id = pn.id"
        " WHERE pn.person_id=$1",
        person_id,
    )
    assert row is not None
    assert list(row["given_names"]) == ["Jane"]
    assert list(row["family_names"]) == ["Doe"]


async def test_write_names_person_canonical_hint_promotes(db, api_key_id):
    pid = generate_id()
    await db.execute("INSERT INTO people (id) VALUES ($1)", pid)
    name = ObservationPersonName(name="Alice Smith", name_type="legal", is_canonical=True)
    await write_names(db, pid, "person", api_key_id, [name])
    row = await db.fetchrow(
        "SELECT is_canonical FROM person_names WHERE person_id=$1 AND name=$2",
        pid,
        "Alice Smith",
    )
    assert row["is_canonical"] is True


async def test_write_names_person_canonical_hint_no_displace(db, api_key_id):
    pid = generate_id()
    await db.execute("INSERT INTO people (id) VALUES ($1)", pid)
    first = ObservationPersonName(name="Alice Smith", name_type="legal", is_canonical=True)
    await write_names(db, pid, "person", api_key_id, [first])
    second = ObservationPersonName(name="Alice", name_type="preferred", is_canonical=True)
    await write_names(db, pid, "person", api_key_id, [second])
    rows = await db.fetch(
        "SELECT name, is_canonical FROM person_names WHERE person_id=$1 ORDER BY created_at",
        pid,
    )
    # Both should be canonical: different name_types have separate canonical slots
    assert rows[0]["is_canonical"] is True
    assert rows[1]["is_canonical"] is True


async def test_write_names_person_canonical_hint_same_type_no_displace(db, api_key_id):
    pid = generate_id()
    await db.execute("INSERT INTO people (id) VALUES ($1)", pid)
    first = ObservationPersonName(name="Alice Smith", name_type="legal", is_canonical=True)
    await write_names(db, pid, "person", api_key_id, [first])
    second = ObservationPersonName(name="Alice J. Smith", name_type="legal", is_canonical=True)
    await write_names(db, pid, "person", api_key_id, [second])
    rows = await db.fetch(
        "SELECT name, is_canonical FROM person_names WHERE person_id=$1 ORDER BY created_at",
        pid,
    )
    assert rows[0]["name"] == "Alice Smith"
    assert rows[0]["is_canonical"] is True
    assert rows[1]["name"] == "Alice J. Smith"
    assert rows[1]["is_canonical"] is False  # first legal name stays canonical


# ---------------------------------------------------------------------------
# write_names — organization
# ---------------------------------------------------------------------------


async def test_write_names_organization(db, org_id, api_key_id):
    name = ObservationOrgName(name="Acme Corp", name_type="legal")
    await write_names(db, org_id, "organization", api_key_id, [name])
    rows = await db.fetch(
        "SELECT name, source_key_id, is_canonical FROM organization_names WHERE organization_id=$1",
        org_id,
    )
    assert len(rows) == 1
    assert rows[0]["name"] == "Acme Corp"
    assert rows[0]["source_key_id"] == api_key_id
    assert rows[0]["is_canonical"] is True


async def test_write_names_org_multi_name_list_promotes_first(db, api_key_id):
    oid = generate_id()
    await db.execute("INSERT INTO organizations (id) VALUES ($1)", oid)
    names = [
        ObservationOrgName(name="WA Joint Committee on Education", name_type="legal"),
        ObservationOrgName(name="Joint Ed Committee", name_type="dba"),
    ]
    await write_names(db, oid, "organization", api_key_id, names)
    rows = await db.fetch(
        "SELECT is_canonical FROM organization_names WHERE organization_id=$1 ORDER BY created_at",
        oid,
    )
    assert len(rows) == 2
    assert rows[0]["is_canonical"] is True
    assert rows[1]["is_canonical"] is False


async def test_write_names_org_second_name_not_canonical(db, api_key_id):
    oid = generate_id()
    await db.execute("INSERT INTO organizations (id) VALUES ($1)", oid)
    first = ObservationOrgName(name="Senate Finance Committee", name_type="legal")
    await write_names(db, oid, "organization", api_key_id, [first])
    second = ObservationOrgName(name="Finance Committee", name_type="dba")
    await write_names(db, oid, "organization", api_key_id, [second])
    rows = await db.fetch(
        "SELECT is_canonical FROM organization_names WHERE organization_id=$1 ORDER BY created_at",
        oid,
    )
    assert len(rows) == 2
    assert rows[0]["is_canonical"] is True
    assert rows[1]["is_canonical"] is False


async def test_write_names_org_canonical_hint_promotes_specific(db, api_key_id):
    """is_canonical=True on a non-first name → that name becomes canonical, not the first."""
    oid = generate_id()
    await db.execute("INSERT INTO organizations (id) VALUES ($1)", oid)
    names = [
        ObservationOrgName(name="WA Leg", name_type="dba", is_canonical=False),
        ObservationOrgName(
            name="Washington State Legislature", name_type="legal", is_canonical=True
        ),
    ]
    await write_names(db, oid, "organization", api_key_id, names)
    rows = await db.fetch(
        "SELECT name, is_canonical FROM organization_names WHERE organization_id=$1",
        oid,
    )
    by_name = {r["name"]: r["is_canonical"] for r in rows}
    assert by_name["WA Leg"] is False
    assert by_name["Washington State Legislature"] is True


async def test_write_names_org_canonical_hint_no_displace(db, api_key_id):
    """is_canonical=True does not displace an already-canonical name."""
    oid = generate_id()
    await db.execute("INSERT INTO organizations (id) VALUES ($1)", oid)
    first = ObservationOrgName(name="First Name", name_type="legal")
    await write_names(db, oid, "organization", api_key_id, [first])
    second = ObservationOrgName(name="Second Name", name_type="dba", is_canonical=True)
    await write_names(db, oid, "organization", api_key_id, [second])
    rows = await db.fetch(
        "SELECT name, is_canonical FROM organization_names"
        " WHERE organization_id=$1 ORDER BY created_at",
        oid,
    )
    assert rows[0]["name"] == "First Name"
    assert rows[0]["is_canonical"] is True
    assert rows[1]["name"] == "Second Name"
    assert rows[1]["is_canonical"] is False


# ---------------------------------------------------------------------------
# write_links
# ---------------------------------------------------------------------------


async def test_write_links_appends_new(db, person_id):
    link = ObservationLink(url="https://example.com/jane", link_type_slug="website")
    await write_links(db, person_id, "person", [link])
    rows = await db.fetch(
        "SELECT url, link_type_id FROM links WHERE entity_type='person' AND entity_id=$1",
        person_id,
    )
    assert len(rows) == 1
    assert rows[0]["url"] == "https://example.com/jane"
    lt = await db.fetchrow("SELECT id FROM link_types WHERE slug='website'")
    assert rows[0]["link_type_id"] == lt["id"]


async def test_write_links_duplicate_is_noop(db, person_id):
    link = ObservationLink(url="https://example.com/jane", link_type_slug="website")
    await write_links(db, person_id, "person", [link])
    await write_links(db, person_id, "person", [link])
    rows = await db.fetch(
        "SELECT id FROM links WHERE entity_type='person' AND entity_id=$1", person_id
    )
    assert len(rows) == 1


async def test_write_links_by_id(db, person_id):
    lt = await db.fetchrow("SELECT id FROM link_types WHERE slug='twitter'")
    link = ObservationLink(url="https://twitter.com/jane", link_type_id=lt["id"])
    await write_links(db, person_id, "person", [link])
    rows = await db.fetch(
        "SELECT link_type_id FROM links WHERE entity_type='person' AND entity_id=$1",
        person_id,
    )
    assert len(rows) == 1
    assert rows[0]["link_type_id"] == lt["id"]


async def test_write_links_insert_writes_entity_changes(db):
    """Initial INSERT of a link writes an entity_changes row."""
    pid = generate_id()
    await db.execute("INSERT INTO people (id) VALUES ($1)", pid)
    before = await db.fetchval(
        "SELECT COUNT(*) FROM entity_changes WHERE entity_type='person' AND entity_id=$1", pid
    )
    link = ObservationLink(url="https://example.com/new", link_type_slug="website")
    await write_links(db, pid, "person", [link])
    after = await db.fetchval(
        "SELECT COUNT(*) FROM entity_changes WHERE entity_type='person' AND entity_id=$1", pid
    )
    assert after - before == 1


# ---------------------------------------------------------------------------
# write_contact_methods
# ---------------------------------------------------------------------------


async def test_write_contact_methods_phone_normalized(db, person_id):
    cm = ObservationContactMethod(contact_type="phone", value="(206) 555-1234")
    await write_contact_methods(db, person_id, "person", [cm])
    rows = await db.fetch(
        "SELECT value FROM contact_methods WHERE entity_type='person' AND entity_id=$1",
        person_id,
    )
    assert len(rows) == 1
    assert rows[0]["value"] == "+12065551234"


async def test_write_contact_methods_invalid_phone_raises(db, person_id):
    cm = ObservationContactMethod(contact_type="phone", value="not a phone")
    with pytest.raises(ObservationRejected):
        await write_contact_methods(db, person_id, "person", [cm])


async def test_write_contact_methods_duplicate_noop(db, person_id):
    cm1 = ObservationContactMethod(contact_type="phone", value="(206) 555-1234")
    cm2 = ObservationContactMethod(contact_type="phone", value="+1 206 555 1234")
    await write_contact_methods(db, person_id, "person", [cm1, cm2])
    rows = await db.fetch(
        "SELECT id FROM contact_methods WHERE entity_type='person' AND entity_id=$1",
        person_id,
    )
    assert len(rows) == 1


async def test_write_contact_methods_email(db, person_id):
    cm = ObservationContactMethod(contact_type="email", value="Jane@Example.com")
    await write_contact_methods(db, person_id, "person", [cm])
    rows = await db.fetch(
        "SELECT value FROM contact_methods WHERE entity_type='person' AND entity_id=$1",
        person_id,
    )
    assert len(rows) == 1
    # email-validator normalizes domain to lowercase
    assert rows[0]["value"].endswith("@example.com")


async def test_write_contact_methods_insert_writes_entity_changes(db):
    """Initial INSERT of a contact method writes an entity_changes row."""
    pid = generate_id()
    await db.execute("INSERT INTO people (id) VALUES ($1)", pid)
    before = await db.fetchval(
        "SELECT COUNT(*) FROM entity_changes WHERE entity_type='person' AND entity_id=$1", pid
    )
    cm = ObservationContactMethod(contact_type="phone", value="(206) 555-0100")
    await write_contact_methods(db, pid, "person", [cm])
    after = await db.fetchval(
        "SELECT COUNT(*) FROM entity_changes WHERE entity_type='person' AND entity_id=$1", pid
    )
    assert after - before == 1


async def test_write_contact_methods_null_fill_updates_label(db):
    """display_label NULL-filled from re-observation; entity_changes row written."""
    pid = generate_id()
    await db.execute("INSERT INTO people (id) VALUES ($1)", pid)
    cm1 = ObservationContactMethod(contact_type="phone", value="(206) 555-0101")
    await write_contact_methods(db, pid, "person", [cm1])
    before = await db.fetchval(
        "SELECT COUNT(*) FROM entity_changes WHERE entity_type='person' AND entity_id=$1", pid
    )
    cm2 = ObservationContactMethod(
        contact_type="phone", value="(206) 555-0101", display_label="Main"
    )
    await write_contact_methods(db, pid, "person", [cm2])
    row = await db.fetchrow(
        "SELECT display_label FROM contact_methods WHERE entity_type='person' AND entity_id=$1",
        pid,
    )
    after = await db.fetchval(
        "SELECT COUNT(*) FROM entity_changes WHERE entity_type='person' AND entity_id=$1", pid
    )
    assert row["display_label"] == "Main"
    assert after - before == 1


async def test_write_contact_methods_existing_label_not_overwritten(db):
    """Non-NULL display_label is not overwritten; no entity_changes row written."""
    pid = generate_id()
    await db.execute("INSERT INTO people (id) VALUES ($1)", pid)
    cm1 = ObservationContactMethod(
        contact_type="phone", value="(206) 555-0102", display_label="Office"
    )
    await write_contact_methods(db, pid, "person", [cm1])
    before = await db.fetchval(
        "SELECT COUNT(*) FROM entity_changes WHERE entity_type='person' AND entity_id=$1", pid
    )
    cm2 = ObservationContactMethod(
        contact_type="phone", value="(206) 555-0102", display_label="Mobile"
    )
    await write_contact_methods(db, pid, "person", [cm2])
    row = await db.fetchrow(
        "SELECT display_label FROM contact_methods WHERE entity_type='person' AND entity_id=$1",
        pid,
    )
    after = await db.fetchval(
        "SELECT COUNT(*) FROM entity_changes WHERE entity_type='person' AND entity_id=$1", pid
    )
    assert row["display_label"] == "Office"
    assert after - before == 0


async def test_write_contact_methods_null_fill_idempotent(db):
    """Second re-observation after label already filled → no new entity_changes."""
    pid = generate_id()
    await db.execute("INSERT INTO people (id) VALUES ($1)", pid)
    cm1 = ObservationContactMethod(contact_type="phone", value="(206) 555-0103")
    await write_contact_methods(db, pid, "person", [cm1])
    cm2 = ObservationContactMethod(contact_type="phone", value="(206) 555-0103", display_label="WA")
    await write_contact_methods(db, pid, "person", [cm2])
    before = await db.fetchval(
        "SELECT COUNT(*) FROM entity_changes WHERE entity_type='person' AND entity_id=$1", pid
    )
    await write_contact_methods(db, pid, "person", [cm2])
    after = await db.fetchval(
        "SELECT COUNT(*) FROM entity_changes WHERE entity_type='person' AND entity_id=$1", pid
    )
    assert after - before == 0


async def test_write_contact_methods_empty_string_label_skipped(db):
    """Empty string display_label is not written; no entity_changes row emitted."""
    pid = generate_id()
    await db.execute("INSERT INTO people (id) VALUES ($1)", pid)
    cm1 = ObservationContactMethod(contact_type="phone", value="(206) 555-0104")
    await write_contact_methods(db, pid, "person", [cm1])
    before = await db.fetchval(
        "SELECT COUNT(*) FROM entity_changes WHERE entity_type='person' AND entity_id=$1", pid
    )
    cm2 = ObservationContactMethod(contact_type="phone", value="(206) 555-0104", display_label="")
    await write_contact_methods(db, pid, "person", [cm2])
    row = await db.fetchrow(
        "SELECT display_label FROM contact_methods WHERE entity_type='person' AND entity_id=$1",
        pid,
    )
    after = await db.fetchval(
        "SELECT COUNT(*) FROM entity_changes WHERE entity_type='person' AND entity_id=$1", pid
    )
    assert row["display_label"] is None
    assert after - before == 0


async def test_write_contact_methods_initial_empty_string_stored_as_null(db):
    """Initial INSERT with display_label='' stores NULL so future NULL-fill can land."""
    pid = generate_id()
    await db.execute("INSERT INTO people (id) VALUES ($1)", pid)
    cm1 = ObservationContactMethod(contact_type="phone", value="(206) 555-0105", display_label="")
    await write_contact_methods(db, pid, "person", [cm1])
    row = await db.fetchrow(
        "SELECT display_label FROM contact_methods WHERE entity_type='person' AND entity_id=$1",
        pid,
    )
    assert row["display_label"] is None
    # Confirm a subsequent real label can fill the slot
    cm2 = ObservationContactMethod(
        contact_type="phone", value="(206) 555-0105", display_label="Main"
    )
    await write_contact_methods(db, pid, "person", [cm2])
    row2 = await db.fetchrow(
        "SELECT display_label FROM contact_methods WHERE entity_type='person' AND entity_id=$1",
        pid,
    )
    assert row2["display_label"] == "Main"


# ---------------------------------------------------------------------------
# write_addresses
# ---------------------------------------------------------------------------


async def test_write_addresses_basic(db, org_id, monkeypatch):
    """write_addresses inserts an address row and an entity_addresses join."""
    # Disable external validator → falls back to LocalAddressNormalizer (usaddress)
    monkeypatch.delenv("ADDRESS_VALIDATOR_API_KEY", raising=False)
    addr_mod._reset_normalizer()
    try:
        addr = ObservationAddress(
            raw_input="123 Main St, Seattle, WA 98101", address_type="mailing"
        )
        await write_addresses(db, org_id, "organization", [addr])
        rows = await db.fetch(
            "SELECT ea.address_type, a.raw_input FROM entity_addresses ea"
            " JOIN addresses a ON a.id = ea.address_id"
            " WHERE ea.entity_type='organization' AND ea.entity_id=$1",
            org_id,
        )
        assert len(rows) == 1
        assert rows[0]["address_type"] == "mailing"
    finally:
        addr_mod._reset_normalizer()


async def test_write_addresses_duplicate_noop(db, org_id, monkeypatch):
    monkeypatch.delenv("ADDRESS_VALIDATOR_API_KEY", raising=False)
    addr_mod._reset_normalizer()
    try:
        addr = ObservationAddress(
            raw_input="123 Main St, Seattle, WA 98101", address_type="mailing"
        )
        await write_addresses(db, org_id, "organization", [addr])
        await write_addresses(db, org_id, "organization", [addr])
        rows = await db.fetch(
            "SELECT id FROM entity_addresses WHERE entity_type='organization' AND entity_id=$1",
            org_id,
        )
        assert len(rows) == 1
    finally:
        addr_mod._reset_normalizer()


async def test_write_addresses_insert_writes_entity_changes(db, monkeypatch):
    """Initial INSERT of an address writes an entity_changes row."""
    monkeypatch.delenv("ADDRESS_VALIDATOR_API_KEY", raising=False)
    addr_mod._reset_normalizer()
    try:
        oid = generate_id()
        await db.execute("INSERT INTO organizations (id) VALUES ($1)", oid)
        before = await db.fetchval(
            "SELECT COUNT(*) FROM entity_changes WHERE entity_type='organization' AND entity_id=$1",
            oid,
        )
        addr = ObservationAddress(
            raw_input="456 Pine St, Seattle, WA 98101", address_type="physical"
        )
        await write_addresses(db, oid, "organization", [addr])
        after = await db.fetchval(
            "SELECT COUNT(*) FROM entity_changes WHERE entity_type='organization' AND entity_id=$1",
            oid,
        )
        assert after - before == 1
    finally:
        addr_mod._reset_normalizer()


async def test_write_addresses_null_fill_updates_display_name(db, monkeypatch):
    """display_name NULL-filled from re-observation; entity_changes row written."""
    monkeypatch.delenv("ADDRESS_VALIDATOR_API_KEY", raising=False)
    addr_mod._reset_normalizer()
    try:
        oid = generate_id()
        await db.execute("INSERT INTO organizations (id) VALUES ($1)", oid)
        addr1 = ObservationAddress(
            raw_input="789 Oak Ave, Seattle, WA 98101", address_type="mailing"
        )
        await write_addresses(db, oid, "organization", [addr1])
        before = await db.fetchval(
            "SELECT COUNT(*) FROM entity_changes WHERE entity_type='organization' AND entity_id=$1",
            oid,
        )
        addr2 = ObservationAddress(
            raw_input="789 Oak Ave, Seattle, WA 98101", address_type="mailing", display_name="HQ"
        )
        await write_addresses(db, oid, "organization", [addr2])
        row = await db.fetchrow(
            "SELECT ea.display_name FROM entity_addresses ea"
            " WHERE ea.entity_type='organization' AND ea.entity_id=$1",
            oid,
        )
        after = await db.fetchval(
            "SELECT COUNT(*) FROM entity_changes WHERE entity_type='organization' AND entity_id=$1",
            oid,
        )
        assert row["display_name"] == "HQ"
        assert after - before == 1
    finally:
        addr_mod._reset_normalizer()


async def test_write_addresses_existing_display_name_not_overwritten(db, monkeypatch):
    """Non-NULL display_name is not overwritten; no entity_changes row written."""
    monkeypatch.delenv("ADDRESS_VALIDATOR_API_KEY", raising=False)
    addr_mod._reset_normalizer()
    try:
        oid = generate_id()
        await db.execute("INSERT INTO organizations (id) VALUES ($1)", oid)
        addr1 = ObservationAddress(
            raw_input="321 Elm St, Seattle, WA 98101", address_type="mailing", display_name="Branch"
        )
        await write_addresses(db, oid, "organization", [addr1])
        before = await db.fetchval(
            "SELECT COUNT(*) FROM entity_changes WHERE entity_type='organization' AND entity_id=$1",
            oid,
        )
        addr2 = ObservationAddress(
            raw_input="321 Elm St, Seattle, WA 98101", address_type="mailing", display_name="HQ"
        )
        await write_addresses(db, oid, "organization", [addr2])
        row = await db.fetchrow(
            "SELECT ea.display_name FROM entity_addresses ea"
            " WHERE ea.entity_type='organization' AND ea.entity_id=$1",
            oid,
        )
        after = await db.fetchval(
            "SELECT COUNT(*) FROM entity_changes WHERE entity_type='organization' AND entity_id=$1",
            oid,
        )
        assert row["display_name"] == "Branch"
        assert after - before == 0
    finally:
        addr_mod._reset_normalizer()


async def test_write_addresses_null_fill_idempotent(db, monkeypatch):
    """Second re-observation after display_name already filled → no new entity_changes."""
    monkeypatch.delenv("ADDRESS_VALIDATOR_API_KEY", raising=False)
    addr_mod._reset_normalizer()
    try:
        oid = generate_id()
        await db.execute("INSERT INTO organizations (id) VALUES ($1)", oid)
        addr1 = ObservationAddress(
            raw_input="654 Maple Dr, Seattle, WA 98101", address_type="other"
        )
        await write_addresses(db, oid, "organization", [addr1])
        addr2 = ObservationAddress(
            raw_input="654 Maple Dr, Seattle, WA 98101", address_type="other", display_name="Annex"
        )
        await write_addresses(db, oid, "organization", [addr2])
        before = await db.fetchval(
            "SELECT COUNT(*) FROM entity_changes WHERE entity_type='organization' AND entity_id=$1",
            oid,
        )
        await write_addresses(db, oid, "organization", [addr2])
        after = await db.fetchval(
            "SELECT COUNT(*) FROM entity_changes WHERE entity_type='organization' AND entity_id=$1",
            oid,
        )
        assert after - before == 0
    finally:
        addr_mod._reset_normalizer()


async def test_write_addresses_empty_string_display_name_skipped(db, monkeypatch):
    """Empty string display_name is not written; no entity_changes row emitted."""
    monkeypatch.delenv("ADDRESS_VALIDATOR_API_KEY", raising=False)
    addr_mod._reset_normalizer()
    try:
        oid = generate_id()
        await db.execute("INSERT INTO organizations (id) VALUES ($1)", oid)
        addr1 = ObservationAddress(
            raw_input="987 Cedar Rd, Seattle, WA 98101", address_type="mailing"
        )
        await write_addresses(db, oid, "organization", [addr1])
        before = await db.fetchval(
            "SELECT COUNT(*) FROM entity_changes WHERE entity_type='organization' AND entity_id=$1",
            oid,
        )
        addr2 = ObservationAddress(
            raw_input="987 Cedar Rd, Seattle, WA 98101", address_type="mailing", display_name=""
        )
        await write_addresses(db, oid, "organization", [addr2])
        row = await db.fetchrow(
            "SELECT ea.display_name FROM entity_addresses ea"
            " WHERE ea.entity_type='organization' AND ea.entity_id=$1",
            oid,
        )
        after = await db.fetchval(
            "SELECT COUNT(*) FROM entity_changes WHERE entity_type='organization' AND entity_id=$1",
            oid,
        )
        assert row["display_name"] is None
        assert after - before == 0
    finally:
        addr_mod._reset_normalizer()


async def test_write_addresses_initial_empty_string_stored_as_null(db, monkeypatch):
    """Initial INSERT with display_name='' stores NULL so future NULL-fill can land."""
    monkeypatch.delenv("ADDRESS_VALIDATOR_API_KEY", raising=False)
    addr_mod._reset_normalizer()
    try:
        oid = generate_id()
        await db.execute("INSERT INTO organizations (id) VALUES ($1)", oid)
        addr1 = ObservationAddress(
            raw_input="111 Test Ave, Seattle, WA 98101",
            address_type="mailing",
            display_name="",
        )
        await write_addresses(db, oid, "organization", [addr1])
        row = await db.fetchrow(
            "SELECT ea.display_name FROM entity_addresses ea"
            " WHERE ea.entity_type='organization' AND ea.entity_id=$1",
            oid,
        )
        assert row["display_name"] is None
        # Confirm a subsequent real label can fill the slot
        addr2 = ObservationAddress(
            raw_input="111 Test Ave, Seattle, WA 98101",
            address_type="mailing",
            display_name="HQ",
        )
        await write_addresses(db, oid, "organization", [addr2])
        row2 = await db.fetchrow(
            "SELECT ea.display_name FROM entity_addresses ea"
            " WHERE ea.entity_type='organization' AND ea.entity_id=$1",
            oid,
        )
        assert row2["display_name"] == "HQ"
    finally:
        addr_mod._reset_normalizer()


# ---------------------------------------------------------------------------
# write_org_acronyms
# ---------------------------------------------------------------------------


async def test_write_org_acronyms_appends(db, org_id):
    await write_org_acronyms(db, org_id, [ObservationAcronym(acronym="ACME")])
    rows = await db.fetch(
        "SELECT acronym, is_canonical FROM organization_acronyms WHERE organization_id=$1",
        org_id,
    )
    assert len(rows) == 1
    assert rows[0]["acronym"] == "ACME"
    assert rows[0]["is_canonical"] is True


async def test_write_org_acronyms_duplicate_noop(db, org_id):
    await write_org_acronyms(db, org_id, [ObservationAcronym(acronym="ACME")])
    await write_org_acronyms(db, org_id, [ObservationAcronym(acronym="ACME")])
    rows = await db.fetch("SELECT id FROM organization_acronyms WHERE organization_id=$1", org_id)
    assert len(rows) == 1


async def test_write_org_acronyms_second_not_canonical(db):
    oid = generate_id()
    await db.execute("INSERT INTO organizations (id) VALUES ($1)", oid)
    await write_org_acronyms(db, oid, [ObservationAcronym(acronym="WLEG")])
    await write_org_acronyms(db, oid, [ObservationAcronym(acronym="WA-LEG")])
    rows = await db.fetch(
        "SELECT acronym, is_canonical FROM organization_acronyms"
        " WHERE organization_id=$1 ORDER BY created_at",
        oid,
    )
    assert len(rows) == 2
    assert rows[0]["is_canonical"] is True
    assert rows[1]["is_canonical"] is False


async def test_write_org_acronyms_canonical_hint_promotes_specific(db):
    """is_canonical=True on non-first acronym → that one becomes canonical."""
    oid = generate_id()
    await db.execute("INSERT INTO organizations (id) VALUES ($1)", oid)
    acronyms = [
        ObservationAcronym(acronym="WL", is_canonical=False),
        ObservationAcronym(acronym="WLEG", is_canonical=True),
    ]
    await write_org_acronyms(db, oid, acronyms)
    rows = await db.fetch(
        "SELECT acronym, is_canonical FROM organization_acronyms WHERE organization_id=$1",
        oid,
    )
    by_acronym = {r["acronym"]: r["is_canonical"] for r in rows}
    assert by_acronym["WL"] is False
    assert by_acronym["WLEG"] is True


async def test_write_org_acronyms_canonical_hint_no_displace(db):
    """is_canonical=True does not displace an already-canonical acronym."""
    oid = generate_id()
    await db.execute("INSERT INTO organizations (id) VALUES ($1)", oid)
    await write_org_acronyms(db, oid, [ObservationAcronym(acronym="FIRST")])
    await write_org_acronyms(db, oid, [ObservationAcronym(acronym="SECOND", is_canonical=True)])
    rows = await db.fetch(
        "SELECT acronym, is_canonical FROM organization_acronyms"
        " WHERE organization_id=$1 ORDER BY created_at",
        oid,
    )
    assert rows[0]["acronym"] == "FIRST"
    assert rows[0]["is_canonical"] is True
    assert rows[1]["acronym"] == "SECOND"
    assert rows[1]["is_canonical"] is False


# ---------------------------------------------------------------------------
# write_role_assignments
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture(loop_scope="session")
async def role_id(db, org_id):
    rid = generate_id()
    await db.execute(
        "INSERT INTO roles (id, organization_id, title) VALUES ($1, $2, $3)",
        rid,
        org_id,
        "Test Role",
    )
    return rid


async def test_write_role_assignments_creates(db, person_id, role_id):
    ra = ObservationRoleAssignment(role_id=role_id, start_date="2024-01-01")
    await write_role_assignments(db, person_id, [ra])
    rows = await db.fetch(
        "SELECT role_id, start_date FROM role_assignments WHERE person_id=$1", person_id
    )
    assert len(rows) == 1
    assert rows[0]["role_id"] == role_id


async def test_write_role_assignments_open_noop(db, person_id, role_id):
    """Open assignment (no end_date) already exists → no-op."""
    ra_id = generate_id()
    await db.execute(
        "INSERT INTO role_assignments (id, person_id, role_id, start_date) VALUES ($1, $2, $3, $4)",
        ra_id,
        person_id,
        role_id,
        date(2023, 1, 1),
    )
    ra = ObservationRoleAssignment(role_id=role_id, start_date="2024-01-01")
    await write_role_assignments(db, person_id, [ra])
    rows = await db.fetch(
        "SELECT id FROM role_assignments WHERE person_id=$1 AND role_id=$2",
        person_id,
        role_id,
    )
    assert len(rows) == 1


# ---------------------------------------------------------------------------
# write_org_parent
# ---------------------------------------------------------------------------


async def test_write_org_parent_sets_when_null(db, org_id):
    parent_id = generate_id()
    await db.execute("INSERT INTO organizations (id) VALUES ($1)", parent_id)
    await write_org_parent(db, org_id, parent_id)
    row = await db.fetchrow("SELECT parent_id FROM organizations WHERE id=$1", org_id)
    assert row["parent_id"] == parent_id


async def test_write_org_parent_noop_when_set(db, org_id):
    p1 = generate_id()
    p2 = generate_id()
    await db.execute("INSERT INTO organizations (id) VALUES ($1)", p1)
    await db.execute("INSERT INTO organizations (id) VALUES ($1)", p2)
    await write_org_parent(db, org_id, p1)
    await write_org_parent(db, org_id, p2)
    row = await db.fetchrow("SELECT parent_id FROM organizations WHERE id=$1", org_id)
    assert row["parent_id"] == p1


# ---------------------------------------------------------------------------
# write_pronouns
# ---------------------------------------------------------------------------


async def test_write_pronouns_sets_when_null(db, person_id):
    await write_pronouns(db, person_id, "she/her")
    row = await db.fetchrow("SELECT personal_pronouns FROM people WHERE id=$1", person_id)
    assert row["personal_pronouns"] == "she/her"


async def test_write_pronouns_noop_when_set(db, person_id):
    await write_pronouns(db, person_id, "she/her")
    await write_pronouns(db, person_id, "they/them")
    row = await db.fetchrow("SELECT personal_pronouns FROM people WHERE id=$1", person_id)
    assert row["personal_pronouns"] == "she/her"


# ---------------------------------------------------------------------------
# write_additional_identifiers
# ---------------------------------------------------------------------------


async def test_write_additional_identifiers_new_type(db, person_id):
    item = ObservationAdditionalIdentifier(
        identifier_type_slug="person_ssn", identifier_value="123-45-6789"
    )
    await write_additional_identifiers(db, person_id, [item])
    eit = await db.fetchrow("SELECT id FROM entity_identifier_types WHERE slug='person_ssn'")
    row = await db.fetchrow(
        "SELECT value FROM identifiers WHERE entity_id=$1 AND entity_identifier_type_id=$2",
        person_id,
        eit["id"],
    )
    assert row is not None
    assert row["value"] == "123-45-6789"


async def test_write_additional_identifiers_same_value_noop(db, person_id):
    item = ObservationAdditionalIdentifier(
        identifier_type_slug="person_ssn", identifier_value="123-45-6789"
    )
    await write_additional_identifiers(db, person_id, [item])
    await write_additional_identifiers(db, person_id, [item])
    eit = await db.fetchrow("SELECT id FROM entity_identifier_types WHERE slug='person_ssn'")
    rows = await db.fetch(
        "SELECT id FROM identifiers WHERE entity_id=$1 AND entity_identifier_type_id=$2",
        person_id,
        eit["id"],
    )
    assert len(rows) == 1


async def test_write_additional_identifiers_conflict_raises(db, person_id):
    mk = lambda v: [  # noqa: E731
        ObservationAdditionalIdentifier(identifier_type_slug="person_ssn", identifier_value=v)
    ]
    await write_additional_identifiers(db, person_id, mk("123"))
    with pytest.raises(IdentifierConflict) as exc:
        await write_additional_identifiers(db, person_id, mk("999"))
    assert exc.value.identifier_type_slug == "person_ssn"
