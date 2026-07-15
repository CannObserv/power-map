"""Tests for scripts/cleanup_org_wa_pdc_remnants.py (#296).

The retype (#296) left 7 non-node org_wa_pdc values. This resolves them:

- 3 campaign-finance committee URLs -> retype to the bare committee filer_id
  under the new ``org_wa_pdc_committee`` type, preserve the URL as a ``wa_pdc``
  link, delete the org_wa_pdc row.
- 4 ``I-502 …`` WSLCB license-type strings (mis-entered — not PDC data) -> move
  the license type to the org's ``notes`` and delete the org_wa_pdc row.

Core logic takes an injected connection so tests run inside the rolled-back
``db`` transaction.
"""

import pytest
import pytest_asyncio

from scripts.cleanup_org_wa_pdc_remnants import REMNANTS, cleanup_org_wa_pdc_remnants
from src.core.db import generate_id

# One committee (URL -> org_wa_pdc_committee) and one I-502 (note) entry.
LAB_ORG = "01KV6PPCFBDCV42CE8HCSQYEXY"
LAB_URL = "https://www.pdc.wa.gov/browse/campaign-explorer/committee?filer_id=LABORG%20503&election_year=2018"
LAB_FILER = "LABORG 503"

SATORI_ORG = "01KV6PPY2W7TJ1Q14B93YDMSZC"
SATORI_VALUE = "I-502 Retailer"
SATORI_NOTE = "WSLCB I-502 license type: Retailer"


def test_remnants_table_is_well_formed():
    assert len(REMNANTS) == 7
    org_ids = [r["org_id"] for r in REMNANTS]
    assert len(set(org_ids)) == len(org_ids)
    by = {"committee": 0, "i502_note": 0}
    for r in REMNANTS:
        by[r["treatment"]] += 1
        if r["treatment"] == "committee":
            assert r["committee_filer_id"] and r["note"] is None
        else:
            assert r["note"] and r["committee_filer_id"] is None
    assert by == {"committee": 3, "i502_note": 4}


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


async def _org_with_pdc(db, org_id: str, value: str, notes: str = "") -> None:
    await db.execute("INSERT INTO organizations (id, notes) VALUES ($1, $2)", org_id, notes)
    await db.execute(
        "INSERT INTO identifiers (id, entity_id, entity_identifier_type_id, value)"
        " SELECT $1, $2, t.id, $3 FROM entity_identifier_types t WHERE t.slug = 'org_wa_pdc'",
        generate_id(),
        org_id,
        value,
    )


async def _values(db, org_id: str, slug: str) -> list[str]:
    rows = await db.fetch(
        "SELECT i.value FROM identifiers i JOIN entity_identifier_types t"
        " ON t.id = i.entity_identifier_type_id"
        " WHERE t.slug = $1 AND i.entity_id = $2 ORDER BY i.value",
        slug,
        org_id,
    )
    return [r["value"] for r in rows]


async def _links(db, org_id: str) -> list[str]:
    rows = await db.fetch(
        "SELECT l.url FROM links l JOIN link_types lt ON lt.id = l.link_type_id"
        " WHERE lt.slug = 'wa_pdc' AND l.entity_type = 'organization' AND l.entity_id = $1",
        org_id,
    )
    return [r["url"] for r in rows]


async def _notes(db, org_id: str) -> str:
    return await db.fetchval("SELECT notes FROM organizations WHERE id = $1", org_id)


def _status(actions, org_id: str) -> str:
    return next(a["status"] for a in actions if a["org_id"] == org_id)


async def test_committee_retype(db):
    await _org_with_pdc(db, LAB_ORG, LAB_URL)

    actions = await cleanup_org_wa_pdc_remnants(db, execute=True)

    assert _status(actions, LAB_ORG) == "applied"
    assert await _values(db, LAB_ORG, "org_wa_pdc") == []
    assert await _values(db, LAB_ORG, "org_wa_pdc_committee") == [LAB_FILER]
    assert await _links(db, LAB_ORG) == [LAB_URL]


async def test_i502_moves_type_to_notes_preserving_existing(db):
    await _org_with_pdc(db, SATORI_ORG, SATORI_VALUE, notes="Dockside co-owner detail")

    actions = await cleanup_org_wa_pdc_remnants(db, execute=True)

    assert _status(actions, SATORI_ORG) == "applied"
    assert await _values(db, SATORI_ORG, "org_wa_pdc") == []
    notes = await _notes(db, SATORI_ORG)
    assert "Dockside co-owner detail" in notes
    assert SATORI_NOTE in notes


async def test_i502_sets_notes_when_empty(db):
    await _org_with_pdc(db, SATORI_ORG, SATORI_VALUE, notes="")

    await cleanup_org_wa_pdc_remnants(db, execute=True)

    assert await _notes(db, SATORI_ORG) == SATORI_NOTE


async def test_dry_run_makes_no_changes(db):
    await _org_with_pdc(db, LAB_ORG, LAB_URL)
    await _org_with_pdc(db, SATORI_ORG, SATORI_VALUE)

    actions = await cleanup_org_wa_pdc_remnants(db, execute=False)

    assert _status(actions, LAB_ORG) == "planned"
    assert _status(actions, SATORI_ORG) == "planned"
    assert await _values(db, LAB_ORG, "org_wa_pdc") == [LAB_URL]
    assert await _values(db, LAB_ORG, "org_wa_pdc_committee") == []
    assert await _notes(db, SATORI_ORG) == ""


async def test_idempotent_second_run_reports_exists(db):
    await _org_with_pdc(db, LAB_ORG, LAB_URL)
    await _org_with_pdc(db, SATORI_ORG, SATORI_VALUE, notes="keep me")

    await cleanup_org_wa_pdc_remnants(db, execute=True)
    actions = await cleanup_org_wa_pdc_remnants(db, execute=True)

    assert _status(actions, LAB_ORG) == "exists"
    assert _status(actions, SATORI_ORG) == "exists"
    assert await _values(db, LAB_ORG, "org_wa_pdc_committee") == [LAB_FILER]
    notes = await _notes(db, SATORI_ORG)
    assert notes.count(SATORI_NOTE) == 1  # not appended twice
    assert "keep me" in notes


async def test_unexpected_value_is_a_conflict(db):
    await _org_with_pdc(db, LAB_ORG, "https://accesshub.pdc.wa.gov/node/99999")

    actions = await cleanup_org_wa_pdc_remnants(db, execute=True)

    assert _status(actions, LAB_ORG) == "conflict"
    assert await _values(db, LAB_ORG, "org_wa_pdc") == ["https://accesshub.pdc.wa.gov/node/99999"]


async def test_missing_org_is_reported(db):
    actions = await cleanup_org_wa_pdc_remnants(db, execute=True)

    assert {a["status"] for a in actions} == {"missing"}
    assert len(actions) == 7
