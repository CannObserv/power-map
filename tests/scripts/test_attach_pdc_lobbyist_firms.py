"""Tests for scripts/attach_pdc_lobbyist_firms.py (#296).

Promotes the verified PDC lobbyist-firm ``filer_id`` keys (established by the
#295 audit) to org grain: find-or-create the firm Organization, attach
``org_wa_pdc`` = ``filer_id``, and add a person->firm affiliation (a plain
"Lobbyist" role + a ``role_assignment`` temporally bounded by the firm's
``bp5b-jrti`` employment years).

Core logic takes an injected connection so tests run inside the rolled-back
``db`` transaction (mirrors tests/scripts/test_migrate_person_wa_pdc_accesshub.py).
"""

from datetime import date

import pytest
import pytest_asyncio

from scripts.attach_pdc_lobbyist_firms import (
    FIRMS,
    LOBBYIST_ROLE_TITLE,
    attach_lobbyist_firms,
)
from src.core.db import generate_id

# A single-member, single-agent firm.
BOSWELL_FILER = "17398"
BOSWELL_ORG_NAME = "Boswell Consulting"
BOSWELL_PERSON = "01KV6PQMD3M2GQMBH9QBG7NMYP"
BOSWELL_AGENT = "110"

# A multi-member firm — both people affiliate to the SAME org.
GTH_FILER = "17581"
MURRAY_PERSON = "01KV6PQMH78YMSCWE7EFPNMJ5D"
MURRAY_AGENT = "387"
CARLEN_PERSON = "01KV6PQP9Y1Z5BS10J5QEBG5KG"
CARLEN_AGENT = "390"

# Org-only firm (Jack Goldberg has no modern agent_id).
STRATEGIES360_FILER = "17996"
GOLDBERG_PERSON = "01KV6PQQHQGFBFKF8P9NV1AYR8"


# ---------------------------------------------------------------------------
# FIRMS table — structural (no DB)
# ---------------------------------------------------------------------------


def test_firms_table_is_well_formed():
    """21 Tier-A firms; filer_ids unique; years sane; members reference agents."""
    assert len(FIRMS) == 21
    filer_ids = [f["filer_id"] for f in FIRMS]
    assert len(set(filer_ids)) == len(filer_ids), "duplicate filer_id"
    for f in FIRMS:
        assert f["filer_id"].isdigit()
        assert f["name"].strip() == f["name"] and f["name"]
        for m in f["members"]:
            assert m["person_id"].startswith("01")
            # A member either carries agent_ids AND a year window, or neither
            # (org-only, e.g. Goldberg at Strategies 360).
            if m["agent_ids"]:
                assert 2016 <= m["year_min"] <= m["year_max"] <= 2026
                assert all(a.isdigit() for a in m["agent_ids"])
            else:
                assert m["year_min"] is None and m["year_max"] is None


def test_goldberg_is_the_only_org_only_member():
    org_only = [
        (f["filer_id"], m["person_id"]) for f in FIRMS for m in f["members"] if not m["agent_ids"]
    ]
    assert org_only == [(STRATEGIES360_FILER, GOLDBERG_PERSON)]


def test_j_dylan_doty_affiliates_to_two_firms():
    """The same person (agent 266 / 429) belongs to two distinct firms."""
    doty = "01KV6PQQHGTVCNSTV8NHWNAJNB"
    firms = [f["filer_id"] for f in FIRMS if any(m["person_id"] == doty for m in f["members"])]
    assert sorted(firms) == ["17496", "17589"]


# ---------------------------------------------------------------------------
# attach_lobbyist_firms — integration
# ---------------------------------------------------------------------------

pytestmark = pytest.mark.integration


@pytest_asyncio.fixture(loop_scope="session")
async def db(db_pool):
    async with db_pool.acquire() as conn:
        tr = conn.transaction()
        await tr.start()
        try:
            yield conn
        finally:
            await tr.rollback()


async def _seed_member(db, person_id: str, agent_id: str | None) -> None:
    """Create the person and (optionally) its #295 lobbyist-agent identifier."""
    await db.execute("INSERT INTO people (id) VALUES ($1)", person_id)
    if agent_id is not None:
        await db.execute(
            "INSERT INTO identifiers (id, entity_id, entity_identifier_type_id, value)"
            " SELECT $1, $2, t.id, $3 FROM entity_identifier_types t"
            " WHERE t.slug = 'person_wa_pdc_lobbyist_agent'",
            generate_id(),
            person_id,
            agent_id,
        )


async def _org_by_key(db, filer_id: str) -> str | None:
    return await db.fetchval(
        "SELECT i.entity_id FROM identifiers i"
        " JOIN entity_identifier_types t ON t.id = i.entity_identifier_type_id"
        " WHERE t.slug = 'org_wa_pdc' AND i.value = $1",
        filer_id,
    )


