"""The re-standardization sweep (#595): selection, dry run vs --execute, the audit exit."""

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio

from scripts.restandardize_addresses import (
    EXIT_STALE,
    SWEEPABLE,
    fetch_candidates,
    oldest_unavailable,
    print_report,
    run,
    sweep,
)
from src.core.db import generate_id
from src.core.normalizers.base import NormalizationResult

FORM = "1 MAIN ST OLYMPIA WA 98501"
NOW = datetime(2026, 11, 12, 12, 0, tzinfo=UTC)


class _FakeNormalizer:
    """Answers every input with one prepared result; records what it was asked."""

    def __init__(self, result: NormalizationResult):
        self.result = result
        self.calls: list[tuple[str, str]] = []

    async def normalize(self, raw, country="US"):
        self.calls.append((raw, country))
        return self.result


def _standardized() -> NormalizationResult:
    return NormalizationResult(
        value={"raw_input": "1 Main St", "address_line_1": "1 MAIN ST", "standardized": FORM},
        validation_detail={"provider": "address-validator"},
    )


def _rejected() -> NormalizationResult:
    return NormalizationResult(
        value={"raw_input": "1 Main St", "standardized": None},
        validation_detail={"provider": "usaddress", "fallback": "rejected"},
    )


# ── selection: never a curator's decision ───────────────────────────────────


def test_sweepable_statuses_exclude_terminal_ones():
    assert SWEEPABLE == ("unavailable", "local", "unsupported")
    for never in ("declined", "rejected", "no_result", "standardized"):
        assert never not in SWEEPABLE


@pytest.mark.parametrize("status", ["declined", "rejected", "no_result", "standardized"])
def test_the_parser_refuses_a_terminal_status(status):
    with pytest.raises(SystemExit) as exc:
        asyncio.run(run(["--status", status, "--database-url", "postgresql://h/db"]))
    assert exc.value.code == 2


async def test_fetch_candidates_refuses_declined_before_touching_the_db():
    with pytest.raises(ValueError, match="declined"):
        await fetch_candidates(None, ("unavailable", "declined"), 10)


async def test_a_missing_api_key_is_a_usage_error(local_address_normalizer, capsys):
    """A sweep with no validator would only re-mark every row 'local'."""
    with pytest.raises(SystemExit) as exc:
        await run(["--database-url", "postgresql://h/db"])
    assert exc.value.code == 2
    assert "ADDRESS_VALIDATOR_API_KEY" in capsys.readouterr().err


# ── the audit's stale rule ───────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("age", "stale"),
    [(None, False), (timedelta(days=2), False), (timedelta(days=3, hours=1), True)],
)
def test_print_report_flags_an_unavailable_row_older_than_three_days(age, stale, capsys):
    oldest = None if age is None else NOW - age
    assert print_report({"unavailable": 1}, oldest, NOW) is stale
    assert ("STALE" in capsys.readouterr().out) is stale


def test_exit_stale_is_three():
    assert EXIT_STALE == 3


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


async def _address(db, status: str, checked_at=None, raw="1 Main St, Olympia WA") -> str:
    aid, oid = generate_id(), generate_id()
    await db.execute("INSERT INTO organizations (id) VALUES ($1)", oid)
    await db.execute(
        "INSERT INTO addresses (id, raw_input, country, standardization_status,"
        "  standardization_checked_at) VALUES ($1, $2, 'US', $3, $4)",
        aid,
        raw,
        status,
        checked_at,
    )
    await db.execute(
        "INSERT INTO entity_addresses (id, entity_type, entity_id, address_id, address_type)"
        " VALUES ($1, 'organization', $2, $3, 'mailing')",
        generate_id(),
        oid,
        aid,
    )
    return aid


async def _status(db, aid) -> tuple:
    row = await db.fetchrow(
        "SELECT standardization_status, standardized FROM addresses WHERE id=$1", aid
    )
    return tuple(row)


@pytest.mark.integration
async def test_candidates_are_only_the_selected_statuses_oldest_first(db):
    newer = await _address(db, "unavailable", datetime(2026, 11, 1, tzinfo=UTC))
    older = await _address(db, "unavailable", datetime(2026, 10, 1, tzinfo=UTC))
    declined = await _address(db, "declined", datetime(2026, 1, 1, tzinfo=UTC))
    local = await _address(db, "local", datetime(2026, 1, 1, tzinfo=UTC))

    ids = [r["id"] for r in await fetch_candidates(db, ("unavailable",), 1000)]

    mine = [i for i in ids if i in {newer, older, declined, local}]
    assert mine == [older, newer]


@pytest.mark.integration
async def test_the_dry_run_calls_the_validator_but_writes_nothing(db):
    aid = await _address(db, "unavailable")
    normalizer = _FakeNormalizer(_standardized())
    candidates = [r for r in await fetch_candidates(db, ("unavailable",), 1000) if r["id"] == aid]

    tally = await sweep(db, normalizer, candidates, execute=False)

    assert normalizer.calls == [("1 Main St, Olympia WA", "US")]
    assert tally["unavailable -> standardized"] == 1
    assert await _status(db, aid) == ("unavailable", None)


@pytest.mark.integration
async def test_execute_writes_each_outcome(db):
    fixed = await _address(db, "unavailable")
    refused = await _address(db, "unavailable", raw="nowhere")
    by_id = {r["id"]: r for r in await fetch_candidates(db, ("unavailable",), 1000)}

    await sweep(db, _FakeNormalizer(_standardized()), [by_id[fixed]], execute=True)
    tally = await sweep(db, _FakeNormalizer(_rejected()), [by_id[refused]], execute=True)

    assert await _status(db, fixed) == ("standardized", FORM)
    assert await _status(db, refused) == ("rejected", None)
    assert tally == {
        "unavailable -> rejected": 1,
        "links_moved": 0,
        "links_dropped": 0,
        "rows_deleted": 0,
    }


@pytest.mark.integration
async def test_oldest_unavailable_reads_the_longest_waiting_row(db):
    await _address(db, "unavailable", datetime(2001, 1, 1, tzinfo=UTC))
    await _address(db, "declined", datetime(1999, 1, 1, tzinfo=UTC))
    assert await oldest_unavailable(db) == datetime(2001, 1, 1, tzinfo=UTC)
