"""Tests for the seat-occupancy audit (#506, #671).

usa-wa's D7 predicate (#671): at each principal's start and each capacity
period's start, more distinct principals holding a role than its capacity is a
finding — every pair of them. Capacity is the covering `role_capacity_periods`
row's `max_holders` (null = no limit), else the role type's `occupancy`
(`single` → 1). Intervals are half-open, so a same-day handoff is not an
overlap; substitutes never count. The audit is a ratchet: it fails only when
the count rises above the recorded baseline.

The DB cases run in a rolled-back transaction on the shared test DB, which may
hold other rows, so they assert on the pairs they create, never on totals.
"""

import datetime
import logging
from pathlib import Path

import pytest
import pytest_asyncio

from scripts.audit_seat_occupancy import BASELINE, audit, exit_code, find_overlaps
from src.core.db import generate_id

D = datetime.date
INFRA = Path(__file__).resolve().parents[2] / "infra"

# --------------------------------------------------------------------------- #
# Ratchet (no DB)
# --------------------------------------------------------------------------- #


def test_baseline_only_ratchets_down_from_the_2026_10_27_measurement():
    """Lowering it as triage lands is the point; raising it to clear a failure is not.

    56 on 2026-10-10; 55 once #639 truncated Robinson's legacy LD-38 Pos. 1 row;
    48 after #671's re-pin re-keyed 17 spans (measured 2026-10-23); 25 once #671's
    execute landed capacity periods and substitutes (measured 2026-10-25, equal
    pair for pair to usa-wa's may-tier list); 22 once #703's execute split four
    return tenures (measured 2026-10-27, again equal pair for pair).
    """
    assert 0 <= BASELINE <= 22


def test_exit_code_at_baseline_is_zero():
    assert exit_code(56, baseline=56) == 0


def test_exit_code_below_baseline_is_zero():
    """Triage landing lowers the count; that never fails the unit."""
    assert exit_code(12, baseline=56) == 0


def test_exit_code_above_baseline_is_three():
    assert exit_code(57, baseline=56) == 3


# --------------------------------------------------------------------------- #
# Timer
# --------------------------------------------------------------------------- #


def test_service_runs_the_audit_read_only_in_the_main_checkout():
    text = (INFRA / "power-map-seat-occupancy.service").read_text()
    assert "Type=oneshot" in text
    assert "WorkingDirectory=/home/exedev/power-map" in text
    assert "ExecStart=uv run python -m scripts.audit_seat_occupancy\n" in text
    assert "--execute" not in text


def test_timer_is_daily_and_persistent():
    text = (INFRA / "power-map-seat-occupancy.timer").read_text()
    assert "OnCalendar=daily" in text
    assert "Persistent=true" in text


# --------------------------------------------------------------------------- #
# Overlap rule (DB)
# --------------------------------------------------------------------------- #

integration = pytest.mark.integration


@pytest_asyncio.fixture(loop_scope="session")
async def conn(db_pool):
    async with db_pool.acquire() as c:
        tr = c.transaction()
        await tr.start()
        try:
            yield c
        finally:
            await tr.rollback()


async def _role(conn, slug="state_senator", qualifier=None) -> str:
    org, jur, role = generate_id(), generate_id(), generate_id()
    await conn.execute("INSERT INTO organizations (id) VALUES ($1)", org)
    await conn.execute(
        "INSERT INTO jurisdictions (id, slug, name, type_id)"
        " SELECT $1, $2, 'Test LD', id FROM jurisdiction_types WHERE slug='legislative_district'",
        jur,
        f"ld-{jur[-8:].lower()}",
    )
    await conn.execute(
        "INSERT INTO roles (id, organization_id, title, role_type_id, jurisdiction_id, qualifier)"
        " SELECT $1, $2, 'Seat', id, $3, $4 FROM role_types WHERE slug=$5",
        role,
        org,
        jur,
        qualifier,
        slug,
    )
    return role


async def _person(conn, name: str | None = None) -> str:
    pid = generate_id()
    await conn.execute("INSERT INTO people (id) VALUES ($1)", pid)
    if name:
        await conn.execute(
            "INSERT INTO person_names (id, person_id, name, name_type, is_canonical)"
            " VALUES ($1, $2, $3, 'legal', TRUE)",
            generate_id(),
            pid,
            name,
        )
    return pid