async def _org_name(db, org_id: str) -> str | None:
    return await db.fetchval(
        "SELECT display_name FROM v_org_display_names WHERE organization_id = $1", org_id
    )


async def _assignments(db, person_id: str) -> list[dict]:
    rows = await db.fetch(
        "SELECT ra.start_date, ra.end_date, ra.is_current, r.title, r.organization_id"
        " FROM role_assignments ra JOIN roles r ON r.id = ra.role_id"
        " WHERE ra.person_id = $1 AND ra.archived_at IS NULL"
        " ORDER BY r.organization_id",
        person_id,
    )
    return [dict(r) for r in rows]


def _org_status(result, filer_id: str) -> str:
    return next(f["org_status"] for f in result if f["filer_id"] == filer_id)


def _member_status(result, filer_id: str, person_id: str) -> str:
    firm = next(f for f in result if f["filer_id"] == filer_id)
    return next(m["status"] for m in firm["members"] if m["person_id"] == person_id)


async def test_creates_org_key_role_and_assignment(db):
    await _seed_member(db, BOSWELL_PERSON, BOSWELL_AGENT)

    result = await attach_lobbyist_firms(db, execute=True)

    assert _org_status(result, BOSWELL_FILER) == "created"
    assert _member_status(result, BOSWELL_FILER, BOSWELL_PERSON) == "applied"

    org_id = await _org_by_key(db, BOSWELL_FILER)
    assert org_id is not None
    assert await _org_name(db, org_id) == BOSWELL_ORG_NAME

    assigns = await _assignments(db, BOSWELL_PERSON)
    assert len(assigns) == 1
    a = assigns[0]
    assert a["organization_id"] == org_id
    assert a["title"] == LOBBYIST_ROLE_TITLE
    assert a["start_date"] == date(2016, 1, 1)
    assert a["end_date"] == date(2026, 12, 31)
    assert a["is_current"] is False


async def test_multi_member_firm_shares_one_org(db):
    await _seed_member(db, MURRAY_PERSON, MURRAY_AGENT)
    await _seed_member(db, CARLEN_PERSON, CARLEN_AGENT)

    result = await attach_lobbyist_firms(db, execute=True)

    assert _org_status(result, GTH_FILER) == "created"
    assert _member_status(result, GTH_FILER, MURRAY_PERSON) == "applied"
    assert _member_status(result, GTH_FILER, CARLEN_PERSON) == "applied"

    org_id = await _org_by_key(db, GTH_FILER)
    murray = await _assignments(db, MURRAY_PERSON)
    carlen = await _assignments(db, CARLEN_PERSON)
    assert murray[0]["organization_id"] == org_id
    assert carlen[0]["organization_id"] == org_id
    # exactly one shared "Lobbyist" role at the org
    role_ids = await db.fetch(
        "SELECT id FROM roles WHERE organization_id = $1 AND lower(title) = lower($2)"
        " AND archived_at IS NULL",
        org_id,
        LOBBYIST_ROLE_TITLE,
    )
    assert len(role_ids) == 1


async def test_org_only_member_creates_org_without_assignment(db):
    await _seed_member(db, GOLDBERG_PERSON, None)

    result = await attach_lobbyist_firms(db, execute=True)

    assert _org_status(result, STRATEGIES360_FILER) == "created"
    assert _member_status(result, STRATEGIES360_FILER, GOLDBERG_PERSON) == "no_agent"
    assert await _org_by_key(db, STRATEGIES360_FILER) is not None
    assert await _assignments(db, GOLDBERG_PERSON) == []


async def test_idempotent_second_run_reports_exists(db):
    await _seed_member(db, BOSWELL_PERSON, BOSWELL_AGENT)

    await attach_lobbyist_firms(db, execute=True)
    result = await attach_lobbyist_firms(db, execute=True)

    assert _org_status(result, BOSWELL_FILER) == "exists"
    assert _member_status(result, BOSWELL_FILER, BOSWELL_PERSON) == "exists"
    assert len(await _assignments(db, BOSWELL_PERSON)) == 1
    # org key not duplicated
    keys = await db.fetch(
        "SELECT 1 FROM identifiers i JOIN entity_identifier_types t"
        " ON t.id = i.entity_identifier_type_id"
        " WHERE t.slug = 'org_wa_pdc' AND i.value = $1",
        BOSWELL_FILER,
    )
    assert len(keys) == 1


async def test_dry_run_makes_no_changes(db):
    await _seed_member(db, BOSWELL_PERSON, BOSWELL_AGENT)

    result = await attach_lobbyist_firms(db, execute=False)

    assert _org_status(result, BOSWELL_FILER) == "planned"
    assert _member_status(result, BOSWELL_FILER, BOSWELL_PERSON) == "planned"
    assert await _org_by_key(db, BOSWELL_FILER) is None
    assert await _assignments(db, BOSWELL_PERSON) == []


