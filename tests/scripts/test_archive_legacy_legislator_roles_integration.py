"""Integration tests for the #265 legacy legislator role validator/archiver.

Requires TEST_DATABASE_URL and a schema-applied DB.

Run via:
    uv run pytest tests/scripts/test_archive_legacy_legislator_roles_integration.py
"""

import pytest
import pytest_asyncio

from scripts.archive_legacy_legislator_roles import archive_legacy_legislator_roles
from src.core.db import generate_id

pytestmark = [
    pytest.mark.integration,
]

_PDC_URL = (
    "https://www.pdc.wa.gov/browse/campaign-explorer/candidate"
    "?filer_id=CODYE%20%20126&election_year=2018"
)


@pytest_asyncio.fixture(loop_scope="session")
async def db(db_pool):
    async with db_pool.acquire() as conn:
        tr = conn.transaction()
        await tr.start()
        try:
            yield conn
        finally:
            await tr.rollback()


# ---------------------------------------------------------------------------
# Seed helpers
# ---------------------------------------------------------------------------


async def _identifier_type_id(db, slug: str) -> str:
    return await db.fetchval("SELECT id FROM entity_identifier_types WHERE slug = $1", slug)


async def _link_type_id(db, slug: str) -> str:
    return await db.fetchval("SELECT id FROM link_types WHERE slug = $1", slug)


async def _org(db, name: str, *, chamber: str | None = None, legislature: bool = False) -> str:
    """Seed an org; tag with the chamber/legislature identifier the resolver keys on."""
    oid = generate_id()
    await db.execute("INSERT INTO organizations (id) VALUES ($1)", oid)
    await db.execute(
        "INSERT INTO organization_names (id, organization_id, name, is_canonical)"
        " VALUES ($1, $2, $3, TRUE)",
        generate_id(),
        oid,
        name,
    )
    if chamber:
        await db.execute(
            "INSERT INTO identifiers (id, entity_id, entity_identifier_type_id, value)"
            " VALUES ($1, $2, $3, $4)",
            generate_id(),
            oid,
            await _identifier_type_id(db, "org_wa_legislature_chamber"),
            f"usa_wa_{chamber}",
        )
    if legislature:
        await db.execute(
            "INSERT INTO identifiers (id, entity_id, entity_identifier_type_id, value)"
            " VALUES ($1, $2, $3, $4)",
            generate_id(),
            oid,
            await _identifier_type_id(db, "org_wa_legislature"),
            "usa_wa_legislature",
        )
    return oid


@pytest_asyncio.fixture(loop_scope="session")
async def orgs(db) -> dict[str, str]:
    return {
        "house": await _org(db, "Test WA House", chamber="house"),
        "senate": await _org(db, "Test WA Senate", chamber="senate"),
        "legislature": await _org(db, "Test WA Legislature", legislature=True),
    }


async def _person(db) -> str:
    pid = generate_id()
    await db.execute("INSERT INTO people (id) VALUES ($1)", pid)
    return pid


async def _jurisdiction(db, district: int) -> str:
    """Seed (or adopt) the LD jurisdiction the seat-match keys on."""
    type_id = await db.fetchval("SELECT id FROM jurisdiction_types LIMIT 1")
    return await db.fetchval(
        """
        INSERT INTO jurisdictions (id, slug, name, type_id)
        VALUES ($1, $2, $3, $4)
        ON CONFLICT (slug) DO UPDATE SET name = EXCLUDED.name
        RETURNING id
        """,
        generate_id(),
        f"usa-wa-ld-{district}",
        f"Washington Legislative District {district}",
        type_id,
    )


