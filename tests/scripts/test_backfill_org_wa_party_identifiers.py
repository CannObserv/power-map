"""Tests for scripts/backfill_org_wa_party_identifiers.py (#270).

Backfills the ``org_wa_party`` identifier onto the two existing WA party Orgs
(matched by canonical name) so a producer's first party observation
AUTO_ATTACHES instead of creating a duplicate. Core logic takes an injected
connection so tests run inside the rolled-back ``db`` transaction (mirrors
tests/scripts/test_seed_roles.py).
"""

import pytest
import pytest_asyncio

from scripts.backfill_org_wa_party_identifiers import backfill_party_identifiers
from src.core.db import generate_id

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


async def _party_org(db, name: str) -> str:
    """Create an org with the given canonical name; return its id."""
    oid = generate_id()
    await db.execute("INSERT INTO organizations (id) VALUES ($1)", oid)
    await db.execute(
        "INSERT INTO organization_names (id, organization_id, name, is_canonical)"
        " VALUES ($1,$2,$3,TRUE)",
        generate_id(),
        oid,
        name,
    )
    return oid


async def _add_canonical_acronym(db, org_id: str, acronym: str) -> None:
    await db.execute(
        "INSERT INTO organization_acronyms (id, organization_id, acronym, is_canonical)"
        " VALUES ($1,$2,$3,TRUE)",
        generate_id(),
        org_id,
        acronym,
    )


async def _party_value(db, org_id: str) -> str | None:
    return await db.fetchval(
        "SELECT i.value FROM identifiers i"
        " JOIN entity_identifier_types t ON t.id = i.entity_identifier_type_id"
        " WHERE t.slug = 'org_wa_party' AND i.entity_id = $1",
        org_id,
    )


async def test_backfill_attaches_party_identifiers_by_canonical_name(db):
    dem = await _party_org(db, "Washington State Democratic Party")
    rep = await _party_org(db, "Washington State Republican Party")

    actions = await backfill_party_identifiers(db, execute=True)

    assert await _party_value(db, dem) == "democratic"
    assert await _party_value(db, rep) == "republican"
    assert {a["value"]: a["status"] for a in actions} == {
        "democratic": "applied",
        "republican": "applied",
    }


async def test_backfill_is_idempotent(db):
    dem = await _party_org(db, "Washington State Democratic Party")
    rep = await _party_org(db, "Washington State Republican Party")

    await backfill_party_identifiers(db, execute=True)
    actions = await backfill_party_identifiers(db, execute=True)

    assert {a["status"] for a in actions} == {"exists"}
    count = await db.fetchval(
        "SELECT count(*) FROM identifiers i"
        " JOIN entity_identifier_types t ON t.id = i.entity_identifier_type_id"
        " WHERE t.slug = 'org_wa_party' AND i.entity_id = ANY($1)",
        [dem, rep],
    )
    assert count == 2


async def test_backfill_dry_run_makes_no_changes(db):
    dem = await _party_org(db, "Washington State Democratic Party")

    actions = await backfill_party_identifiers(db, execute=False)

    assert await _party_value(db, dem) is None
    assert any(a["value"] == "democratic" and a["status"] == "planned" for a in actions)


async def test_backfill_reports_missing_party_org(db):
    """A party name with no matching Org is reported, never created."""
    actions = await backfill_party_identifiers(db, execute=True)

    by_value = {a["value"]: a["status"] for a in actions}
    assert by_value == {"democratic": "missing", "republican": "missing"}


async def test_backfill_matches_acronymed_org_by_canonical_name(db):
    """An Org with a canonical acronym still matches (regression guard).

    v_org_display_names would render this Org as "Washington State Democratic
    Party (WSDP)"; matching on that composed display name would miss it. The
    backfill keys on the canonical name row directly, so the acronym is
    irrelevant and the identifier still attaches.
    """
    dem = await _party_org(db, "Washington State Democratic Party")
    await _add_canonical_acronym(db, dem, "WSDP")

    actions = await backfill_party_identifiers(db, execute=True)

    assert await _party_value(db, dem) == "democratic"
    assert {a["value"]: a["status"] for a in actions}["democratic"] == "applied"