async def _hold(
    conn, role, start, end, *, person=None, current=False, membership="principal"
) -> str:
    aid = generate_id()
    await conn.execute(
        "INSERT INTO role_assignments"
        " (id, person_id, role_id, start_date, end_date, is_current, membership_type)"
        " VALUES ($1,$2,$3,$4,$5,$6,$7)",
        aid,
        person or await _person(conn),
        role,
        start,
        end,
        current,
        membership,
    )
    return aid


async def _period(conn, role, valid_from, valid_to, max_holders, *, archived=False) -> None:
    await conn.execute(
        "INSERT INTO role_capacity_periods"
        " (id, role_id, valid_from, valid_to, max_holders, archived_at)"
        " VALUES ($1, $2, $3, $4, $5, CASE WHEN $6 THEN NOW() END)",
        generate_id(),
        role,
        valid_from,
        valid_to,
        max_holders,
        archived,
    )


async def _untyped_role(conn) -> str:
    org, role = generate_id(), generate_id()
    await conn.execute("INSERT INTO organizations (id) VALUES ($1)", org)
    await conn.execute(
        "INSERT INTO roles (id, organization_id, title) VALUES ($1, $2, 'Legacy')", role, org
    )
    return role


async def _pairs(conn, role) -> set[frozenset]:
    return {
        frozenset((r["a_id"], r["b_id"])) for r in await find_overlaps(conn) if r["role_id"] == role
    }


@integration
async def test_strict_overlap_on_single_seat_is_a_finding(conn):
    """The #506 shape: the predecessor's end never truncated to the successor's start."""
    role = await _role(conn)
    a = await _hold(conn, role, D(1943, 1, 1), D(1958, 12, 31))
    b = await _hold(conn, role, D(1957, 1, 1), D(1974, 12, 31))
    assert await _pairs(conn, role) == {frozenset((a, b))}


@integration
async def test_positioned_house_seat_is_single(conn):
    role = await _role(conn, "state_representative", "Position 1")
    a = await _hold(conn, role, D(2019, 1, 1), D(2020, 12, 31))
    b = await _hold(conn, role, D(2020, 5, 13), D(2022, 12, 31))
    assert await _pairs(conn, role) == {frozenset((a, b))}


@integration
async def test_same_day_handoff_is_not_a_finding(conn):
    """Mid-term succession: one span ends the day the next begins."""
    role = await _role(conn)
    await _hold(conn, role, D(2007, 1, 5), D(2007, 11, 29))
    await _hold(conn, role, D(2007, 11, 29), None, current=True)
    assert await _pairs(conn, role) == set()


@integration
async def test_current_holder_is_open_ended(conn):
    role = await _role(conn)
    a = await _hold(conn, role, D(2020, 1, 1), None, current=True)
    b = await _hold(conn, role, D(2023, 1, 1), D(2024, 12, 31))
    assert await _pairs(conn, role) == {frozenset((a, b))}


@integration
async def test_undated_spans_are_never_findings(conn):
    """No span is invented: an unknown start, or an unknown end that is not current."""
    role = await _role(conn)
    await _hold(conn, role, D(2020, 1, 1), D(2024, 12, 31))
    await _hold(conn, role, None, D(2022, 12, 31))
    await _hold(conn, role, D(2021, 1, 1), None)
    assert await _pairs(conn, role) == set()


@integration
async def test_shared_occupancy_role_is_out_of_scope(conn):
    """At-large: N concurrent holders on one role is the design (#302)."""
    role = await _role(conn, "state_representative_at_large")
    await _hold(conn, role, D(1949, 1, 1), D(1950, 12, 31))
    await _hold(conn, role, D(1949, 1, 1), D(1950, 12, 31))
    assert await _pairs(conn, role) == set()


@integration
async def test_same_person_is_left_to_the_duplicate_audit(conn):
    """One person on one role twice is #311's shape, not a second occupant."""
    role = await _role(conn)
    person = await _person(conn)
    await _hold(conn, role, D(2013, 1, 1), D(2018, 12, 31), person=person)
    await _hold(conn, role, D(2015, 1, 1), D(2020, 12, 31), person=person)
    assert await _pairs(conn, role) == set()


