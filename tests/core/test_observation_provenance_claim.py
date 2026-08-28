"""Hermetic branch guards for the id-addressed provenance claim (#478).

``update_assignment_fields`` short-circuits an identical redelivery **before** the
authority gate (CR round 1, #311) so a foreign key cannot alter an owned row by
agreeing with it. That short-circuit also skipped the ``COALESCE`` that claims an
*unowned* row, so ``source_key_id`` could only ever be claimed as a side effect of
a value change — leaving 6,698 pre-#311 assignments unclaimable without
falsifying a date (#478).

Behaviour against a real database (the #327 touch-trigger emit, the ``updated_at``
bump, the sibling unique index) lives in the integration tier. These pin the
*decision table* with a stub connection so the branch itself is covered by the
hermetic suite, where a regression shows up on every commit.
"""

from datetime import date

import pytest

from src.core.observation import ObservationRejected, update_assignment_fields

_RA_ID = "01RAASSIGNMENT0000000000000"
_MINE = "01KEYMINE000000000000000000"
_THEIRS = "01KEYTHEIRS00000000000000000"


class _StubConn:
    """Minimal asyncpg-connection stand-in: one canned row, recorded executes."""

    def __init__(self, row):
        self._row = row
        self.executed: list[tuple[str, tuple]] = []

    async def fetchrow(self, _sql, *_args):
        return self._row

    async def execute(self, sql, *args):
        self.executed.append((sql, args))


def _row(*, start=date(2013, 1, 14), end=None, is_current=True, source_key_id=None):
    return {
        "start_date": start,
        "end_date": end,
        "is_current": is_current,
        "source_key_id": source_key_id,
    }


# ---------------------------------------------------------------------------
# Identical assertion — the #478 case
# ---------------------------------------------------------------------------


async def test_identical_assertion_claims_an_unowned_row():
    """The whole point of #478: agreeing with an unowned row claims it."""
    conn = _StubConn(_row(source_key_id=None))

    claimed = await update_assignment_fields(
        conn, _RA_ID, start_date=date(2013, 1, 14), source_key_id=_MINE
    )

    assert claimed is True
    assert len(conn.executed) == 1
    sql, args = conn.executed[0]
    # Provenance only — the bounds are already right, and rewriting them would
    # risk `uq_role_assignment_person_role_start` for no gain.
    assert "source_key_id" in sql
    assert "start_date=" not in sql
    assert args == (_RA_ID, _MINE)


async def test_identical_assertion_on_own_row_is_a_quiet_noop():
    """Already ours — nothing to claim, no clock bump, no outbox row."""
    conn = _StubConn(_row(source_key_id=_MINE))

    claimed = await update_assignment_fields(
        conn, _RA_ID, start_date=date(2013, 1, 14), source_key_id=_MINE
    )

    assert claimed is False
    assert conn.executed == []


async def test_identical_assertion_by_a_foreign_key_is_a_quiet_noop():
    """CR round 1 (#311) preserved: agreement never lets a foreign key touch an owned row."""
    conn = _StubConn(_row(source_key_id=_THEIRS))

    claimed = await update_assignment_fields(
        conn, _RA_ID, start_date=date(2013, 1, 14), source_key_id=_MINE
    )

    assert claimed is False
    assert conn.executed == []


async def test_identical_assertion_without_a_key_claims_nothing():
    """A keyless core caller must not blank-stamp; COALESCE(NULL, NULL) is a no-write."""
    conn = _StubConn(_row(source_key_id=None))

    claimed = await update_assignment_fields(
        conn, _RA_ID, start_date=date(2013, 1, 14), source_key_id=None
    )

    assert claimed is False
    assert conn.executed == []


# ---------------------------------------------------------------------------
# Differing assertion — the pre-existing path, now reporting the same signal
# ---------------------------------------------------------------------------


async def test_differing_assertion_on_an_unowned_row_reports_the_claim():
    """The COALESCE already claimed here; #478 only makes it say so."""
    conn = _StubConn(_row(source_key_id=None))

    claimed = await update_assignment_fields(
        conn, _RA_ID, start_date=date(2014, 1, 1), source_key_id=_MINE
    )

    assert claimed is True
    assert len(conn.executed) == 1
    assert "start_date=" in conn.executed[0][0]


async def test_differing_assertion_on_own_row_claims_nothing():
    """Same source: the row changed, but provenance did not."""
    conn = _StubConn(_row(source_key_id=_MINE))

    claimed = await update_assignment_fields(
        conn, _RA_ID, start_date=date(2014, 1, 1), source_key_id=_MINE
    )

    assert claimed is False
    assert len(conn.executed) == 1


async def test_differing_assertion_by_a_foreign_key_still_rejects():
    """The authority gate is untouched — disagreement is still `source_key_mismatch`."""
    conn = _StubConn(_row(source_key_id=_THEIRS))

    with pytest.raises(ObservationRejected, match="source_key_mismatch"):
        await update_assignment_fields(
            conn, _RA_ID, start_date=date(2014, 1, 1), source_key_id=_MINE
        )
    assert conn.executed == []
