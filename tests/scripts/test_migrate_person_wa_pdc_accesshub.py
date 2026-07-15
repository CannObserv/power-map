"""Tests for scripts/migrate_person_wa_pdc_accesshub.py (#295).

Retypes the 60 legacy accesshub (lobbyist) URL-form ``person_wa_pdc`` values:
the person-stable ``agent_id`` from PDC's Lobbyist Agents dataset is minted
under ``person_wa_pdc_lobbyist_agent``, the raw URL is preserved as a
``wa_pdc`` link, and the URL-form identifier row is deleted. Core logic takes
an injected connection so tests run inside the rolled-back ``db`` transaction
(mirrors tests/scripts/test_migrate_person_wa_pdc_identifiers.py).
"""

import pytest
import pytest_asyncio

from scripts.migrate_person_wa_pdc_accesshub import (
    RETYPES,
    migrate_accesshub_identifiers,
)
from src.core.db import generate_id

# Single verified agent_id.
CHRISTOPHERSEN_PM_ID = "01KV6PR0H8TEKREAK1ZS82DT0C"
CHRISTOPHERSEN_URL = "https://accesshub.pdc.wa.gov/node/17348"

# Multiple agent_ids (PDC duplicate directory rows for name variants).
OSULLIVAN_PM_ID = "01KV6PQYFYW9PB69SDBY2D61JQ"
OSULLIVAN_URL = "https://accesshub.pdc.wa.gov/node/17873"

# No modern key exists — link-only treatment.
COLMAN_PM_ID = "01KV6PR0HJCAJPWJQ63FC1YT7Q"
COLMAN_URL = "https://accesshub.pdc.wa.gov/node/46208"

# Deferred: PM duplicate pair (merge first) and ambiguous agent match.
DOTY_PM_ID = "01KV6PQPJTEBKM13599W3E3CC1"
DOTY_URL = "https://accesshub.pdc.wa.gov/node/17496"
J_DOTY_PM_ID = "01KV6PQQHGTVCNSTV8NHWNAJNB"
MORAN_PM_ID = "01KV6PQVRNHXC0H5B4W36TESY2"


def test_retypes_table_covers_the_60_people():
    """55 retype + 2 link_only (Colman, Goldberg) + 3 deferred (Doty pair, Moran)."""
    assert len(RETYPES) == 60
    by_treatment: dict[str, int] = {}
    for r in RETYPES:
        by_treatment[r["treatment"]] = by_treatment.get(r["treatment"], 0) + 1
    assert by_treatment == {"retype": 55, "link_only": 2, "deferred": 3}
    deferred = {r["person_id"] for r in RETYPES if r["treatment"] == "deferred"}
    assert deferred == {DOTY_PM_ID, J_DOTY_PM_ID, MORAN_PM_ID}
    link_only = {r["person_id"] for r in RETYPES if r["treatment"] == "link_only"}
    assert COLMAN_PM_ID in link_only
    # retype rows carry at least one agent_id; link_only/deferred mint nothing
    assert all(r["agent_ids"] for r in RETYPES if r["treatment"] == "retype")
    assert all(not r["agent_ids"] for r in RETYPES if r["treatment"] != "retype")


# ---------------------------------------------------------------------------
# migrate_accesshub_identifiers — integration
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


async def _links(db, person_id: str) -> list[str]:
    rows = await db.fetch(
        "SELECT l.url FROM links l"
        " JOIN link_types lt ON lt.id = l.link_type_id"
        " WHERE lt.slug = 'wa_pdc' AND l.entity_type = 'person' AND l.entity_id = $1"
        " ORDER BY l.url",
        person_id,
    )
    return [r["url"] for r in rows]


def _status(actions, person_id: str) -> str:
    return next(a["status"] for a in actions if a["person_id"] == person_id)


async def test_retype_mints_agents_link_and_deletes_url_row(db):
    await _person_with_pdc(db, CHRISTOPHERSEN_PM_ID, CHRISTOPHERSEN_URL)

    actions = await migrate_accesshub_identifiers(db, execute=True)

    assert _status(actions, CHRISTOPHERSEN_PM_ID) == "applied"
    assert await _values(db, CHRISTOPHERSEN_PM_ID, "person_wa_pdc") == []
    assert await _values(db, CHRISTOPHERSEN_PM_ID, "person_wa_pdc_lobbyist_agent") == ["33"]
    assert await _links(db, CHRISTOPHERSEN_PM_ID) == [CHRISTOPHERSEN_URL]


async def test_retype_mints_all_duplicate_agent_ids(db):
    """Sean O'Sullivan: PDC carries three duplicate agent rows — all minted."""
    await _person_with_pdc(db, OSULLIVAN_PM_ID, OSULLIVAN_URL)

    actions = await migrate_accesshub_identifiers(db, execute=True)

    assert _status(actions, OSULLIVAN_PM_ID) == "applied"
    assert await _values(db, OSULLIVAN_PM_ID, "person_wa_pdc_lobbyist_agent") == [
        "832",
        "833",
        "834",
    ]


async def test_link_only_preserves_url_without_minting(db):
    """Vic Colman has no modern PDC key — URL becomes a link, nothing minted."""
    await _person_with_pdc(db, COLMAN_PM_ID, COLMAN_URL)

    actions = await migrate_accesshub_identifiers(db, execute=True)

    assert _status(actions, COLMAN_PM_ID) == "applied"
    assert await _values(db, COLMAN_PM_ID, "person_wa_pdc") == []
    assert await _values(db, COLMAN_PM_ID, "person_wa_pdc_lobbyist_agent") == []
    assert await _links(db, COLMAN_PM_ID) == [COLMAN_URL]