@integration
async def test_archived_rows_are_not_live(conn):
    role = await _role(conn)
    await _hold(conn, role, D(2001, 1, 1), D(2002, 12, 31))
    b = await _hold(conn, role, D(2001, 6, 1), D(2004, 12, 31))
    await conn.execute("UPDATE role_assignments SET archived_at=NOW() WHERE id=$1", b)
    assert await _pairs(conn, role) == set()

    archived_role = await _role(conn)
    await _hold(conn, archived_role, D(2001, 1, 1), D(2002, 12, 31))
    await _hold(conn, archived_role, D(2001, 6, 1), D(2004, 12, 31))
    await conn.execute("UPDATE roles SET archived_at=NOW() WHERE id=$1", archived_role)
    assert await _pairs(conn, archived_role) == set()


@integration
async def test_audit_fails_only_above_baseline_and_names_each_pair(conn, caplog):
    role = await _role(conn)
    zahn = await _person(conn, "George D. Zahn")
    a = await _hold(conn, role, D(1955, 1, 10), D(1956, 12, 31), person=zahn)
    b = await _hold(conn, role, D(1949, 1, 1), D(1956, 12, 31))  # unnamed: id stands in
    count = len(await find_overlaps(conn))

    with caplog.at_level(logging.INFO, logger="scripts.audit_seat_occupancy"):
        assert await audit(conn, baseline=count) == 0
    # Triage reads who, not just which rows: a name where one exists, the id always.
    assert any(a in m and b in m and "George D. Zahn" in m for m in caplog.messages)

    caplog.clear()
    with caplog.at_level(logging.INFO, logger="scripts.audit_seat_occupancy"):
        assert await audit(conn, baseline=count - 1) == 3
    assert any(f"{count} pairs" in m and f"baseline {count - 1}" in m for m in caplog.messages)

    # A stale baseline passes, but loudly: "fewer" must not read like routine INFO.
    caplog.clear()
    with caplog.at_level(logging.INFO, logger="scripts.audit_seat_occupancy"):
        assert await audit(conn, baseline=count + 1) == 0
    stale = [r for r in caplog.records if "below baseline" in r.getMessage()]
    assert [r.levelno for r in stale] == [logging.WARNING]
    assert f"lower BASELINE to {count}" in stale[0].getMessage()


# --------------------------------------------------------------------------- #
# D7: capacity periods and principals (#671)
# --------------------------------------------------------------------------- #


def _all_pairs(*ids) -> set[frozenset]:
    return {frozenset((a, b)) for i, a in enumerate(ids) for b in ids[i + 1 :]}


@integration
async def test_a_substitute_never_counts_toward_capacity(conn):
    """Kreidler, LD-22, 1991: she served under her husband on military leave."""
    role = await _role(conn)
    await _hold(conn, role, D(1987, 1, 12), D(1993, 1, 11))
    await _hold(conn, role, D(1991, 1, 14), D(1991, 4, 18), membership="substitute")
    assert await _pairs(conn, role) == set()


@integration
async def test_holders_within_a_periods_capacity_are_not_findings(conn):
    """LD-5, 1889: three senators in a seat that held three. They hand off the day
    the period ends; running on past it would meet the seat of one after (CR 4)."""
    role = await _role(conn)
    await _period(conn, role, D(1889, 1, 1), D(1890, 12, 31), 3)
    for _ in range(3):
        await _hold(conn, role, D(1889, 11, 6), D(1891, 1, 1))
    assert await _pairs(conn, role) == set()


@integration
async def test_one_holder_over_capacity_makes_every_holder_a_pair(conn):
    """D7 counts holders, not pairs: four in a seat of three names all six pairs."""
    role = await _role(conn)
    await _period(conn, role, D(1889, 1, 1), D(1890, 12, 31), 3)
    held = [await _hold(conn, role, D(1889, 11, 6), D(1890, 12, 31)) for _ in range(4)]
    assert await _pairs(conn, role) == _all_pairs(*held)


