"""Re-standardizing an address row and merging it into its twin (#595).

Rollback-wrapped; the observable for "touched a parent" is ``row_version``.
"""

import json
from datetime import date

import pytest
import pytest_asyncio

from src.core.address_repair import RepairOutcome, apply_result, compose_input, find_twins
from src.core.db import generate_id
from src.core.normalizers.base import NormalizationResult
from tests.db_utils import row_version

FORM = "123 MAIN ST SEATTLE WA 98101"


def _standardized(form: str = FORM) -> NormalizationResult:
    return NormalizationResult(
        value={
            "raw_input": "123 Main St Seattle WA",
            "address_line_1": "123 MAIN ST",
            "address_line_2": None,
            "city": "SEATTLE",
            "region": "WA",
            "postal_code": "98101",
            "country": "US",
            "standardized": form,
            "latitude": 47.6,
            "longitude": -122.3,
            "components": {"spec": "usps-pub28"},
        },
        validation_detail={"provider": "address-validator"},
    )


def _fell_back(reason: str) -> NormalizationResult:
    return NormalizationResult(
        value={"raw_input": "123 Main St Seattle WA", "standardized": None},
        validation_detail={"provider": "usaddress", "fallback": reason},
    )


# ── compose_input (no DB) ────────────────────────────────────────────────────


def test_compose_input_prefers_raw_input():
    row = {
        "raw_input": "1 A St, Olympia",
        "address_line_1": "ignored",
        "address_line_2": None,
        "city": None,
        "region": None,
        "postal_code": None,
    }
    assert compose_input(row) == "1 A St, Olympia"


def test_compose_input_joins_the_lines_of_an_admin_row():
    """Admin rows have no raw_input (#595 correction 2)."""
    row = {
        "raw_input": None,
        "address_line_1": "1 A St ",
        "address_line_2": "",
        "city": "Olympia",
        "region": "WA",
        "postal_code": "98501",
    }
    assert compose_input(row) == "1 A St Olympia WA 98501"


# ── DB ───────────────────────────────────────────────────────────────────────


@pytest_asyncio.fixture(loop_scope="session")
async def db(db_pool):
    async with db_pool.acquire() as conn:
        tr = conn.transaction()
        await tr.start()
        try:
            yield conn
        finally:
            await tr.rollback()


async def _org(db) -> str:
    oid = generate_id()
    await db.execute("INSERT INTO organizations (id) VALUES ($1)", oid)
    return oid


async def _address(db, raw: str, standardized: str | None = None, status: str = "unavailable"):
    aid = generate_id()
    await db.execute(
        "INSERT INTO addresses (id, raw_input, address_line_1, city, region, country,"
        "  standardized, standardization_status, standardization_checked_at)"
        " VALUES ($1, $2, '123 Main St', 'Seattle', 'WA', 'US', $3, $4, '2026-01-01Z')",
        aid,
        raw,
        standardized,
        status,
    )
    return aid


async def _link(db, eid, aid, *, address_type="mailing", valid_from=None, valid_until=None):
    lid = generate_id()
    await db.execute(
        "INSERT INTO entity_addresses (id, entity_type, entity_id, address_id, address_type,"
        "  valid_from, valid_until) VALUES ($1, 'organization', $2, $3, $4, $5, $6)",
        lid,
        eid,
        aid,
        address_type,
        valid_from,
        valid_until,
    )
    return lid


async def _links(db, eid) -> list[tuple]:
    return [
        tuple(r)
        for r in await db.fetch(
            "SELECT address_id, address_type, valid_from, valid_until FROM entity_addresses"
            " WHERE entity_id=$1 ORDER BY address_id, valid_from NULLS FIRST",
            eid,
        )
    ]


async def _exists(db, aid) -> bool:
    return await db.fetchval("SELECT EXISTS (SELECT 1 FROM addresses WHERE id=$1)", aid)


integration = pytest.mark.integration  # the compose_input tests need no DB


@integration
@pytest.mark.parametrize("status", ["unavailable", "local", "unsupported"])
async def test_an_unchanged_status_writes_nothing(db, status):
    """``checked_at`` is when the status was decided, so the audit's clock keeps running (#797)."""
    oid = await _org(db)
    aid = await _address(db, "123 Main St Seattle WA", status=status)
    await _link(db, oid, aid)
    before = dict(await db.fetchrow("SELECT * FROM addresses WHERE id=$1", aid))

    outcome = await apply_result(db, aid, _fell_back(status))

    assert outcome == RepairOutcome(aid, status)
    assert dict(await db.fetchrow("SELECT * FROM addresses WHERE id=$1", aid)) == before


@integration
@pytest.mark.parametrize("reason", ["rejected", "unsupported"])
async def test_a_result_without_a_form_updates_only_the_status(db, reason):
    oid = await _org(db)
    aid = await _address(db, "123 Main St Seattle WA")
    await _link(db, oid, aid)
    before = await row_version(db, "organizations", oid)

    outcome = await apply_result(db, aid, _fell_back(reason))

    row = await db.fetchrow("SELECT * FROM addresses WHERE id=$1", aid)
    assert outcome.status == reason
    assert row["standardization_status"] == reason
    assert row["standardized"] is None
    assert str(row["standardization_checked_at"].date()) != "2026-01-01"  # bumped
    assert await row_version(db, "organizations", oid) == before


