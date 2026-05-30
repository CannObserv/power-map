"""Integration tests for src.core.observation per-surface writers."""

import hashlib
import os
from datetime import date

import pytest
import pytest_asyncio

from src.api.public.schemas import (
    ObservationAdditionalIdentifier,
    ObservationAddress,
    ObservationContactMethod,
    ObservationLink,
    ObservationName,
    ObservationNameParts,
    ObservationRoleAssignment,
)
from src.core.db import generate_id
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
    pytest.mark.asyncio(loop_scope="session"),
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
# write_names
# ---------------------------------------------------------------------------


async def test_write_names_appends_new_person_name(db, person_id, api_key_id):
    name = ObservationName(name="Jane Doe", name_type="legal")
    await write_names(db, person_id, "person", api_key_id, [name])
    rows = await db.fetch(
        "SELECT name, source_key_id FROM person_names WHERE person_id=$1", person_id
    )
    assert len(rows) == 1
    assert rows[0]["name"] == "Jane Doe"
    assert rows[0]["source_key_id"] == api_key_id


async def test_write_names_exact_match_is_noop(db, person_id, api_key_id):
    name = ObservationName(name="Jane Doe", name_type="legal")
    await write_names(db, person_id, "person", api_key_id, [name])
    await write_names(db, person_id, "person", api_key_id, [name])
    rows = await db.fetch("SELECT id FROM person_names WHERE person_id=$1", person_id)
    assert len(rows) == 1


async def test_write_names_parts_written_on_new_row(db, person_id, api_key_id):
    parts = ObservationNameParts(given_names=["Jane"], family_names=["Doe"])
    name = ObservationName(name="Jane Doe", name_type="legal", parts=parts)
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


async def test_write_names_organization(db, org_id, api_key_id):
    name = ObservationName(name="Acme Corp", name_type="legal")
    await write_names(db, org_id, "organization", api_key_id, [name])
    rows = await db.fetch(
        "SELECT name, source_key_id FROM organization_names WHERE organization_id=$1", org_id
    )
    assert len(rows) == 1
    assert rows[0]["name"] == "Acme Corp"
    assert rows[0]["source_key_id"] == api_key_id


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


# ---------------------------------------------------------------------------
# write_addresses
# ---------------------------------------------------------------------------


async def test_write_addresses_basic(db, org_id, monkeypatch):
    """write_addresses inserts an address row and an entity_addresses join."""
    # Disable external validator → falls back to LocalAddressNormalizer (usaddress)
    monkeypatch.delenv("ADDRESS_VALIDATOR_API_KEY", raising=False)
    from src.core.normalizers import address as addr_mod

    addr_mod._reset_normalizer()
    addr = ObservationAddress(raw_input="123 Main St, Seattle, WA 98101", address_type="mailing")
    await write_addresses(db, org_id, "organization", [addr])
    rows = await db.fetch(
        "SELECT ea.address_type, a.raw_input FROM entity_addresses ea"
        " JOIN addresses a ON a.id = ea.address_id"
        " WHERE ea.entity_type='organization' AND ea.entity_id=$1",
        org_id,
    )
    assert len(rows) == 1
    assert rows[0]["address_type"] == "mailing"
    addr_mod._reset_normalizer()


async def test_write_addresses_duplicate_noop(db, org_id, monkeypatch):
    monkeypatch.delenv("ADDRESS_VALIDATOR_API_KEY", raising=False)
    from src.core.normalizers import address as addr_mod

    addr_mod._reset_normalizer()
    addr = ObservationAddress(raw_input="123 Main St, Seattle, WA 98101", address_type="mailing")
    await write_addresses(db, org_id, "organization", [addr])
    await write_addresses(db, org_id, "organization", [addr])
    rows = await db.fetch(
        "SELECT id FROM entity_addresses WHERE entity_type='organization' AND entity_id=$1",
        org_id,
    )
    assert len(rows) == 1
    addr_mod._reset_normalizer()


# ---------------------------------------------------------------------------
# write_org_acronyms
# ---------------------------------------------------------------------------


async def test_write_org_acronyms_appends(db, org_id):
    await write_org_acronyms(db, org_id, ["ACME"])
    rows = await db.fetch(
        "SELECT acronym, is_canonical FROM organization_acronyms WHERE organization_id=$1",
        org_id,
    )
    assert len(rows) == 1
    assert rows[0]["acronym"] == "ACME"
    assert rows[0]["is_canonical"] is False


async def test_write_org_acronyms_duplicate_noop(db, org_id):
    await write_org_acronyms(db, org_id, ["ACME"])
    await write_org_acronyms(db, org_id, ["ACME"])
    rows = await db.fetch("SELECT id FROM organization_acronyms WHERE organization_id=$1", org_id)
    assert len(rows) == 1


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
