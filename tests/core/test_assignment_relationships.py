"""Unit tests for the assignment-relationship observation writer (#301).

Covers refine-in-place, anti-resurrection, retract, provenance gate, identity
immutability, endpoint/rel_type resolution, partial-success batching, and the
admin-path window guard.
"""

import datetime
import hashlib
import os

import pytest
import pytest_asyncio

from src.core.assignment_relationships import (
    EdgeOutsideAssignmentWindow,
    RelationshipClaim,
    RelationshipDisposition,
    RelationshipRejectReason,
    apply_relationship_observations,
    check_edge_within_assignments,
)
from src.core.db import generate_id

pytestmark = [pytest.mark.integration]

D = datetime.date


@pytest_asyncio.fixture(loop_scope="session")
async def conn(db_pool):
    async with db_pool.acquire() as c:
        tr = c.transaction()
        await tr.start()
        try:
            yield c
        finally:
            await tr.rollback()


async def _key(conn):
    uid = generate_id()
    kid = generate_id()
    raw = "pm_" + os.urandom(16).hex()
    await conn.execute("INSERT INTO app_users (id, email) VALUES ($1, $2)", uid, f"{uid}@t.com")
    await conn.execute(
        "INSERT INTO api_keys (id, user_id, label, key_prefix, key_hash) VALUES ($1,$2,$3,$4,$5)",
        kid,
        uid,
        "T",
        raw[:8],
        hashlib.sha256(raw.encode()).hexdigest(),
    )
    return kid


async def _assignment(conn, start=None, end=None, is_current=False):
    org = generate_id()
    person = generate_id()
    role = generate_id()
    aid = generate_id()
    await conn.execute("INSERT INTO organizations (id) VALUES ($1)", org)
    await conn.execute("INSERT INTO people (id) VALUES ($1)", person)
    await conn.execute(
        "INSERT INTO roles (id, organization_id, title) VALUES ($1,$2,$3)", role, org, "R"
    )
    await conn.execute(
        """INSERT INTO role_assignments (id, person_id, role_id, start_date, end_date, is_current)
           VALUES ($1,$2,$3,$4,$5,$6)""",
        aid,
        person,
        role,
        start,
        end,
        is_current,
    )
    return aid


async def _pair(conn, **kw):
    return await _assignment(conn, **kw), await _assignment(conn, **kw)


def _observe(frm, to, **kw):
    return RelationshipClaim(from_pm_assignment_id=frm, to_pm_assignment_id=to, **kw)


async def _one(conn, key, claim):
    (res,) = await apply_relationship_observations(conn, key, [claim])
    return res


# --------------------------------------------------------------------------- #
# Create / refine
# --------------------------------------------------------------------------- #


async def test_create_new(conn):
    frm, to = await _pair(conn)
    res = await _one(conn, None, _observe(frm, to, valid_from=D(2023, 1, 1)))
    assert res.disposition is RelationshipDisposition.NEW
    assert res.relationship_id


async def test_reobserve_same_payload_is_noop(conn):
    frm, to = await _pair(conn)
    r1 = await _one(conn, None, _observe(frm, to, valid_from=D(2023, 1, 1)))
    r2 = await _one(conn, None, _observe(frm, to, valid_from=D(2023, 1, 1)))
    assert r2.disposition is RelationshipDisposition.AUTO_ATTACHED
    assert r2.relationship_id == r1.relationship_id
    assert r2.attached_archived is False  # #477: live row, not an archived twin


async def test_reobserve_changed_payload_updates(conn):
    frm, to = await _pair(conn)
    r1 = await _one(conn, None, _observe(frm, to, valid_from=D(2023, 1, 1)))
    r2 = await _one(conn, None, _observe(frm, to, valid_from=D(2023, 1, 1), notes="added"))
    assert r2.disposition is RelationshipDisposition.UPDATED
    assert r2.relationship_id == r1.relationship_id
    notes = await conn.fetchval(
        "SELECT notes FROM role_assignment_relationships WHERE id=$1", r1.relationship_id
    )
    assert notes == "added"


async def test_pm_id_refine_in_place(conn):
    frm, to = await _pair(conn)
    r1 = await _one(conn, None, _observe(frm, to))
    r2 = await _one(
        conn,
        None,
        RelationshipClaim(pm_relationship_id=r1.relationship_id, valid_until=D(2024, 12, 31)),
    )
    assert r2.disposition is RelationshipDisposition.UPDATED
    assert r2.relationship_id == r1.relationship_id


# --------------------------------------------------------------------------- #
# Rejections
# --------------------------------------------------------------------------- #


async def test_assignment_unresolved(conn):
    to = await _assignment(conn)
    res = await _one(conn, None, _observe(generate_id(), to))
    assert res.disposition is RelationshipDisposition.REJECTED
    assert res.reason == RelationshipRejectReason.ASSIGNMENT_UNRESOLVED


async def test_self_relationship(conn):
    a = await _assignment(conn)
    res = await _one(conn, None, _observe(a, a))
    assert res.reason == RelationshipRejectReason.SELF_RELATIONSHIP


async def test_rel_type_unknown(conn):
    frm, to = await _pair(conn)
    res = await _one(conn, None, _observe(frm, to, rel_type="not_a_type"))
    assert res.reason == RelationshipRejectReason.REL_TYPE_UNKNOWN


async def test_invalid_window(conn):
    frm, to = await _pair(conn)
    res = await _one(
        conn, None, _observe(frm, to, valid_from=D(2024, 1, 1), valid_until=D(2023, 1, 1))
    )
    assert res.reason == RelationshipRejectReason.INVALID