async def _seat(db, org_id: str, chamber: str, district: int, qualifier: str | None = None) -> str:
    """Seed a typed seat-Role (the #263 shape)."""
    role_type_slug = "state_senator" if chamber == "senate" else "state_representative"
    role_type_id = await db.fetchval("SELECT id FROM role_types WHERE slug = $1", role_type_slug)
    title = (
        f"Washington State {'Senator' if chamber == 'senate' else 'Representative'}, LD-{district}"
    )
    if qualifier:
        title += f", {qualifier}"
    rid = generate_id()
    await db.execute(
        """
        INSERT INTO roles (id, organization_id, title, role_type_id, jurisdiction_id, qualifier)
        VALUES ($1, $2, $3, $4, $5, $6)
        """,
        rid,
        org_id,
        title,
        role_type_id,
        await _jurisdiction(db, district),
        qualifier,
    )
    return rid


async def _legacy_role(db, org_id: str, title: str) -> str:
    rid = generate_id()
    await db.execute(
        "INSERT INTO roles (id, organization_id, title) VALUES ($1, $2, $3)", rid, org_id, title
    )
    return rid


async def _assign(db, person_id: str, role_id: str, *, current: bool = False, start=None) -> str:
    aid = generate_id()
    await db.execute(
        """
        INSERT INTO role_assignments (id, person_id, role_id, is_current, start_date)
        VALUES ($1, $2, $3, $4, $5)
        """,
        aid,
        person_id,
        role_id,
        current,
        start,
    )
    return aid


async def _link(db, entity_id: str, url: str, *, slug: str = "profile") -> str:
    lid = generate_id()
    await db.execute(
        """
        INSERT INTO links (id, entity_type, entity_id, url, link_type_id)
        VALUES ($1, 'role_assignment', $2, $3, $4)
        """,
        lid,
        entity_id,
        url,
        await _link_type_id(db, slug),
    )
    return lid


async def _contact(db, entity_id: str, value: str) -> str:
    cid = generate_id()
    await db.execute(
        """
        INSERT INTO contact_methods (id, entity_type, entity_id, contact_type, value)
        VALUES ($1, 'role_assignment', $2, 'email', $3)
        """,
        cid,
        entity_id,
        value,
    )
    return cid


async def _fc(db, entity_id: str, field_name: str, value_hash: str) -> str:
    fid = generate_id()
    await db.execute(
        """
        INSERT INTO field_confidence
            (id, entity_type, entity_id, field_name, value_hash,
             source_reliability, validation_status)
        VALUES ($1, 'role_assignment', $2, $3, $4, 0.8, 'unconfirmed')
        """,
        fid,
        entity_id,
        field_name,
        value_hash,
    )
    return fid


async def _role_pdc(db, assignment_id: str, value: str) -> str:
    iid = generate_id()
    await db.execute(
        "INSERT INTO identifiers (id, entity_id, entity_identifier_type_id, value)"
        " VALUES ($1, $2, $3, $4)",
        iid,
        assignment_id,
        await _identifier_type_id(db, "role_wa_pdc"),
        value,
    )
    return iid


async def _archived_at(db, table: str, row_id: str):
    return await db.fetchval(f"SELECT archived_at FROM {table} WHERE id = $1", row_id)  # noqa: S608


def _action(report, assignment_id):
    return next(a for a in report["actions"] if a["assignment_id"] == assignment_id)


# ---------------------------------------------------------------------------
# Matching + archival
# ---------------------------------------------------------------------------


async def test_senate_district_match_archives_assignment_and_role(db, orgs):
    person = await _person(db)
    legacy_role = await _legacy_role(db, orgs["senate"], "Senator, District 5")
    legacy_assign = await _assign(db, person, legacy_role)
    seat = await _seat(db, orgs["senate"], "senate", 5)
    target = await _assign(db, person, seat, current=True, start=None)

    report = await archive_legacy_legislator_roles(db, execute=True)

    action = _action(report, legacy_assign)
    assert action["status"] == "archived"
    assert action["target_assignment_id"] == target
    assert await _archived_at(db, "role_assignments", legacy_assign) is not None
    assert await _archived_at(db, "roles", legacy_role) is not None
    assert legacy_role in report["archived_roles"]


