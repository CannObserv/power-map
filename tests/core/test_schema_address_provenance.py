"""addresses.standardization_status + the address touch trigger (#595).

Two parts:

- **Rollback-wrapped:** the CHECK vocabulary, and the AFTER UPDATE trigger that
  touches every parent serving an address — the entities linked through
  ``entity_addresses`` and the events whose place it is — so their ETags move
  when a sweep re-standardizes the row. The observable is ``row_version``
  (``updated_at`` is frozen inside one transaction).
- **Committed:** the backfill runs inside ``apply_schema``, so its test commits
  legacy rows (status NULL), re-applies the schema, and cleans up after itself.
"""

import hashlib
import json

import asyncpg
import pytest
import pytest_asyncio

from src.core.db import apply_schema, generate_id
from src.core.normalizers.address import STANDARDIZATION_STATUSES
from tests.db_utils import row_version

pytestmark = [pytest.mark.integration]


@pytest_asyncio.fixture(loop_scope="session")
async def db(db_pool):
    async with db_pool.acquire() as conn:
        tr = conn.transaction()
        await tr.start()
        try:
            yield conn
        finally:
            await tr.rollback()


async def _address(db, **cols) -> str:
    aid = generate_id()
    cols = {"country": "US", **cols}
    names = ", ".join(["id", *cols])
    marks = ", ".join(f"${i}" for i in range(1, len(cols) + 2))
    await db.execute(f"INSERT INTO addresses ({names}) VALUES ({marks})", aid, *cols.values())
    return aid


async def _link(db, entity_type: str, entity_id: str, aid: str) -> None:
    await db.execute(
        "INSERT INTO entity_addresses (id, entity_type, entity_id, address_id, address_type)"
        " VALUES ($1, $2, $3, $4, 'mailing')",
        generate_id(),
        entity_type,
        entity_id,
        aid,
    )


async def _org(db) -> str:
    oid = generate_id()
    await db.execute("INSERT INTO organizations (id) VALUES ($1)", oid)
    return oid


# ── CHECK vocabulary ─────────────────────────────────────────────────────────


@pytest.mark.parametrize("status", [*STANDARDIZATION_STATUSES, None])
async def test_check_accepts_each_status_and_null(db, status):
    aid = await _address(db, raw_input="1 Main St", standardization_status=status)
    assert (
        await db.fetchval("SELECT standardization_status FROM addresses WHERE id=$1", aid) == status
    )


async def test_check_rejects_an_unknown_status(db):
    with pytest.raises(asyncpg.CheckViolationError):
        async with db.transaction():
            await _address(db, raw_input="1 Main St", standardization_status="bogus")


async def test_checked_at_column_exists(db):
    aid = await _address(db, raw_input="1 Main St")
    await db.execute("UPDATE addresses SET standardization_checked_at = NOW() WHERE id=$1", aid)
    assert await db.fetchval(
        "SELECT standardization_checked_at IS NOT NULL FROM addresses WHERE id=$1", aid
    )


# ── touch trigger ────────────────────────────────────────────────────────────


async def _role_and_assignment(db) -> tuple[str, str]:
    oid, rid, pid, raid = (generate_id() for _ in range(4))
    await db.execute("INSERT INTO organizations (id) VALUES ($1)", oid)
    await db.execute(
        "INSERT INTO roles (id, organization_id, title) VALUES ($1, $2, 'Director')", rid, oid
    )
    await db.execute("INSERT INTO people (id) VALUES ($1)", pid)
    await db.execute(
        "INSERT INTO role_assignments (id, person_id, role_id, is_current)"
        " VALUES ($1, $2, $3, TRUE)",
        raid,
        pid,
        rid,
    )
    return rid, raid


async def test_restandardizing_touches_every_linked_parent(db):
    """Role + assignment details serve `standardized`; their ETags key on the parent."""
    aid = await _address(db, raw_input="1 Main St Seattle WA")
    oid = await _org(db)
    rid, raid = await _role_and_assignment(db)
    for entity_type, eid in (("organization", oid), ("role", rid), ("role_assignment", raid)):
        await _link(db, entity_type, eid, aid)
    parents = (("organizations", oid), ("roles", rid), ("role_assignments", raid))
    before = {t: await row_version(db, t, i) for t, i in parents}

    await db.execute(
        "UPDATE addresses SET standardized='1 MAIN ST SEATTLE WA 98101' WHERE id=$1", aid
    )

    for table, eid in parents:
        assert await row_version(db, table, eid) != before[table], table


async def test_restandardizing_touches_events_that_use_it_as_a_place(db):
    """Event lists serve `event_place_address.standardized`, versioned on the event rows."""
    oid = await _org(db)
    aid = await _address(db, raw_input="Olympia WA", city="Olympia", region="WA")
    type_id = await db.fetchval("SELECT id FROM entity_event_types ORDER BY slug LIMIT 1")
    evid = generate_id()
    await db.execute(
        "INSERT INTO entity_events"
        " (id, entity_type, entity_id, event_type_id, event_year, event_place_address_id)"
        " VALUES ($1, 'organization', $2, $3, 2001, $4)",
        evid,
        oid,
        type_id,
        aid,
    )
    ctid = "SELECT ctid::text FROM entity_events WHERE id=$1"  # row_version's observable
    before = await db.fetchval(ctid, evid)
    await db.execute("UPDATE addresses SET standardized='OLYMPIA WA' WHERE id=$1", aid)
    assert await db.fetchval(ctid, evid) != before


