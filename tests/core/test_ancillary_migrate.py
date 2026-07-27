"""Integration tests for src.core.ancillary_migrate (#324).

Merge dedup hard-deletes duplicate role_assignments. Their polymorphic
ancillary — links / contact_methods / field_confidence / identifiers, keyed on
(entity_type='role_assignment', entity_id=<assignment_id>) with no FK — would be
silently orphaned unless re-homed onto the surviving assignment first. These
tests pin the re-point-or-dedup contract and the survivor outbox signal.
"""

import pytest
import pytest_asyncio

from src.core.ancillary_migrate import (
    count_orphaned_role_assignment_ancillary,
    migrate_role_assignment_ancillary,
    rehome_conflicting_assignment_ancillary,
)
from src.core.db import generate_id

pytestmark = [pytest.mark.integration]

_LINK_TYPE_ID = "01KKZ3WGJRPV2TDZV672NWFE8G"  # 'twitter'
_RA_IDTYPE_ID = "01KKZ3WGJSZF0F96SMYC000AVV"  # 'role_wa_pdc'


@pytest_asyncio.fixture(loop_scope="session")
async def db(db_pool):
    async with db_pool.acquire() as conn:
        tr = conn.transaction()
        await tr.start()
        try:
            yield conn
        finally:
            await tr.rollback()


async def _assignment(db) -> str:
    """A minimal person + org + role + role_assignment; returns the assignment id."""
    oid, pid, rid, aid = (generate_id() for _ in range(4))
    await db.execute("INSERT INTO organizations (id) VALUES ($1)", oid)
    await db.execute("INSERT INTO people (id) VALUES ($1)", pid)
    await db.execute(
        "INSERT INTO roles (id, organization_id, title) VALUES ($1, $2, 'Member')", rid, oid
    )
    await db.execute(
        "INSERT INTO role_assignments (id, person_id, role_id) VALUES ($1, $2, $3)", aid, pid, rid
    )
    return aid


async def _add_link(db, aid, url):
    lid = generate_id()
    await db.execute(
        "INSERT INTO links (id, entity_type, entity_id, url, link_type_id)"
        " VALUES ($1, 'role_assignment', $2, $3, $4)",
        lid,
        aid,
        url,
        _LINK_TYPE_ID,
    )
    return lid


async def _add_contact(db, aid, value):
    cid = generate_id()
    await db.execute(
        "INSERT INTO contact_methods (id, entity_type, entity_id, contact_type, value)"
        " VALUES ($1, 'role_assignment', $2, 'email', $3)",
        cid,
        aid,
        value,
    )
    return cid


async def _add_fc(db, aid, field_name, value_hash):
    fid = generate_id()
    await db.execute(
        "INSERT INTO field_confidence"
        " (id, entity_type, entity_id, field_name, value_hash, source_reliability,"
        "  validation_status)"
        " VALUES ($1, 'role_assignment', $2, $3, $4, 0.5, 'unconfirmed')",
        fid,
        aid,
        field_name,
        value_hash,
    )
    return fid


async def _add_identifier(db, aid, value):
    iid = generate_id()
    await db.execute(
        "INSERT INTO identifiers (id, entity_id, entity_identifier_type_id, value)"
        " VALUES ($1, $2, $3, $4)",
        iid,
        aid,
        _RA_IDTYPE_ID,
        value,
    )
    return iid


async def _entity_id(db, table, row_id):
    return await db.fetchval(f"SELECT entity_id FROM {table} WHERE id=$1", row_id)


# ── migrate_role_assignment_ancillary ───────────────────────────────────────


async def test_repoints_all_four_tables_when_survivor_lacks_them(db):
    loser, winner = await _assignment(db), await _assignment(db)
    lid = await _add_link(db, loser, "https://leg.wa.gov/x")
    cid = await _add_contact(db, loser, "alex.ramel@leg.wa.gov")
    fid = await _add_fc(db, loser, "url", "abc123")
    iid = await _add_identifier(db, loser, "PDC-123")

    counts = await migrate_role_assignment_ancillary(db, loser, winner)

    assert await _entity_id(db, "links", lid) == winner
    assert await _entity_id(db, "contact_methods", cid) == winner
    assert await _entity_id(db, "field_confidence", fid) == winner
    assert await _entity_id(db, "identifiers", iid) == winner
    assert counts["links"] == (1, 0)
    assert counts["contact_methods"] == (1, 0)
    assert counts["field_confidence"] == (1, 0)
    assert counts["identifiers"] == (1, 0)