async def test_house_district_match_via_position_seat(db, orgs):
    person = await _person(db)
    legacy_role = await _legacy_role(db, orgs["house"], "Representative, District 5")
    legacy_assign = await _assign(db, person, legacy_role)
    seat = await _seat(db, orgs["house"], "house", 5, qualifier="Position 2")
    await _assign(db, person, seat, current=True)

    report = await archive_legacy_legislator_roles(db, execute=True)

    assert _action(report, legacy_assign)["status"] == "archived"


async def test_generic_title_matches_chamber_wide(db, orgs):
    person = await _person(db)
    legacy_role = await _legacy_role(db, orgs["senate"], "Senator")
    legacy_assign = await _assign(db, person, legacy_role)
    seat = await _seat(db, orgs["senate"], "senate", 7)
    await _assign(db, person, seat, current=True)

    report = await archive_legacy_legislator_roles(db, execute=True)

    assert _action(report, legacy_assign)["status"] == "archived"


async def test_legislature_org_uses_title_chamber(db, orgs):
    person = await _person(db)
    legacy_role = await _legacy_role(db, orgs["legislature"], "Representative, District 5")
    legacy_assign = await _assign(db, person, legacy_role)
    seat = await _seat(db, orgs["house"], "house", 5, qualifier="Position 1")
    await _assign(db, person, seat, current=True)

    report = await archive_legacy_legislator_roles(db, execute=True)

    assert _action(report, legacy_assign)["status"] == "archived"


async def test_no_typed_assignment_is_unmatched_and_kept(db, orgs):
    person = await _person(db)
    legacy_role = await _legacy_role(db, orgs["senate"], "Senator, District 5")
    legacy_assign = await _assign(db, person, legacy_role)

    report = await archive_legacy_legislator_roles(db, execute=True)

    assert _action(report, legacy_assign)["status"] == "unmatched"
    assert await _archived_at(db, "role_assignments", legacy_assign) is None
    assert await _archived_at(db, "roles", legacy_role) is None


async def test_district_mismatch_is_unmatched(db, orgs):
    person = await _person(db)
    legacy_role = await _legacy_role(db, orgs["senate"], "Senator, District 5")
    legacy_assign = await _assign(db, person, legacy_role)
    seat = await _seat(db, orgs["senate"], "senate", 7)  # wrong district
    await _assign(db, person, seat, current=True)

    report = await archive_legacy_legislator_roles(db, execute=True)

    assert _action(report, legacy_assign)["status"] == "unmatched"


async def test_wrong_chamber_is_unmatched(db, orgs):
    person = await _person(db)
    legacy_role = await _legacy_role(db, orgs["senate"], "Senator")
    legacy_assign = await _assign(db, person, legacy_role)
    seat = await _seat(db, orgs["house"], "house", 5, qualifier="Position 1")
    await _assign(db, person, seat, current=True)  # house seat can't cover a Senator row

    report = await archive_legacy_legislator_roles(db, execute=True)

    assert _action(report, legacy_assign)["status"] == "unmatched"


async def test_generic_title_ignores_seats_on_foreign_orgs(db, orgs):
    person = await _person(db)
    legacy_role = await _legacy_role(db, orgs["senate"], "Senator")
    legacy_assign = await _assign(db, person, legacy_role)
    foreign = await _org(db, "Test Other-State Senate")  # no chamber identifier
    seat = await _seat(db, foreign, "senate", 11)
    await _assign(db, person, seat, current=True)

    report = await archive_legacy_legislator_roles(db, execute=True)

    assert _action(report, legacy_assign)["status"] == "unmatched"


async def test_district_title_ignores_seats_on_foreign_orgs(db, orgs):
    person = await _person(db)
    legacy_role = await _legacy_role(db, orgs["senate"], "Senator, District 5")
    legacy_assign = await _assign(db, person, legacy_role)
    foreign = await _org(db, "Test Other-State Senate")
    seat = await _seat(db, foreign, "senate", 5)  # matching LD slug, wrong org
    await _assign(db, person, seat, current=True)

    report = await archive_legacy_legislator_roles(db, execute=True)

    assert _action(report, legacy_assign)["status"] == "unmatched"