async def test_deferred_person_is_never_touched(db):
    """Dylan Doty (duplicate-person pair, merge first) stays exactly as-is."""
    await _person_with_pdc(db, DOTY_PM_ID, DOTY_URL)

    actions = await migrate_accesshub_identifiers(db, execute=True)

    assert _status(actions, DOTY_PM_ID) == "deferred"
    assert await _values(db, DOTY_PM_ID, "person_wa_pdc") == [DOTY_URL]
    assert await _values(db, DOTY_PM_ID, "person_wa_pdc_lobbyist_agent") == []
    assert await _links(db, DOTY_PM_ID) == []


async def test_dry_run_makes_no_changes(db):
    await _person_with_pdc(db, CHRISTOPHERSEN_PM_ID, CHRISTOPHERSEN_URL)

    actions = await migrate_accesshub_identifiers(db, execute=False)

    assert _status(actions, CHRISTOPHERSEN_PM_ID) == "planned"
    assert await _values(db, CHRISTOPHERSEN_PM_ID, "person_wa_pdc") == [CHRISTOPHERSEN_URL]
    assert await _values(db, CHRISTOPHERSEN_PM_ID, "person_wa_pdc_lobbyist_agent") == []
    assert await _links(db, CHRISTOPHERSEN_PM_ID) == []


async def test_migrate_is_idempotent(db):
    await _person_with_pdc(db, CHRISTOPHERSEN_PM_ID, CHRISTOPHERSEN_URL)
    await _person_with_pdc(db, COLMAN_PM_ID, COLMAN_URL)

    await migrate_accesshub_identifiers(db, execute=True)
    actions = await migrate_accesshub_identifiers(db, execute=True)

    assert _status(actions, CHRISTOPHERSEN_PM_ID) == "exists"
    assert _status(actions, COLMAN_PM_ID) == "exists"
    assert await _values(db, CHRISTOPHERSEN_PM_ID, "person_wa_pdc_lobbyist_agent") == ["33"]
    assert await _links(db, CHRISTOPHERSEN_PM_ID) == [CHRISTOPHERSEN_URL]
    assert await _links(db, COLMAN_PM_ID) == [COLMAN_URL]


async def test_unexpected_value_is_a_conflict(db):
    """A value that isn't the exact issue-table URL is never touched."""
    await _person_with_pdc(db, CHRISTOPHERSEN_PM_ID, "https://accesshub.pdc.wa.gov/node/99999")

    actions = await migrate_accesshub_identifiers(db, execute=True)

    assert _status(actions, CHRISTOPHERSEN_PM_ID) == "conflict"
    assert await _values(db, CHRISTOPHERSEN_PM_ID, "person_wa_pdc") == [
        "https://accesshub.pdc.wa.gov/node/99999"
    ]
    assert await _values(db, CHRISTOPHERSEN_PM_ID, "person_wa_pdc_lobbyist_agent") == []


async def test_missing_person_is_reported(db):
    actions = await migrate_accesshub_identifiers(db, execute=True)

    non_deferred = [a for a in actions if a["status"] != "deferred"]
    assert {a["status"] for a in non_deferred} == {"missing"}
    assert len(actions) == 60


async def test_multiple_rows_are_ambiguous(db):
    await _person_with_pdc(db, CHRISTOPHERSEN_PM_ID, CHRISTOPHERSEN_URL)
    await db.execute(
        "INSERT INTO identifiers (id, entity_id, entity_identifier_type_id, value)"
        " SELECT $1, $2, t.id, $3 FROM entity_identifier_types t"
        " WHERE t.slug = 'person_wa_pdc'",
        generate_id(),
        CHRISTOPHERSEN_PM_ID,
        CHRISTOPHERSEN_URL,
    )

    actions = await migrate_accesshub_identifiers(db, execute=True)

    assert _status(actions, CHRISTOPHERSEN_PM_ID) == "ambiguous"
    assert await _values(db, CHRISTOPHERSEN_PM_ID, "person_wa_pdc_lobbyist_agent") == []


async def test_agent_id_on_another_person_is_a_collision(db):
    """Target agent_id already on a different person → duplicate pair, skip."""
    await _person_with_pdc(db, CHRISTOPHERSEN_PM_ID, CHRISTOPHERSEN_URL)
    other = generate_id()
    await db.execute("INSERT INTO people (id) VALUES ($1)", other)
    await db.execute(
        "INSERT INTO identifiers (id, entity_id, entity_identifier_type_id, value)"
        " SELECT $1, $2, t.id, $3 FROM entity_identifier_types t"
        " WHERE t.slug = 'person_wa_pdc_lobbyist_agent'",
        generate_id(),
        other,
        "33",
    )

    actions = await migrate_accesshub_identifiers(db, execute=True)

    assert _status(actions, CHRISTOPHERSEN_PM_ID) == "collision"
    assert await _values(db, CHRISTOPHERSEN_PM_ID, "person_wa_pdc") == [CHRISTOPHERSEN_URL]
    assert await _values(db, CHRISTOPHERSEN_PM_ID, "person_wa_pdc_lobbyist_agent") == []
