"""Tests for the assignment-relationship window audit (#301)."""

import datetime

import pytest
import pytest_asyncio

from scripts.audit_assignment_relationship_windows import _clamp, audit, classify, run_audit
from src.core.db import generate_id

D = datetime.date


# --------------------------------------------------------------------------- #
# Pure clamp rule (no DB)
# --------------------------------------------------------------------------- #


def test_clamp_raises_defined_start():
    assert _clamp(D(2022, 1, 1), None, D(2023, 1, 1), None) == (D(2023, 1, 1), None)


def test_clamp_never_materializes_unknown_start():
    # NULL valid_from with a defined endpoint start stays NULL (#307 — never invent).
    assert _clamp(None, D(2024, 1, 1), D(2023, 1, 1), D(2024, 12, 31)) == (None, D(2024, 1, 1))


def test_clamp_lowers_defined_end():
    assert _clamp(D(2023, 1, 1), D(2025, 1, 1), None, D(2024, 12, 31)) == (
        D(2023, 1, 1),
        D(2024, 12, 31),
    )


def test_clamp_closes_ongoing_end_at_endpoint():
    # NULL (ongoing) valid_until materializes at a defined endpoint end.
    assert _clamp(D(2023, 1, 1), None, None, D(2024, 12, 31)) == (D(2023, 1, 1), D(2024, 12, 31))


def test_clamp_noop_within_window():
    assert _clamp(D(2023, 6, 1), D(2024, 6, 1), D(2023, 1, 1), D(2024, 12, 31)) == (
        D(2023, 6, 1),
        D(2024, 6, 1),
    )


def test_classify_archived_endpoint_wins():
    row = {
        "endpoint_archived": True,
        "valid_from": None,
        "valid_until": None,
        "lo": None,
        "hi": None,
    }
    assert classify(row)[0] == "archived_endpoint"


def test_classify_inverted():
    row = {
        "endpoint_archived": False,
        "valid_from": D(2024, 6, 1),
        "valid_until": None,
        "lo": None,
        "hi": D(2024, 1, 1),
    }
    assert classify(row)[0] == "inverted"


def test_classify_clamp():
    row = {
        "endpoint_archived": False,
        "valid_from": D(2023, 1, 1),
        "valid_until": D(2025, 1, 1),
        "lo": None,
        "hi": D(2024, 12, 31),
    }
    cat, nf, nu = classify(row)
    assert cat == "clamp" and nu == D(2024, 12, 31)


def test_classify_noop():
    row = {
        "endpoint_archived": False,
        "valid_from": D(2023, 6, 1),
        "valid_until": D(2024, 6, 1),
        "lo": D(2023, 1, 1),
        "hi": D(2024, 12, 31),
    }
    assert classify(row) is None


# --------------------------------------------------------------------------- #
# DB reconcile
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


async def _assignment(conn, start=None, end=None):
    org, person, role, aid = generate_id(), generate_id(), generate_id(), generate_id()
    await conn.execute("INSERT INTO organizations (id) VALUES ($1)", org)
    await conn.execute("INSERT INTO people (id) VALUES ($1)", person)
    await conn.execute(
        "INSERT INTO roles (id, organization_id, title) VALUES ($1,$2,'R')", role, org
    )
    await conn.execute(
        "INSERT INTO role_assignments (id, person_id, role_id, start_date, end_date, is_current)"
        " VALUES ($1,$2,$3,$4,$5,$6)",
        aid,
        person,
        role,
        start,
        end,
        end is None,
    )
    return aid


async def _edge(conn, frm, to, vf=None, vu=None):
    eid = generate_id()
    await conn.execute(
        """INSERT INTO role_assignment_relationships
               (id, from_assignment_id, to_assignment_id, rel_type_id, valid_from, valid_until)
           SELECT $1,$2,$3,id,$4,$5
           FROM role_assignment_relationship_types WHERE slug='staff_of'""",
        eid,
        frm,
        to,
        vf,
        vu,
    )
    return eid


@integration
async def test_reconcile_clamps_and_is_idempotent(conn):
    # observation-path edge whose window outlives the principal's ended seat
    frm = await _assignment(conn, start=D(2023, 1, 1))
    to = await _assignment(conn, start=D(2023, 1, 1), end=D(2024, 12, 31))
    e = await _edge(conn, frm, to, vf=D(2023, 1, 1), vu=D(2030, 1, 1))

    findings = await run_audit(conn, execute=True)
    assert len(findings["clamp"]) == 1
    row = await conn.fetchrow(
        "SELECT valid_until FROM role_assignment_relationships WHERE id=$1", e
    )
    assert row["valid_until"] == D(2024, 12, 31)

    # idempotent: a second pass finds nothing
    again = await audit(conn)
    assert sum(len(v) for v in again.values()) == 0


@integration
async def test_reconcile_archives_on_archived_endpoint(conn):
    frm = await _assignment(conn, start=D(2023, 1, 1))
    to = await _assignment(conn, start=D(2023, 1, 1))
    e = await _edge(conn, frm, to, vf=D(2023, 1, 1))
    await conn.execute("UPDATE role_assignments SET archived_at = NOW() WHERE id=$1", to)

    # NB: the cascade already archived the edge when the endpoint was archived;
    # the audit is the backstop for any edge the cascade missed (e.g. direct SQL).
    await conn.execute("UPDATE role_assignment_relationships SET archived_at = NULL WHERE id=$1", e)
    findings = await run_audit(conn, execute=True)
    assert len(findings["archived_endpoint"]) == 1
    archived = await conn.fetchval(
        "SELECT archived_at FROM role_assignment_relationships WHERE id=$1", e
    )
    assert archived is not None