async def test_resolver_ignores_foreign_legislature_identifier_values(db, orgs):
    other = await _org(db, "Test Other Legislature")
    await db.execute(
        "INSERT INTO identifiers (id, entity_id, entity_identifier_type_id, value)"
        " VALUES ($1, $2, $3, $4)",
        generate_id(),
        other,
        await _identifier_type_id(db, "org_wa_legislature"),
        "usa_or_legislature",  # different value must not trip the exactly-one check
    )

    report = await archive_legacy_legislator_roles(db, execute=False)

    assert report["actions"] == []


async def test_staff_title_is_excluded_and_untouched(db, orgs):
    person = await _person(db)
    legacy_role = await _legacy_role(db, orgs["senate"], "Secretary of the Senate")
    legacy_assign = await _assign(db, person, legacy_role)
    seat = await _seat(db, orgs["senate"], "senate", 5)
    await _assign(db, person, seat, current=True)  # person coverage must NOT validate staff rows

    report = await archive_legacy_legislator_roles(db, execute=True)

    assert _action(report, legacy_assign)["status"] == "excluded"
    assert await _archived_at(db, "role_assignments", legacy_assign) is None
    assert await _archived_at(db, "roles", legacy_role) is None


async def test_role_kept_while_any_assignment_remains_active(db, orgs):
    matched = await _person(db)
    unmatched = await _person(db)
    legacy_role = await _legacy_role(db, orgs["senate"], "Senator")
    matched_assign = await _assign(db, matched, legacy_role)
    unmatched_assign = await _assign(db, unmatched, legacy_role)
    seat = await _seat(db, orgs["senate"], "senate", 9)
    await _assign(db, matched, seat, current=True)

    report = await archive_legacy_legislator_roles(db, execute=True)

    assert _action(report, matched_assign)["status"] == "archived"
    assert _action(report, unmatched_assign)["status"] == "unmatched"
    assert await _archived_at(db, "roles", legacy_role) is None
    assert legacy_role not in report["archived_roles"]


async def test_assignmentless_seat_shaped_role_is_archived(db, orgs):
    legacy_role = await _legacy_role(db, orgs["senate"], "Senator, District 3")

    report = await archive_legacy_legislator_roles(db, execute=True)

    assert await _archived_at(db, "roles", legacy_role) is not None
    assert legacy_role in report["archived_roles"]


# ---------------------------------------------------------------------------
# Ancillary migration
# ---------------------------------------------------------------------------


async def test_links_contacts_fc_migrate_to_target(db, orgs):
    person = await _person(db)
    legacy_role = await _legacy_role(db, orgs["senate"], "Senator, District 5")
    legacy_assign = await _assign(db, person, legacy_role)
    seat = await _seat(db, orgs["senate"], "senate", 5)
    target = await _assign(db, person, seat, current=True)

    link = await _link(db, legacy_assign, "https://sdc.wastateleg.org/test")
    contact = await _contact(db, legacy_assign, "test.senator@leg.wa.gov")
    fc = await _fc(db, legacy_assign, "email", "hash-1")

    report = await archive_legacy_legislator_roles(db, execute=True)

    assert _action(report, legacy_assign)["status"] == "archived"
    assert await db.fetchval("SELECT entity_id FROM links WHERE id = $1", link) == target
    assert (
        await db.fetchval("SELECT entity_id FROM contact_methods WHERE id = $1", contact) == target
    )
    assert await db.fetchval("SELECT entity_id FROM field_confidence WHERE id = $1", fc) == target