async def test_dedups_when_survivor_already_has_identical_row(db):
    loser, winner = await _assignment(db), await _assignment(db)
    lid_loser = await _add_link(db, loser, "https://dup.example/board")
    await _add_link(db, winner, "https://dup.example/board")  # identical on survivor
    await _add_identifier(db, loser, "PDC-DUP")
    await _add_identifier(db, winner, "PDC-DUP")

    counts = await migrate_role_assignment_ancillary(db, loser, winner)

    # loser's duplicate row is deleted, not re-pointed (would violate identity)
    assert await db.fetchval("SELECT 1 FROM links WHERE id=$1", lid_loser) is None
    winner_links = await db.fetchval(
        "SELECT count(*) FROM links WHERE entity_type='role_assignment' AND entity_id=$1", winner
    )
    assert winner_links == 1
    assert counts["links"] == (0, 1)
    assert counts["identifiers"] == (0, 1)


async def test_identifier_scoped_to_role_assignment_type_only(db):
    """A person-typed identifier on the same id is not treated as ra ancillary."""
    loser, winner = await _assignment(db), await _assignment(db)
    # person_wa_pdc is a person-scoped identifier type — must be ignored.
    person_type = await db.fetchval(
        "SELECT id FROM entity_identifier_types WHERE entity_type='person' LIMIT 1"
    )
    stray = generate_id()
    await db.execute(
        "INSERT INTO identifiers (id, entity_id, entity_identifier_type_id, value)"
        " VALUES ($1, $2, $3, 'STRAY')",
        stray,
        loser,
        person_type,
    )

    counts = await migrate_role_assignment_ancillary(db, loser, winner)

    assert counts["identifiers"] == (0, 0)
    assert await _entity_id(db, "identifiers", stray) == loser  # untouched


# ── rehome_conflicting_assignment_ancillary (batch + outbox) ────────────────


async def test_batch_rehomes_pairs_and_emits_survivor_outbox(db):
    loser, winner = await _assignment(db), await _assignment(db)
    await _add_contact(db, loser, "melanie.morgan@leg.wa.gov")

    before = await db.fetchval(
        "SELECT count(*) FROM entity_changes"
        " WHERE entity_type='role_assignment' AND entity_id=$1 AND change_kind='updated'",
        winner,
    )
    await rehome_conflicting_assignment_ancillary(db, [(loser, winner)])
    after = await db.fetchval(
        "SELECT count(*) FROM entity_changes"
        " WHERE entity_type='role_assignment' AND entity_id=$1 AND change_kind='updated'",
        winner,
    )

    assert await db.fetchval(
        "SELECT count(*) FROM contact_methods WHERE entity_type='role_assignment' AND entity_id=$1",
        winner,
    )
    assert after == before + 1  # one survivor 'updated' signal


async def test_batch_no_outbox_when_only_dedup(db):
    """Pure dedup (nothing moved) leaves the survivor row unchanged — no signal."""
    loser, winner = await _assignment(db), await _assignment(db)
    await _add_contact(db, loser, "michelle.caldier@leg.wa.gov")
    await _add_contact(db, winner, "michelle.caldier@leg.wa.gov")

    before = await db.fetchval(
        "SELECT count(*) FROM entity_changes"
        " WHERE entity_type='role_assignment' AND entity_id=$1 AND change_kind='updated'",
        winner,
    )
    await rehome_conflicting_assignment_ancillary(db, [(loser, winner)])
    after = await db.fetchval(
        "SELECT count(*) FROM entity_changes"
        " WHERE entity_type='role_assignment' AND entity_id=$1 AND change_kind='updated'",
        winner,
    )

    assert after == before  # nothing re-pointed → no outbox bump


# ── count_orphaned_role_assignment_ancillary (guard) ────────────────────────


async def test_guard_counts_orphans_after_raw_delete(db):
    aid = await _assignment(db)
    await _add_link(db, aid, "https://orphan.example/x")
    await _add_contact(db, aid, "orphan@leg.wa.gov")
    await _add_fc(db, aid, "url", "orphanhash")
    await _add_identifier(db, aid, "PDC-ORPH")

    before = await count_orphaned_role_assignment_ancillary(db)
    # Hard-delete the assignment out from under the ancillary (the #324 hazard).
    await db.execute("DELETE FROM role_assignments WHERE id=$1", aid)
    after = await count_orphaned_role_assignment_ancillary(db)

    assert after["links"] == before["links"] + 1
    assert after["contact_methods"] == before["contact_methods"] + 1
    assert after["field_confidence"] == before["field_confidence"] + 1
    assert after["identifiers"] == before["identifiers"] + 1


async def test_guard_ignores_live_assignment_ancillary(db):
    aid = await _assignment(db)
    await _add_link(db, aid, "https://live.example/x")
    await _add_identifier(db, aid, "PDC-LIVE")

    before = await count_orphaned_role_assignment_ancillary(db)
    # No delete — the assignment is live, so nothing should be flagged.
    after = await count_orphaned_role_assignment_ancillary(db)

    assert after == before
