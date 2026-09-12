"""The curation overlay's write side (#498): pin, unpin, scope.

A pin is a curator's decision that PM's value for a producer-owned field wins
over every later snapshot. It is keyed `(entity_type, entity_id, field)` and
only an *active* row holds the field: unpinning archives, a new value archives
the old pin and inserts a fresh one, so every decision keeps its author and
time. Pins exist only for entities in the producer's row scope — a live or
merged `producer_crosswalk` row — because the models apply nothing else.
"""

import asyncpg
import pytest
import pytest_asyncio

from src.core.curation_overlay import (
    OverlayError,
    active_pin,
    active_pins,
    in_scope,
    pin,
    pin_changed,
    unpin,
    unpin_pin,
)
from src.core.db import generate_id
from src.core.ingestion.crosswalk import PRODUCER_SOURCE

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


@pytest_asyncio.fixture(loop_scope="session")
async def curator(db) -> str:
    user_id = f"u-{generate_id()}"
    await db.execute(
        "INSERT INTO app_users (id, email) VALUES ($1, $2)", user_id, "curator@example.org"
    )
    return user_id


async def _anchor(db, kind: str, pm_id: str, resolution: str = "live") -> None:
    await db.execute(
        "INSERT INTO producer_crosswalk"
        " (id, source, kind, producer_id, exported_pm_id, pm_id, resolution)"
        " VALUES ($1, $2, $3, $4, $5, $6, $7)",
        generate_id(),
        PRODUCER_SOURCE,
        kind,
        f"p-{generate_id()}",
        pm_id,
        None if resolution in ("missing", "deleted_no_successor", "cycle") else pm_id,
        resolution,
    )


async def _person(db, *, resolution: str | None = "live") -> str:
    pm_id = generate_id()
    if resolution is not None:
        await _anchor(db, "person", pm_id, resolution)
    return pm_id


async def _rows(db, entity_id: str) -> list[tuple]:
    rows = await db.fetch(
        "SELECT value, archived_at IS NULL AS active FROM curation_overlay"
        " WHERE entity_id = $1 ORDER BY created_at, active",
        entity_id,
    )
    return [(r["value"], r["active"]) for r in rows]


# --- scope ---------------------------------------------------------------------


@pytest.mark.parametrize(
    ("resolution", "expected"),
    [("live", True), ("merged", True), ("archived", False), (None, False)],
    ids=["live", "merged", "archived anchor", "no anchor"],
)
async def test_in_scope_reads_the_live_crosswalk(db, resolution, expected):
    """The models apply pins to in-scope rows only; a pin anywhere else would be inert."""
    person = await _person(db, resolution=resolution)

    assert await in_scope(db, "person", person) is expected


async def test_pin_refuses_an_entity_outside_the_crosswalk(db, curator):
    person = await _person(db, resolution=None)

    with pytest.raises(OverlayError, match="scope"):
        await pin(db, "person", person, "name", "Curated", user_id=curator)


# --- pin and unpin ----------------------------------------------------------------


async def test_pin_writes_an_active_row_with_its_author(db, curator):
    person = await _person(db)

    written = await pin(db, "person", person, "name", "Curated", user_id=curator, note="why")

    held = await active_pin(db, "person", person, "name")
    assert held == written
    assert (held.value, held.note, held.created_by) == ("Curated", "why", curator)


async def test_pin_with_the_same_value_is_a_noop(db, curator):
    person = await _person(db)
    first = await pin(db, "person", person, "name", "Curated", user_id=curator)

    again = await pin(db, "person", person, "name", "Curated", user_id=curator)

    assert again.id == first.id
    assert await _rows(db, person) == [("Curated", True)]


async def test_a_new_value_archives_the_old_pin_and_inserts_a_fresh_one(db, curator):
    """Every decision keeps its author and time: a changed pin is history, not an edit."""
    person = await _person(db)
    await pin(db, "person", person, "name", "First", user_id=curator)

    await pin(db, "person", person, "name", "Second", user_id=curator)

    assert await _rows(db, person) == [("First", False), ("Second", True)]


async def test_a_null_pin_asserts_the_field_empty(db, curator):
    person = await _person(db)

    await pin(db, "person", person, "name", None, user_id=curator)

    held = await active_pin(db, "person", person, "name")
    assert held is not None and held.value is None


async def test_a_value_is_stored_as_text(db, curator):
    """`curation_overlay.value` is TEXT; a year arrives as an int and leaves as '2018'."""
    org = generate_id()
    await _anchor(db, "organization", org)

    await pin(db, "organization", org, "dissolved_year", 2018, user_id=curator)

    assert (await active_pin(db, "organization", org, "dissolved_year")).value == "2018"


async def test_unpin_archives_the_active_row(db, curator):
    person = await _person(db)
    await pin(db, "person", person, "name", "Curated", user_id=curator)

    assert await unpin(db, "person", person, "name", user_id=curator) is True

    assert await active_pin(db, "person", person, "name") is None
    assert await _rows(db, person) == [("Curated", False)]
    assert await unpin(db, "person", person, "name", user_id=curator) is False