async def test_duplicate_ancillary_on_target_is_deleted_not_duplicated(db, orgs):
    person = await _person(db)
    legacy_role = await _legacy_role(db, orgs["senate"], "Senator, District 5")
    legacy_assign = await _assign(db, person, legacy_role)
    seat = await _seat(db, orgs["senate"], "senate", 5)
    target = await _assign(db, person, seat, current=True)

    url = "https://sdc.wastateleg.org/test"
    legacy_link = await _link(db, legacy_assign, url)
    await _link(db, target, url)  # target already carries it
    legacy_contact = await _contact(db, legacy_assign, "test.senator@leg.wa.gov")
    await _contact(db, target, "test.senator@leg.wa.gov")
    legacy_fc = await _fc(db, legacy_assign, "email", "hash-1")
    await _fc(db, target, "email", "hash-1")

    await archive_legacy_legislator_roles(db, execute=True)

    assert await db.fetchval("SELECT COUNT(*) FROM links WHERE id = $1", legacy_link) == 0
    assert (
        await db.fetchval("SELECT COUNT(*) FROM contact_methods WHERE id = $1", legacy_contact) == 0
    )
    assert await db.fetchval("SELECT COUNT(*) FROM field_confidence WHERE id = $1", legacy_fc) == 0
    assert (
        await db.fetchval(
            "SELECT COUNT(*) FROM links WHERE entity_type = 'role_assignment' AND entity_id = $1",
            target,
        )
        == 1
    )


async def test_pdc_url_rescued_to_person_identifier_and_link(db, orgs):
    person = await _person(db)
    legacy_role = await _legacy_role(db, orgs["senate"], "Senator, District 5")
    legacy_assign = await _assign(db, person, legacy_role)
    seat = await _seat(db, orgs["senate"], "senate", 5)
    await _assign(db, person, seat, current=True)
    pdc_row = await _role_pdc(db, legacy_assign, _PDC_URL)

    report = await archive_legacy_legislator_roles(db, execute=True)

    assert _action(report, legacy_assign)["status"] == "archived"
    # filer ID minted on the person
    filer = await db.fetchval(
        """
        SELECT i.value FROM identifiers i
        JOIN entity_identifier_types t ON t.id = i.entity_identifier_type_id
        WHERE t.slug = 'person_wa_pdc_filer' AND i.entity_id = $1
        """,
        person,
    )
    assert filer == "CODYE  126"
    # source URL preserved as a person-level wa_pdc link
    url = await db.fetchval(
        """
        SELECT l.url FROM links l
        JOIN link_types lt ON lt.id = l.link_type_id
        WHERE lt.slug = 'wa_pdc' AND l.entity_type = 'person' AND l.entity_id = $1
        """,
        person,
    )
    assert url == _PDC_URL
    # legacy role_wa_pdc row deleted
    assert await db.fetchval("SELECT COUNT(*) FROM identifiers WHERE id = $1", pdc_row) == 0


async def test_pdc_rescue_is_idempotent_when_person_already_has_filer(db, orgs):
    person = await _person(db)
    await db.execute(
        "INSERT INTO identifiers (id, entity_id, entity_identifier_type_id, value)"
        " VALUES ($1, $2, $3, $4)",
        generate_id(),
        person,
        await _identifier_type_id(db, "person_wa_pdc_filer"),
        "CODYE  126",
    )
    legacy_role = await _legacy_role(db, orgs["senate"], "Senator, District 5")
    legacy_assign = await _assign(db, person, legacy_role)
    seat = await _seat(db, orgs["senate"], "senate", 5)
    await _assign(db, person, seat, current=True)
    await _role_pdc(db, legacy_assign, _PDC_URL)

    await archive_legacy_legislator_roles(db, execute=True)

    n = await db.fetchval(
        """
        SELECT COUNT(*) FROM identifiers i
        JOIN entity_identifier_types t ON t.id = i.entity_identifier_type_id
        WHERE t.slug = 'person_wa_pdc_filer' AND i.entity_id = $1
        """,
        person,
    )
    assert n == 1