async def test_pm_id_refine_identity_immutable(conn):
    frm, to = await _pair(conn)
    other = await _assignment(conn)
    r1 = await _one(conn, None, _observe(frm, to))
    res = await _one(
        conn,
        None,
        RelationshipClaim(pm_relationship_id=r1.relationship_id, to_pm_assignment_id=other),
    )
    assert res.reason == RelationshipRejectReason.IDENTITY_IMMUTABLE


async def test_provenance_conflict(conn):
    frm, to = await _pair(conn)
    k1, k2 = await _key(conn), await _key(conn)
    r1 = await _one(conn, k1, _observe(frm, to, valid_from=D(2023, 1, 1)))
    res = await _one(conn, k2, _observe(frm, to, valid_from=D(2023, 6, 1)))
    assert res.reason == RelationshipRejectReason.PROVENANCE_CONFLICT
    assert res.disposition is RelationshipDisposition.REJECTED
    # r1 unchanged
    vf = await conn.fetchval(
        "SELECT valid_from FROM role_assignment_relationships WHERE id=$1", r1.relationship_id
    )
    assert vf == D(2023, 1, 1)


# --------------------------------------------------------------------------- #
# Retract + anti-resurrection
# --------------------------------------------------------------------------- #


async def test_retract(conn):
    frm, to = await _pair(conn)
    r1 = await _one(conn, None, _observe(frm, to))
    res = await _one(
        conn, None, RelationshipClaim(pm_relationship_id=r1.relationship_id, op="retract")
    )
    assert res.disposition is RelationshipDisposition.RETRACTED
    archived = await conn.fetchval(
        "SELECT archived_at FROM role_assignment_relationships WHERE id=$1", r1.relationship_id
    )
    assert archived is not None


async def test_retract_already_archived_noop(conn):
    frm, to = await _pair(conn)
    r1 = await _one(conn, None, _observe(frm, to))
    await _one(conn, None, RelationshipClaim(pm_relationship_id=r1.relationship_id, op="retract"))
    res = await _one(
        conn, None, RelationshipClaim(pm_relationship_id=r1.relationship_id, op="retract")
    )
    assert res.disposition is RelationshipDisposition.AUTO_ATTACHED
    assert res.attached_archived is True  # #477: the edge addressed is retracted


async def test_retract_requires_id(conn):
    frm, to = await _pair(conn)
    res = await _one(conn, None, _observe(frm, to, op="retract"))
    assert res.reason == RelationshipRejectReason.INVALID


async def test_retract_not_found(conn):
    res = await _one(conn, None, RelationshipClaim(pm_relationship_id=generate_id(), op="retract"))
    assert res.reason == RelationshipRejectReason.RELATIONSHIP_NOT_FOUND


async def test_anti_resurrection(conn):
    frm, to = await _pair(conn)
    r1 = await _one(conn, None, _observe(frm, to))
    await _one(conn, None, RelationshipClaim(pm_relationship_id=r1.relationship_id, op="retract"))
    # re-observing the same identity auto-attaches to the archived row, not revive
    res = await _one(conn, None, _observe(frm, to))
    assert res.disposition is RelationshipDisposition.AUTO_ATTACHED
    assert res.relationship_id == r1.relationship_id
    assert res.attached_archived is True  # #477: anti-resurrection attach, labelled
    active = await conn.fetchval(
        """SELECT count(*) FROM role_assignment_relationships
           WHERE from_assignment_id=$1 AND to_assignment_id=$2 AND archived_at IS NULL""",
        frm,
        to,
    )
    assert active == 0


# --------------------------------------------------------------------------- #
# Partial success
# --------------------------------------------------------------------------- #


async def test_partial_success(conn):
    frm, to = await _pair(conn)
    claims = [
        _observe(frm, to),  # new
        _observe(frm, frm),  # self → rejected
        _observe(frm, to),  # auto-attached (identity now exists)
    ]
    results = await apply_relationship_observations(conn, None, claims)
    assert [r.disposition for r in results] == [
        RelationshipDisposition.NEW,
        RelationshipDisposition.REJECTED,
        RelationshipDisposition.AUTO_ATTACHED,
    ]
    assert results[1].reason == RelationshipRejectReason.SELF_RELATIONSHIP


# --------------------------------------------------------------------------- #
# Admin-path window guard
# --------------------------------------------------------------------------- #


async def test_guard_rejects_out_of_window(conn):
    frm = await _assignment(conn, start=D(2023, 1, 1), end=D(2024, 12, 31))
    to = await _assignment(conn, start=D(2023, 1, 1), end=D(2024, 12, 31))
    with pytest.raises(EdgeOutsideAssignmentWindow):
        await check_edge_within_assignments(conn, frm, to, D(2022, 1, 1), None)
    with pytest.raises(EdgeOutsideAssignmentWindow):
        await check_edge_within_assignments(conn, frm, to, None, D(2025, 6, 1))


async def test_guard_allows_within_window(conn):
    frm = await _assignment(conn, start=D(2023, 1, 1), end=D(2024, 12, 31))
    to = await _assignment(conn, start=D(2023, 1, 1), end=D(2024, 12, 31))
    await check_edge_within_assignments(conn, frm, to, D(2023, 6, 1), D(2024, 6, 1))
    # NULL bounds are lenient
    await check_edge_within_assignments(conn, frm, to, None, None)


async def test_guard_observation_path_records_freely(conn):
    """The observation writer does NOT enforce the window — out-of-window records."""
    frm = await _assignment(conn, start=D(2023, 1, 1), end=D(2024, 12, 31))
    to = await _assignment(conn, start=D(2023, 1, 1), end=D(2024, 12, 31))
    res = await _one(conn, None, _observe(frm, to, valid_from=D(2020, 1, 1)))
    assert res.disposition is RelationshipDisposition.NEW