async def test_a_repin_after_an_unpin_is_a_fresh_active_row(db, curator):
    person = await _person(db)
    await pin(db, "person", person, "name", "Curated", user_id=curator)
    await unpin(db, "person", person, "name", user_id=curator)

    await pin(db, "person", person, "name", "Curated Again", user_id=curator)

    assert await _rows(db, person) == [("Curated", False), ("Curated Again", True)]


async def test_active_pins_lists_an_entitys_live_pins_only(db, curator):
    org = generate_id()
    await _anchor(db, "organization", org)
    await pin(db, "organization", org, "acronym", "OLD", user_id=curator)
    await pin(db, "organization", org, "acronym", "NEW", user_id=curator)
    await pin(db, "organization", org, "parent_id", None, user_id=curator)

    held = await active_pins(db, "organization", org)

    assert {p.field: p.value for p in held} == {"acronym": "NEW", "parent_id": None}


async def test_the_table_still_refuses_two_active_pins(db, curator):
    """The partial index is the backstop the module relies on, not a convention."""
    person = await _person(db)
    await pin(db, "person", person, "name", "Curated", user_id=curator)

    with pytest.raises(asyncpg.UniqueViolationError):
        await db.execute(
            "INSERT INTO curation_overlay (id, entity_type, entity_id, field, value)"
            " VALUES ($1, 'person', $2, 'name', 'Racing')",
            generate_id(),
            person,
        )


# --- pin_changed: the admin's edit hook ------------------------------------------


async def test_pin_changed_pins_only_the_fields_whose_value_moved(db, curator):
    org = generate_id()
    await _anchor(db, "organization", org)

    pinned = await pin_changed(
        db,
        "organization",
        org,
        before={"legal_name": "Old Name", "acronym": "ON"},
        after={"legal_name": "New Name", "acronym": "ON"},
        user_id=curator,
    )

    assert pinned == ["legal_name"]
    assert {p.field: p.value for p in await active_pins(db, "organization", org)} == {
        "legal_name": "New Name"
    }


async def test_pin_changed_pins_null_when_the_slot_emptied(db, curator):
    org = generate_id()
    await _anchor(db, "organization", org)

    await pin_changed(
        db,
        "organization",
        org,
        before={"parent_id": "P"},
        after={"parent_id": None},
        user_id=curator,
    )

    assert (await active_pin(db, "organization", org, "parent_id")).value is None


async def test_pin_changed_outside_the_crosswalk_writes_nothing(db, curator):
    """Direct curation stays direct curation: nothing to pin against."""
    person = await _person(db, resolution=None)

    pinned = await pin_changed(
        db, "person", person, before={"name": "A"}, after={"name": "B"}, user_id=curator
    )

    assert pinned == []
    assert await _rows(db, person) == []


# --- unpin by id: the pins page's row action --------------------------------------


async def test_unpin_pin_archives_that_row_and_returns_it(db, curator):
    person = await _person(db)
    held = await pin(db, "person", person, "name", "Curated", user_id=curator)

    gone = await unpin_pin(db, held.id, user_id=curator)

    assert gone is not None and gone.id == held.id and gone.archived_at is not None
    assert await active_pin(db, "person", person, "name") is None


async def test_unpin_pin_on_a_stale_row_never_touches_the_newer_pin(db, curator):
    """A list row can be older than the page: its pin may have been replaced since.
    Unpinning *that* row must not archive the live pin that superseded it."""
    person = await _person(db)
    old = await pin(db, "person", person, "name", "First", user_id=curator)
    await pin(db, "person", person, "name", "Second", user_id=curator)  # archives `old`

    assert await unpin_pin(db, old.id, user_id=curator) is None

    assert (await active_pin(db, "person", person, "name")).value == "Second"


# --- who let a pin go (CR 6) --------------------------------------------------------


@pytest_asyncio.fixture(loop_scope="session")
async def second_curator(db) -> str:
    user_id = generate_id()
    await db.execute(
        "INSERT INTO app_users (id, email) VALUES ($1, $2)", user_id, "second@example.org"
    )
    return user_id


async def _archived_by(db, pin_id: str) -> str | None:
    return await db.fetchval("SELECT archived_by FROM curation_overlay WHERE id = $1", pin_id)


async def test_unpin_records_who_let_the_pin_go(db, curator, second_curator):
    """Unpinning is a decision too — the producer's value returns — so it keeps its
    author beside the pinner's, in the row and not only in the log."""
    person = await _person(db)
    held = await pin(db, "person", person, "name", "Curated", user_id=curator)

    await unpin(db, "person", person, "name", user_id=second_curator)

    assert await _archived_by(db, held.id) == second_curator


async def test_unpin_pin_records_who_let_the_pin_go(db, curator, second_curator):
    person = await _person(db)
    held = await pin(db, "person", person, "name", "Curated", user_id=curator)

    gone = await unpin_pin(db, held.id, user_id=second_curator)

    assert gone.archived_by == second_curator


async def test_a_new_value_records_who_displaced_the_old_pin(db, curator, second_curator):
    person = await _person(db)
    old = await pin(db, "person", person, "name", "First", user_id=curator)

    new = await pin(db, "person", person, "name", "Second", user_id=second_curator)

    assert await _archived_by(db, old.id) == second_curator
    assert new.archived_by is None
