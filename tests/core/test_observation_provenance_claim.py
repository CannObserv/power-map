"""Hermetic branch guards for the id-addressed provenance claim (#478).

``update_assignment_fields`` short-circuits an identical redelivery **before** the
authority gate (CR round 1, #311) so a foreign key cannot alter an owned row by
agreeing with it. That short-circuit also skipped the ``COALESCE`` that claims an
*unowned* row, so ``source_key_id`` could only ever be claimed as a side effect of
a value change — leaving 6,698 pre-#311 assignments unclaimable without
falsifying a date (#478).

Behaviour against a real database (the #327 touch-trigger emit, the sibling
unique index) lives in the integration tier. These pin the *decision table* with
a stub connection so the branch itself is covered by the hermetic suite, where a
regression shows up on every commit.

The ``updated_at`` bump is deliberately pinned nowhere: ``set_updated_at()``
writes ``now()``, which is transaction-start time, and the integration fixtures
run a whole test in one transaction — so the column is constant there no matter
what the code does. The outbox row is the observable that actually moves.
"""

from datetime import date

import pytest

from src.core.observation import ObservationRejected, update_assignment_fields

_RA_ID = "01RAASSIGNMENT0000000000000"
_MINE = "01KEYMINE000000000000000000"
_THEIRS = "01KEYTHEIRS00000000000000000"


class _StubConn:
    """Minimal asyncpg-connection stand-in: one canned row, recorded writes.

    Both UPDATEs go through ``fetchval`` and end in ``RETURNING source_key_id``,
    so the stub answers them the way ``COALESCE`` / the ``source_key_id IS NULL``
    predicate would: the stored owner if there is one, else the supplied key.
    ``lost_race=True`` models the row being claimed (or archived) between the
    SELECT and the UPDATE — the predicate matches nothing and PostgreSQL returns
    no row.
    """

    def __init__(self, row, *, lost_race: bool = False, now_owned_by=None, gone=False):
        self._row = row
        self._lost_race = lost_race
        self._now_owned_by = now_owned_by  # the winner of a lost race, for the diagnostic read
        self._gone = gone  # the row was archived/deleted between read and write
        self.executed: list[tuple[str, tuple]] = []

    async def fetchrow(self, sql, *args):
        if not sql.lstrip().startswith("UPDATE"):
            # Either the opening SELECT, or the diagnostic read the bounds path
            # makes when its UPDATE matched nothing (#478 CR).
            if self._lost_race and self.executed:
                if self._gone:
                    return None
                return {"archived_at": None, "source_key_id": self._now_owned_by}
            return self._row
        self.executed.append((sql, args))
        assert sql.rstrip().endswith("RETURNING id, source_key_id")
        if self._lost_race:
            return None  # predicate matched nothing: archived, or claimed since the SELECT
        stored = self._row["source_key_id"]
        return {"id": _RA_ID, "source_key_id": stored if stored is not None else args[4]}

    async def fetchval(self, sql, *args):
        self.executed.append((sql, args))
        assert sql.rstrip().endswith("RETURNING source_key_id")
        if self._lost_race:
            return None
        stored = self._row["source_key_id"]
        return stored if stored is not None else args[1]


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


async def test_identical_assertion_reports_nothing_when_the_claim_loses_a_race():
    """`provenance_claimed: true` must mean the caller *owns* the row.

    The UPDATE's `source_key_id IS NULL` predicate is re-evaluated after the row
    lock, so a claim committed between the SELECT and the UPDATE wins and ours
    matches nothing. Reporting the stale read here would tell a producer it owns
    a row another key holds.
    """
    conn = _StubConn(_row(source_key_id=None), lost_race=True)

    claimed = await update_assignment_fields(
        conn, _RA_ID, start_date=date(2013, 1, 14), source_key_id=_MINE
    )

    assert claimed is False


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


# ---------------------------------------------------------------------------
# The bounds UPDATE enforces authority in the write, not from the stale read
# (#478 CR findings 8 and 9)
# ---------------------------------------------------------------------------


async def test_bounds_update_rejects_when_another_key_claimed_the_row_mid_flight():
    """The gate reads `source_key_id` before the UPDATE, so it can go stale.

    A key that claims the row between that read and the write would otherwise
    have this producer's bounds written over its own: the pre-read said NULL, and
    `COALESCE` would keep the winner's key while the bounds still landed. The
    predicate on the UPDATE is what actually enforces the gate; the diagnostic
    read only decides which rejection to name.
    """
    conn = _StubConn(_row(source_key_id=None), lost_race=True, now_owned_by=_THEIRS)

    with pytest.raises(ObservationRejected) as exc:
        await update_assignment_fields(
            conn, _RA_ID, start_date=date(2020, 1, 1), source_key_id=_MINE
        )

    assert exc.value.detail == "source_key_mismatch"
    sql, _ = conn.executed[0]
    assert "source_key_id IS NULL OR source_key_id=$5" in sql


async def test_bounds_update_rejects_when_the_row_vanished_mid_flight():
    """Zero rows matched means nothing was written — never report success.

    Before this the UPDATE ignored its own result, so a row archived between the
    read and the write produced a cheerful `Updated role_assignment ...` log and
    an `auto-attached` response for a write that never landed.
    """
    conn = _StubConn(_row(source_key_id=None), lost_race=True, gone=True)

    with pytest.raises(ObservationRejected) as exc:
        await update_assignment_fields(
            conn, _RA_ID, start_date=date(2020, 1, 1), source_key_id=_MINE
        )

    assert exc.value.detail == "assignment_not_found"
