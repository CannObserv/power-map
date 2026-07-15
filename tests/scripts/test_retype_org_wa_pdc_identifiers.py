"""Tests for scripts/retype_org_wa_pdc_identifiers.py (#296).

The org-side analog of #293/#295: every ``org_wa_pdc`` value in the DB is a
legacy PDC Lobbyist Reporting (accesshub) node URL, not the bare numeric node
ID. This retypes each ``.../node/N`` value to ``N``, preserves the URL as a
``wa_pdc`` org link, and deletes the URL row. Non-node values (campaign-explorer
committee URLs, free-text) are reported and never touched.

Rule-based bulk transform (the node ID is literally the URL path segment) — no
embedded crosswalk. Core logic takes an injected connection so tests run inside
the rolled-back ``db`` transaction.
"""

import pytest
import pytest_asyncio

from scripts.retype_org_wa_pdc_identifiers import extract_node_id, retype_org_wa_pdc
from src.core.db import generate_id


def test_extract_node_id_accepts_accesshub_and_legacy_hosts():
    assert extract_node_id("https://accesshub.pdc.wa.gov/node/17398") == "17398"
    assert extract_node_id("https://www.pdc.wa.gov/legacy-lobbyist/node/67155") == "67155"
    assert extract_node_id("https://accesshub.pdc.wa.gov/node/17398/") == "17398"


def test_extract_node_id_rejects_non_node_values():
    assert extract_node_id("17398") is None  # already numeric
    assert extract_node_id("I-502 Retailer") is None
    assert (
        extract_node_id(
            "https://www.pdc.wa.gov/browse/campaign-explorer/committee?filer_id=VIPEP%20%20102"
        )
        is None
    )


# ---------------------------------------------------------------------------
# retype_org_wa_pdc — integration
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


async def _org_with_key(db, value: str) -> str:
    """Create an org carrying an org_wa_pdc identifier with the value."""
    org_id = generate_id()
    await db.execute("INSERT INTO organizations (id) VALUES ($1)", org_id)
    await db.execute(
        "INSERT INTO identifiers (id, entity_id, entity_identifier_type_id, value)"
        " SELECT $1, $2, t.id, $3 FROM entity_identifier_types t WHERE t.slug = 'org_wa_pdc'",
        generate_id(),
        org_id,
        value,
    )
    return org_id


async def _key_values(db, org_id: str) -> list[str]:
    rows = await db.fetch(
        "SELECT i.value FROM identifiers i JOIN entity_identifier_types t"
        " ON t.id = i.entity_identifier_type_id"
        " WHERE t.slug = 'org_wa_pdc' AND i.entity_id = $1 ORDER BY i.value",
        org_id,
    )
    return [r["value"] for r in rows]


async def _links(db, org_id: str) -> list[str]:
    rows = await db.fetch(
        "SELECT l.url FROM links l JOIN link_types lt ON lt.id = l.link_type_id"
        " WHERE lt.slug = 'wa_pdc' AND l.entity_type = 'organization' AND l.entity_id = $1"
        " ORDER BY l.url",
        org_id,
    )
    return [r["url"] for r in rows]


def _status(actions, org_id: str) -> str:
    return next(a["status"] for a in actions if a["org_id"] == org_id)


async def test_retype_node_url_to_numeric_with_link(db):
    url = "https://accesshub.pdc.wa.gov/node/17398"
    org_id = await _org_with_key(db, url)

    actions = await retype_org_wa_pdc(db, execute=True)

    assert _status(actions, org_id) == "applied"
    assert await _key_values(db, org_id) == ["17398"]
    assert await _links(db, org_id) == [url]


async def test_already_numeric_reports_exists(db):
    org_id = await _org_with_key(db, "17398")

    actions = await retype_org_wa_pdc(db, execute=True)

    assert _status(actions, org_id) == "exists"
    assert await _key_values(db, org_id) == ["17398"]
    assert await _links(db, org_id) == []


async def test_committee_url_is_skipped(db):
    url = "https://www.pdc.wa.gov/browse/campaign-explorer/committee?filer_id=VIPEP%20%20102"
    org_id = await _org_with_key(db, url)

    actions = await retype_org_wa_pdc(db, execute=True)

    assert _status(actions, org_id) == "skipped_committee"
    assert await _key_values(db, org_id) == [url]  # untouched


async def test_freetext_is_skipped(db):
    org_id = await _org_with_key(db, "I-502 Retailer")

    actions = await retype_org_wa_pdc(db, execute=True)

    assert _status(actions, org_id) == "skipped_freetext"
    assert await _key_values(db, org_id) == ["I-502 Retailer"]


async def test_collision_when_numeric_already_on_another_org(db):
    other = await _org_with_key(db, "17398")  # numeric already held here
    dup = await _org_with_key(db, "https://accesshub.pdc.wa.gov/node/17398")

    actions = await retype_org_wa_pdc(db, execute=True)

    assert _status(actions, dup) == "collision"
    # URL row on dup is left in place for manual resolution
    assert await _key_values(db, dup) == ["https://accesshub.pdc.wa.gov/node/17398"]
    assert await _key_values(db, other) == ["17398"]


async def test_dry_run_makes_no_changes(db):
    url = "https://accesshub.pdc.wa.gov/node/17398"
    org_id = await _org_with_key(db, url)

    actions = await retype_org_wa_pdc(db, execute=False)

    assert _status(actions, org_id) == "planned"
    assert await _key_values(db, org_id) == [url]
    assert await _links(db, org_id) == []


async def test_idempotent_second_run_reports_exists(db):
    org_id = await _org_with_key(db, "https://accesshub.pdc.wa.gov/node/17398")

    await retype_org_wa_pdc(db, execute=True)
    actions = await retype_org_wa_pdc(db, execute=True)

    assert _status(actions, org_id) == "exists"
    assert await _key_values(db, org_id) == ["17398"]
    assert await _links(db, org_id) == ["https://accesshub.pdc.wa.gov/node/17398"]
