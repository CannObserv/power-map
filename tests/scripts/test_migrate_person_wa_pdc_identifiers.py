"""Tests for scripts/migrate_person_wa_pdc_identifiers.py (#293).

Migrates the 12 legacy URL-form ``person_wa_pdc`` values to the numeric PDC
``person_id`` convention, preserving each URL's ``filer_id`` under the new
``person_wa_pdc_filer`` identifier type. Core logic takes an injected
connection so tests run inside the rolled-back ``db`` transaction (mirrors
tests/scripts/test_backfill_org_wa_party_identifiers.py).
"""

import pytest
import pytest_asyncio

from scripts.migrate_person_wa_pdc_identifiers import (
    MIGRATIONS,
    extract_filer_ids,
    migrate_pdc_identifiers,
)
from src.core.db import generate_id

RAMEL_PM_ID = "01KV6PQKAP9K6VZE80RDMCKB25"
RAMEL_URL = (
    "https://www.pdc.wa.gov/browse/campaign-explorer/candidate"
    "?filer_id=RAMEA%20%20109&election_year=2020"
)
PETERSON_PM_ID = "01KV6PQZEAPR4PS3VNPRWF6N5S"
PETERSON_URL_2X = (
    "https://www.pdc.wa.gov/browse/campaign-explorer/candidate"
    "?filer_id=PETES%20%20026&election_year=2018   "
    "https://www.pdc.wa.gov/browse/campaign-explorer/candidate"
    "?filer_id=PETES%20%20026&election_year=2020"
)
HOVER_PM_ID = "01KV6PQKMA50ZMHKYY3R7YJ455"


# ---------------------------------------------------------------------------
# extract_filer_ids — pure URL parsing, no DB
# ---------------------------------------------------------------------------


def test_extract_filer_ids_single_url_decodes_padding():
    assert extract_filer_ids(RAMEL_URL) == ["RAMEA  109"]


def test_extract_filer_ids_dedupes_across_multiple_urls():
    """Strom Peterson's value holds two URLs with the same filer_id."""
    assert extract_filer_ids(PETERSON_URL_2X) == ["PETES  026"]


def test_extract_filer_ids_preserves_distinct_filers():
    two = RAMEL_URL + " " + RAMEL_URL.replace("RAMEA%20%20109", "DAVIL%20%20109")
    assert extract_filer_ids(two) == ["RAMEA  109", "DAVIL  109"]


def test_extract_filer_ids_non_url_value_yields_nothing():
    assert extract_filer_ids("30420") == []


def test_extract_filer_ids_url_without_filer_id_yields_nothing():
    assert extract_filer_ids("https://www.pdc.wa.gov/browse?election_year=2020") == []


def test_extract_filer_ids_semicolon_joined_urls():
    """Andy Hover's batch-2 value: two URLs joined by ' ; '."""
    value = (
        "https://www.pdc.wa.gov/browse/campaign-explorer/candidate"
        "?filer_id=HOVEA%20%20862&election_year=2016 ; "
        "https://www.pdc.wa.gov/browse/campaign-explorer/candidate"
        "?filer_id=HOVEA%20%20862&election_year=2020"
    )
    assert extract_filer_ids(value) == ["HOVEA  862"]


def test_extract_filer_ids_plus_encoded_padding():
    """Brad Klippert's batch-2 value pads the filer_id with '+' instead of %20."""
    value = (
        "https://www.pdc.wa.gov/browse/campaign-explorer/candidate"
        "?filer_id=KLIPB++336&election_year=2018"
    )
    assert extract_filer_ids(value) == ["KLIPB  336"]


def test_extract_filer_ids_contributions_download_url():
    """Sam Hunt's batch-2 value is a reports/contributions_download link."""
    value = (
        "https://www.pdc.wa.gov/reports/contributions_download"
        "?filer_id=HUNTS%20%20506&election_year=2020"
    )
    assert extract_filer_ids(value) == ["HUNTS  506"]


def test_migrations_table_covers_the_31_people():
    """12 from the #293 issue table + 19 from the batch-2 audit comment.

    Michelle Caldier (01KV6PQVP1CG9GKQ4VSB3S5C64) is deliberately absent:
    her target numeric value already sits on a different PM person (a
    duplicate-person pair needing the merge workflow first).
    """
    assert len(MIGRATIONS) == 31
    assert {m["person_id"] for m in MIGRATIONS} >= {RAMEL_PM_ID, PETERSON_PM_ID, HOVER_PM_ID}
    assert "01KV6PQVP1CG9GKQ4VSB3S5C64" not in {m["person_id"] for m in MIGRATIONS}


# ---------------------------------------------------------------------------
# migrate_pdc_identifiers — integration
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


async def _person_with_pdc(db, person_id: str, value: str) -> None:
    """Create a person carrying a person_wa_pdc identifier with the value."""
    await db.execute("INSERT INTO people (id) VALUES ($1)", person_id)
    await db.execute(
        "INSERT INTO identifiers (id, entity_id, entity_identifier_type_id, value)"
        " SELECT $1, $2, t.id, $3 FROM entity_identifier_types t"
        " WHERE t.slug = 'person_wa_pdc'",
        generate_id(),
        person_id,
        value,
    )