async def test_status_only_update_touches_no_parent(db):
    """A retry that is still unavailable, or the backfill, must not move any ETag."""
    aid = await _address(db, raw_input="1 Main St")
    oid = await _org(db)
    await _link(db, "organization", oid, aid)
    before = await row_version(db, "organizations", oid)
    await db.execute(
        "UPDATE addresses SET standardization_status='unavailable',"
        " standardization_checked_at=NOW() WHERE id=$1",
        aid,
    )
    assert await row_version(db, "organizations", oid) == before


async def test_same_value_update_touches_no_parent(db):
    aid = await _address(db, raw_input="1 Main St", standardized="1 MAIN ST")
    oid = await _org(db)
    await _link(db, "organization", oid, aid)
    before = await row_version(db, "organizations", oid)
    await db.execute("UPDATE addresses SET standardized='1 MAIN ST' WHERE id=$1", aid)
    assert await row_version(db, "organizations", oid) == before


# ── backfill (committed; runs inside apply_schema) ───────────────────────────


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


async def test_apply_schema_backfills_legacy_rows_from_field_confidence(db_pool):
    """Each legacy (status NULL) row gets the status its import recorded.

    A pre-#589 local fallback (usaddress, no marker) is ambiguous, so it is marked
    ``unavailable`` for one classifying retry. An already-set status is untouched,
    and so is a row with no matching assessment. (That the backfill touches no
    parent is ``test_status_only_update_touches_no_parent``'s claim: an org's
    ``updated_at`` can't show it here, since #201's search_tsv backfill re-touches
    every org on every apply.)
    """
    oid = generate_id()
    usps, local = {"provider": "usps"}, {"provider": "usaddress", "status": "not_attempted"}
    marked = {"provider": "usaddress", "fallback": "rejected"}
    google = {"provider": "google", "status": "not_found"}
    cases = {
        # key: (address columns, field_confidence detail or None, expected status)
        "std": ({"raw_input": "1 A St", "standardized": "1 A ST"}, usps, "standardized"),
        "std_no_fc": ({"raw_input": "2 B St", "standardized": "2 B ST"}, None, "standardized"),
        "pre589": ({"raw_input": "3 C St"}, local, "unavailable"),
        "marked": ({"raw_input": "4 D St"}, marked, "rejected"),
        "not_found": ({"raw_input": "5 E St"}, google, "no_result"),
        "unknown": ({"raw_input": "6 F St"}, None, None),
        "declined": (
            {"raw_input": "7 G St", "standardization_status": "declined"},
            usps,
            "declined",
        ),
    }
    ids: dict[str, str] = {}
    async with db_pool.acquire() as conn:
        try:
            await conn.execute("INSERT INTO organizations (id) VALUES ($1)", oid)
            for key, (cols, detail, _) in cases.items():
                ids[key] = await _address(conn, **cols)
                await _link(conn, "organization", oid, ids[key])
                if detail is not None:
                    await conn.execute(
                        "INSERT INTO field_confidence (id, entity_type, entity_id, field_name,"
                        "  value_hash, source_reliability, validation_status, validation_detail,"
                        "  assessed_at)"
                        " VALUES ($1, 'organization', $2, 'address', $3, 0.8, 'not_attempted',"
                        "  $4, '2026-06-15T22:33:00Z')",
                        generate_id(),
                        oid,
                        _hash(cols.get("standardized") or cols["raw_input"]),
                        json.dumps(detail),
                    )

            await apply_schema(conn)

            rows = {
                r["id"]: r
                for r in await conn.fetch(
                    "SELECT id, standardization_status, standardization_checked_at, created_at"
                    " FROM addresses WHERE id = ANY($1)",
                    list(ids.values()),
                )
            }
        finally:
            await conn.execute("DELETE FROM field_confidence WHERE entity_id=$1", oid)
            await conn.execute("DELETE FROM entity_addresses WHERE entity_id=$1", oid)
            await conn.execute("DELETE FROM addresses WHERE id = ANY($1)", list(ids.values()))
            await conn.execute("DELETE FROM organizations WHERE id=$1", oid)

    for key, (_, detail, expected) in cases.items():
        assert rows[ids[key]]["standardization_status"] == expected, key
    # checked_at: the assessment's time where one matched, else the row's creation.
    assert (
        rows[ids["pre589"]]["standardization_checked_at"].isoformat().startswith("2026-06-15T22:33")
    )
    assert (
        rows[ids["std_no_fc"]]["standardization_checked_at"] == rows[ids["std_no_fc"]]["created_at"]
    )
    assert rows[ids["unknown"]]["standardization_checked_at"] is None