@integration
async def test_a_standardized_result_is_written_with_accept_semantics(db):
    oid = await _org(db)
    aid = await _address(db, "123 Main St Seattle WA")
    await _link(db, oid, aid)
    before = await row_version(db, "organizations", oid)

    outcome = await apply_result(db, aid, _standardized())

    row = await db.fetchrow("SELECT * FROM addresses WHERE id=$1", aid)
    assert (outcome.status, outcome.deleted) == ("standardized", False)
    assert row["standardized"] == FORM
    assert row["standardization_status"] == "standardized"
    assert (row["address_line_1"], row["city"], row["postal_code"]) == (
        "123 MAIN ST",
        "SEATTLE",
        "98101",
    )
    assert (row["latitude"], row["longitude"]) == (47.6, -122.3)
    assert json.loads(row["components"]) == {"spec": "usps-pub28"}
    assert row["raw_input"] == "123 Main St Seattle WA"
    assert await row_version(db, "organizations", oid) != before


@integration
async def test_an_exact_duplicate_link_is_dropped_and_the_row_deleted(db):
    oid = await _org(db)
    a = await _address(db, "123 Main St Seattle WA")
    b = await _address(db, "123 Main Street, Seattle", standardized=FORM, status="standardized")
    await _link(db, oid, a)
    await _link(db, oid, b)

    outcome = await apply_result(db, a, _standardized())

    assert (outcome.links_moved, outcome.links_dropped, outcome.deleted) == (0, 1, True)
    assert not await _exists(db, a)
    assert await _links(db, oid) == [(b, "mailing", None, None)]


@integration
async def test_a_different_window_moves_onto_the_twin_unchanged(db):
    """Two windows of one address are history, not duplicates (#181/#256)."""
    oid = await _org(db)
    a = await _address(db, "123 Main St Seattle WA")
    b = await _address(db, "123 Main Street, Seattle", standardized=FORM, status="standardized")
    await _link(db, oid, a, valid_from=date(2010, 1, 1), valid_until=date(2014, 12, 31))
    await _link(db, oid, b)

    outcome = await apply_result(db, a, _standardized())

    assert (outcome.links_moved, outcome.links_dropped, outcome.deleted) == (1, 0, True)
    assert await _links(db, oid) == [
        (b, "mailing", None, None),
        (b, "mailing", date(2010, 1, 1), date(2014, 12, 31)),
    ]


@integration
async def test_another_address_type_is_not_a_twin(db):
    oid = await _org(db)
    a = await _address(db, "123 Main St Seattle WA")
    b = await _address(db, "123 Main Street, Seattle", standardized=FORM, status="standardized")
    await _link(db, oid, a, address_type="physical")
    await _link(db, oid, b, address_type="mailing")

    outcome = await apply_result(db, a, _standardized())

    assert (outcome.links_moved, outcome.links_dropped, outcome.deleted) == (0, 0, False)
    assert await _exists(db, a)


@integration
async def test_another_entitys_twin_does_not_merge(db):
    oid, other = await _org(db), await _org(db)
    a = await _address(db, "123 Main St Seattle WA")
    b = await _address(db, "123 Main Street, Seattle", standardized=FORM, status="standardized")
    await _link(db, oid, a)
    await _link(db, other, b)

    outcome = await apply_result(db, a, _standardized())

    assert (outcome.links_moved, outcome.links_dropped, outcome.deleted) == (0, 0, False)


@integration
async def test_a_row_still_linked_elsewhere_is_kept(db):
    """Only the colliding link moves; the row stays for the entity with no twin."""
    oid, other = await _org(db), await _org(db)
    a = await _address(db, "123 Main St Seattle WA")
    b = await _address(db, "123 Main Street, Seattle", standardized=FORM, status="standardized")
    await _link(db, oid, a)
    await _link(db, oid, b)
    await _link(db, other, a)

    outcome = await apply_result(db, a, _standardized())

    assert (outcome.links_dropped, outcome.deleted) == (1, False)
    assert await _links(db, other) == [(a, "mailing", None, None)]


@integration
async def test_a_row_an_event_places_at_is_kept(db):
    """The event FK is ON DELETE SET NULL: deleting would blank the event's place."""
    oid = await _org(db)
    a = await _address(db, "123 Main St Seattle WA")
    b = await _address(db, "123 Main Street, Seattle", standardized=FORM, status="standardized")
    await _link(db, oid, a)
    await _link(db, oid, b)
    type_id = await db.fetchval("SELECT id FROM entity_event_types ORDER BY slug LIMIT 1")
    await db.execute(
        "INSERT INTO entity_events"
        " (id, entity_type, entity_id, event_type_id, event_year, event_place_address_id)"
        " VALUES ($1, 'organization', $2, $3, 2001, $4)",
        generate_id(),
        oid,
        type_id,
        a,
    )

    outcome = await apply_result(db, a, _standardized())

    assert (outcome.links_dropped, outcome.deleted) == (1, False)
    assert await _exists(db, a)


@integration
async def test_find_twins_previews_without_writing(db):
    oid = await _org(db)
    a = await _address(db, "123 Main St Seattle WA")
    b = await _address(db, "123 Main Street, Seattle", standardized=FORM, status="standardized")
    same = await _link(db, oid, a)
    windowed = await _link(db, oid, a, valid_from=date(2010, 1, 1))
    await _link(db, oid, b)

    twins = await find_twins(db, a, FORM)

    assert sorted((t["link_id"], t["twin_id"], t["duplicate"]) for t in twins) == sorted(
        [(same, b, True), (windowed, b, False)]
    )
    assert await db.fetchval("SELECT standardized FROM addresses WHERE id=$1", a) is None