@integration
async def test_capacity_dropping_at_a_period_start_is_caught(conn):
    """Nobody starts on 1891-01-01, yet two holders run on into a seat of one."""
    role = await _role(conn)
    await _period(conn, role, D(1889, 1, 1), D(1890, 12, 31), 2)
    await _period(conn, role, D(1891, 1, 1), None, 1)
    a = await _hold(conn, role, D(1889, 11, 6), D(1892, 12, 31))
    b = await _hold(conn, role, D(1889, 11, 6), D(1892, 12, 31))
    assert await _pairs(conn, role) == {frozenset((a, b))}


@integration
async def test_a_periods_last_day_is_inside_it(conn):
    """`valid_to` is inclusive: a second holder starting on it is under the seat of two."""
    role = await _role(conn)
    await _period(conn, role, D(1889, 1, 1), D(1890, 12, 31), 2)
    await _period(conn, role, D(1891, 1, 1), None, 1)
    await _hold(conn, role, D(1889, 11, 6), D(1891, 1, 1))
    await _hold(conn, role, D(1890, 12, 31), D(1891, 1, 1))
    assert await _pairs(conn, role) == set()


@integration
async def test_capacity_dropping_after_a_closed_last_period_is_caught(conn):
    """CR 4: no period follows 1890-12-31, so from 1891-01-01 the seat falls back
    to its type's one; nobody starts that day, yet two holders run on past it.
    PM checks the day after a closed period ends — an instant usa-wa's D7 lacks,
    and one its dense tiling makes a period start everywhere it publishes today."""
    role = await _role(conn)
    await _period(conn, role, D(1889, 1, 1), D(1890, 12, 31), 2)
    a = await _hold(conn, role, D(1889, 11, 6), D(1892, 12, 31))
    b = await _hold(conn, role, D(1889, 11, 6), D(1892, 12, 31))
    assert await _pairs(conn, role) == {frozenset((a, b))}


@integration
async def test_a_period_without_a_limit_overrides_a_single_type(conn):
    role = await _role(conn)
    await _period(conn, role, D(1889, 1, 1), None, None)
    await _hold(conn, role, D(1949, 1, 1), D(1950, 12, 31))
    await _hold(conn, role, D(1949, 1, 1), D(1950, 12, 31))
    assert await _pairs(conn, role) == set()


@integration
async def test_a_period_with_a_limit_overrides_a_shared_type(conn):
    """The period is the seat's statement; the type default is only the fallback."""
    role = await _role(conn, "state_representative_at_large")
    await _period(conn, role, D(1949, 1, 1), None, 1)
    a = await _hold(conn, role, D(1949, 1, 1), D(1950, 12, 31))
    b = await _hold(conn, role, D(1949, 1, 1), D(1950, 12, 31))
    assert await _pairs(conn, role) == {frozenset((a, b))}


@integration
async def test_an_archived_period_falls_back_to_the_type_default(conn):
    role = await _role(conn)
    await _period(conn, role, D(1889, 1, 1), D(1890, 12, 31), 3, archived=True)
    a = await _hold(conn, role, D(1889, 11, 6), D(1890, 12, 31))
    b = await _hold(conn, role, D(1889, 11, 6), D(1890, 12, 31))
    assert await _pairs(conn, role) == {frozenset((a, b))}


@integration
async def test_an_instant_outside_every_period_falls_back_to_the_type_default(conn):
    """A role's first period starts at its earliest published tenure (usa-wa limit 4)."""
    role = await _role(conn)
    await _period(conn, role, D(1891, 1, 1), None, 5)
    a = await _hold(conn, role, D(1887, 1, 1), D(1888, 12, 31))
    b = await _hold(conn, role, D(1887, 6, 1), D(1888, 12, 31))
    assert await _pairs(conn, role) == {frozenset((a, b))}


@integration
async def test_a_role_with_no_type_and_no_period_has_no_limit(conn):
    """PM's legacy roles: nothing says how many they seat, so nothing is policed."""
    role = await _untyped_role(conn)
    await _hold(conn, role, D(2001, 1, 1), D(2002, 12, 31))
    await _hold(conn, role, D(2001, 6, 1), D(2004, 12, 31))
    assert await _pairs(conn, role) == set()