async def _values(db, person_id: str, slug: str) -> list[str]:
    rows = await db.fetch(
        "SELECT i.value FROM identifiers i"
        " JOIN entity_identifier_types t ON t.id = i.entity_identifier_type_id"
        " WHERE t.slug = $1 AND i.entity_id = $2 ORDER BY i.value",
        slug,
        person_id,
    )
    return [r["value"] for r in rows]


def _status(actions, person_id: str) -> str:
    return next(a["status"] for a in actions if a["person_id"] == person_id)


async def test_migrate_replaces_url_with_numeric_and_preserves_filer(db):
    await _person_with_pdc(db, RAMEL_PM_ID, RAMEL_URL)

    actions = await migrate_pdc_identifiers(db, execute=True)

    assert await _values(db, RAMEL_PM_ID, "person_wa_pdc") == ["30420"]
    assert await _values(db, RAMEL_PM_ID, "person_wa_pdc_filer") == ["RAMEA  109"]
    assert _status(actions, RAMEL_PM_ID) == "applied"


async def test_migrate_collapses_double_url_to_one_filer(db):
    """Strom Peterson: two URLs in one value → one filer identifier."""
    await _person_with_pdc(db, PETERSON_PM_ID, PETERSON_URL_2X)

    actions = await migrate_pdc_identifiers(db, execute=True)

    assert await _values(db, PETERSON_PM_ID, "person_wa_pdc") == ["159"]
    assert await _values(db, PETERSON_PM_ID, "person_wa_pdc_filer") == ["PETES  026"]
    assert _status(actions, PETERSON_PM_ID) == "applied"


async def test_migrate_is_idempotent(db):
    await _person_with_pdc(db, RAMEL_PM_ID, RAMEL_URL)

    await migrate_pdc_identifiers(db, execute=True)
    actions = await migrate_pdc_identifiers(db, execute=True)

    assert _status(actions, RAMEL_PM_ID) == "exists"
    assert await _values(db, RAMEL_PM_ID, "person_wa_pdc") == ["30420"]
    assert await _values(db, RAMEL_PM_ID, "person_wa_pdc_filer") == ["RAMEA  109"]


async def test_migrate_dry_run_makes_no_changes(db):
    await _person_with_pdc(db, RAMEL_PM_ID, RAMEL_URL)

    actions = await migrate_pdc_identifiers(db, execute=False)

    assert await _values(db, RAMEL_PM_ID, "person_wa_pdc") == [RAMEL_URL]
    assert await _values(db, RAMEL_PM_ID, "person_wa_pdc_filer") == []
    assert _status(actions, RAMEL_PM_ID) == "planned"


async def test_migrate_skips_unexpected_filer_id(db):
    """A URL whose filer_id doesn't match the issue table is never touched."""
    wrong = RAMEL_URL.replace("RAMEA%20%20109", "OTHER%20%20001")
    await _person_with_pdc(db, RAMEL_PM_ID, wrong)

    actions = await migrate_pdc_identifiers(db, execute=True)

    assert await _values(db, RAMEL_PM_ID, "person_wa_pdc") == [wrong]
    assert await _values(db, RAMEL_PM_ID, "person_wa_pdc_filer") == []
    assert _status(actions, RAMEL_PM_ID) == "conflict"


async def test_migrate_skips_unexpected_non_url_value(db):
    """A non-URL, non-target value (e.g. a stray numeric) is a conflict."""
    await _person_with_pdc(db, RAMEL_PM_ID, "99999")

    actions = await migrate_pdc_identifiers(db, execute=True)

    assert await _values(db, RAMEL_PM_ID, "person_wa_pdc") == ["99999"]
    assert _status(actions, RAMEL_PM_ID) == "conflict"


async def test_migrate_reports_missing_person(db):
    """A person with no person_wa_pdc identifier is reported, never created."""
    actions = await migrate_pdc_identifiers(db, execute=True)

    assert {a["status"] for a in actions} == {"missing"}
    assert len(actions) == 31


async def test_migrate_skips_when_numeric_value_on_another_person(db):
    """Target numeric already on a different person (duplicate pair) → collision.

    Guard for the Michelle Caldier case: migrating would put the same
    identifier value on two people, breaking value-based resolution.
    """
    await _person_with_pdc(db, RAMEL_PM_ID, RAMEL_URL)
    other = generate_id()
    await _person_with_pdc(db, other, "30420")

    actions = await migrate_pdc_identifiers(db, execute=True)

    assert _status(actions, RAMEL_PM_ID) == "collision"
    assert await _values(db, RAMEL_PM_ID, "person_wa_pdc") == [RAMEL_URL]
    assert await _values(db, RAMEL_PM_ID, "person_wa_pdc_filer") == []


async def test_migrate_skips_ambiguous_multiple_rows(db):
    await _person_with_pdc(db, RAMEL_PM_ID, RAMEL_URL)
    await db.execute(
        "INSERT INTO identifiers (id, entity_id, entity_identifier_type_id, value)"
        " SELECT $1, $2, t.id, $3 FROM entity_identifier_types t"
        " WHERE t.slug = 'person_wa_pdc'",
        generate_id(),
        RAMEL_PM_ID,
        RAMEL_URL,
    )

    actions = await migrate_pdc_identifiers(db, execute=True)

    assert _status(actions, RAMEL_PM_ID) == "ambiguous"
    assert await _values(db, RAMEL_PM_ID, "person_wa_pdc_filer") == []