async def _make_named_org(db, name: str, key: str | None = None) -> str:
    org_id = generate_id()
    await db.execute("INSERT INTO organizations (id) VALUES ($1)", org_id)
    await db.execute(
        "INSERT INTO organization_names (id, organization_id, name, name_type, is_canonical)"
        " VALUES ($1, $2, $3, 'legal', TRUE)",
        generate_id(),
        org_id,
        name,
    )
    if key is not None:
        await db.execute(
            "INSERT INTO identifiers (id, entity_id, entity_identifier_type_id, value)"
            " SELECT $1, $2, t.id, $3 FROM entity_identifier_types t WHERE t.slug = 'org_wa_pdc'",
            generate_id(),
            org_id,
            key,
        )
    return org_id


async def test_adopts_existing_same_named_keyless_org(db):
    """A same-named org with no org_wa_pdc is adopted — key stamped, affiliated."""
    existing = await _make_named_org(db, BOSWELL_ORG_NAME)
    await _seed_member(db, BOSWELL_PERSON, BOSWELL_AGENT)

    result = await attach_lobbyist_firms(db, execute=True)

    assert _org_status(result, BOSWELL_FILER) == "adopted"
    assert _member_status(result, BOSWELL_FILER, BOSWELL_PERSON) == "applied"
    assert await _org_by_key(db, BOSWELL_FILER) == existing
    assert (await _assignments(db, BOSWELL_PERSON))[0]["organization_id"] == existing


async def test_same_named_org_with_different_key_is_a_conflict(db):
    """A same-named org already carrying a different org_wa_pdc is never touched."""
    await _make_named_org(db, BOSWELL_ORG_NAME, key="99999")
    await _seed_member(db, BOSWELL_PERSON, BOSWELL_AGENT)

    result = await attach_lobbyist_firms(db, execute=True)

    assert _org_status(result, BOSWELL_FILER) == "name_conflict"
    assert _member_status(result, BOSWELL_FILER, BOSWELL_PERSON) == "skipped_org"
    assert await _org_by_key(db, BOSWELL_FILER) is None
    assert await _assignments(db, BOSWELL_PERSON) == []


async def test_reuses_org_matched_by_legacy_node_url(db):
    """A firm keyed by the pre-retype accesshub node URL is reused, not duplicated."""
    existing = await _make_named_org(
        db, "Boswell Consulting, LLC", key=f"https://accesshub.pdc.wa.gov/node/{BOSWELL_FILER}"
    )
    await _seed_member(db, BOSWELL_PERSON, BOSWELL_AGENT)

    result = await attach_lobbyist_firms(db, execute=True)

    assert _org_status(result, BOSWELL_FILER) == "exists"
    assert _member_status(result, BOSWELL_FILER, BOSWELL_PERSON) == "applied"
    assert (await _assignments(db, BOSWELL_PERSON))[0]["organization_id"] == existing


async def test_reuses_org_matched_by_key(db):
    """A pre-existing org already carrying the filer_id key is reused, not duplicated."""
    existing = generate_id()
    await db.execute("INSERT INTO organizations (id) VALUES ($1)", existing)
    await db.execute(
        "INSERT INTO organization_names (id, organization_id, name, name_type, is_canonical)"
        " VALUES ($1, $2, 'Renamed Firm LLC', 'legal', TRUE)",
        generate_id(),
        existing,
    )
    await db.execute(
        "INSERT INTO identifiers (id, entity_id, entity_identifier_type_id, value)"
        " SELECT $1, $2, t.id, $3 FROM entity_identifier_types t WHERE t.slug = 'org_wa_pdc'",
        generate_id(),
        existing,
        BOSWELL_FILER,
    )
    await _seed_member(db, BOSWELL_PERSON, BOSWELL_AGENT)

    result = await attach_lobbyist_firms(db, execute=True)

    assert _org_status(result, BOSWELL_FILER) == "exists"
    assert _member_status(result, BOSWELL_FILER, BOSWELL_PERSON) == "applied"
    assigns = await _assignments(db, BOSWELL_PERSON)
    assert assigns[0]["organization_id"] == existing


async def test_missing_person_is_reported(db):
    """Org is still created; the member with no person row is reported, not created."""
    result = await attach_lobbyist_firms(db, execute=True)

    assert _org_status(result, BOSWELL_FILER) == "created"
    assert _member_status(result, BOSWELL_FILER, BOSWELL_PERSON) == "person_missing"
    assert await _assignments(db, BOSWELL_PERSON) == []


async def test_person_without_agent_identifier_is_skipped(db):
    """Person exists but lacks the #295 lobbyist-agent identifier → skip affiliation."""
    await _seed_member(db, BOSWELL_PERSON, None)  # no agent identifier

    result = await attach_lobbyist_firms(db, execute=True)

    assert _org_status(result, BOSWELL_FILER) == "created"
    assert _member_status(result, BOSWELL_FILER, BOSWELL_PERSON) == "agent_missing"
    assert await _assignments(db, BOSWELL_PERSON) == []