async def test_unparseable_pdc_value_is_conflict_and_kept(db, orgs):
    person = await _person(db)
    legacy_role = await _legacy_role(db, orgs["senate"], "Senator, District 5")
    legacy_assign = await _assign(db, person, legacy_role)
    seat = await _seat(db, orgs["senate"], "senate", 5)
    await _assign(db, person, seat, current=True)
    pdc_row = await _role_pdc(db, legacy_assign, "not-a-url")
    link = await _link(db, legacy_assign, "https://sdc.wastateleg.org/test")

    report = await archive_legacy_legislator_roles(db, execute=True)

    assert _action(report, legacy_assign)["status"] == "conflict"
    assert await _archived_at(db, "role_assignments", legacy_assign) is None
    # nothing migrated, nothing deleted
    assert await db.fetchval("SELECT COUNT(*) FROM identifiers WHERE id = $1", pdc_row) == 1
    assert await db.fetchval("SELECT entity_id FROM links WHERE id = $1", link) == legacy_assign


async def test_migration_emits_outbox_events_for_target_and_person(db, orgs):
    person = await _person(db)
    legacy_role = await _legacy_role(db, orgs["senate"], "Senator, District 5")
    legacy_assign = await _assign(db, person, legacy_role)
    seat = await _seat(db, orgs["senate"], "senate", 5)
    target = await _assign(db, person, seat, current=True)
    await _link(db, legacy_assign, "https://sdc.wastateleg.org/test")
    await _role_pdc(db, legacy_assign, _PDC_URL)

    async def _events(entity_id: str) -> int:
        return await db.fetchval(
            "SELECT COUNT(*) FROM entity_changes WHERE entity_id = $1 AND change_kind = 'updated'",
            entity_id,
        )

    target_before, person_before = await _events(target), await _events(person)
    await archive_legacy_legislator_roles(db, execute=True)

    # target gained links, person gained a filer identifier + wa_pdc link —
    # both must surface on the outbox (observation.py convention)
    assert await _events(target) > target_before
    assert await _events(person) > person_before


# ---------------------------------------------------------------------------
# Dry run + idempotency
# ---------------------------------------------------------------------------


async def test_dry_run_mutates_nothing(db, orgs):
    person = await _person(db)
    legacy_role = await _legacy_role(db, orgs["senate"], "Senator, District 5")
    legacy_assign = await _assign(db, person, legacy_role)
    seat = await _seat(db, orgs["senate"], "senate", 5)
    await _assign(db, person, seat, current=True)
    link = await _link(db, legacy_assign, "https://sdc.wastateleg.org/test")
    pdc_row = await _role_pdc(db, legacy_assign, _PDC_URL)

    report = await archive_legacy_legislator_roles(db, execute=False)

    action = _action(report, legacy_assign)
    assert action["status"] == "planned"
    # ancillary accounting appears in the dry-run report too
    assert action["migrated"] == {"links": 1, "pdc": 1}
    assert legacy_role in report["archived_roles"]  # would-be archival is reported
    assert await _archived_at(db, "role_assignments", legacy_assign) is None
    assert await _archived_at(db, "roles", legacy_role) is None
    assert await db.fetchval("SELECT entity_id FROM links WHERE id = $1", link) == legacy_assign
    assert await db.fetchval("SELECT COUNT(*) FROM identifiers WHERE id = $1", pdc_row) == 1


async def test_second_run_is_a_no_op(db, orgs):
    person = await _person(db)
    legacy_role = await _legacy_role(db, orgs["senate"], "Senator, District 5")
    await _assign(db, person, legacy_role)
    seat = await _seat(db, orgs["senate"], "senate", 5)
    await _assign(db, person, seat, current=True)

    first = await archive_legacy_legislator_roles(db, execute=True)
    second = await archive_legacy_legislator_roles(db, execute=True)

    assert [a["status"] for a in first["actions"]] == ["archived"]
    assert second["actions"] == []
    assert second["archived_roles"] == []
