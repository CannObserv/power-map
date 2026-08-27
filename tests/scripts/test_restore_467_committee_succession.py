"""Integration tests for the #467 un-collapse script (#469).

Reconstructs the merged state synthetically — winner org holding both WSL
identifiers and the loser's reminted pre-2021 assignments — then runs the
restore and asserts the succession shape: predecessor org + role resurrected
under their original ULIDs, tombstones cleared, identifier moved, assignments
re-pointed, dated succeeded_by event created. Re-running must be a no-op.
"""

import datetime

import pytest
import pytest_asyncio

from scripts.restore_467_committee_succession import RestorePlan, run_restore
from src.core.db import generate_id

pytestmark = [
    pytest.mark.integration,
]


@pytest_asyncio.fixture(loop_scope="session")
async def conn(db_pool):
    """Pool-acquired connection wrapped in a rolled-back transaction."""
    async with db_pool.acquire() as c:
        tr = c.transaction()
        await tr.start()
        try:
            yield c
        finally:
            await tr.rollback()


async def _mk_org(conn, name):
    oid = generate_id()
    await conn.execute("INSERT INTO organizations (id) VALUES ($1)", oid)
    await conn.execute(
        "INSERT INTO organization_names (id, organization_id, name, is_canonical)"
        " VALUES ($1, $2, $3, TRUE)",
        generate_id(),
        oid,
        name,
    )
    return oid


@pytest_asyncio.fixture(loop_scope="session")
async def merged_state(conn):
    """The post-#467-merge shape, with synthetic ids."""
    winner = await _mk_org(conn, "Washington State House Transportation Committee")
    pred_org_id = generate_id()  # hard-deleted; only the tombstone remains
    pred_role_id = generate_id()

    tid = await conn.fetchval(
        "SELECT id FROM entity_identifier_types WHERE slug='org_wa_legislature_committee_id'"
    )
    assert tid, "seeded identifier type missing"
    for value in ("31651", "3532"):
        await conn.execute(
            "INSERT INTO identifiers (id, entity_id, entity_identifier_type_id, value)"
            " VALUES ($1,$2,$3,$4)",
            generate_id(),
            winner,
            tid,
            value,
        )

    winner_role = generate_id()
    await conn.execute(
        "INSERT INTO roles (id, organization_id, title) VALUES ($1, $2, 'Member')",
        winner_role,
        winner,
    )
    person = generate_id()
    await conn.execute("INSERT INTO people (id) VALUES ($1)", person)
    old_ids, new_ids = [], []
    for start, end in (("1999-01-11", "2001-01-07"), ("2019-01-14", "2021-01-10")):
        # Reminted (pre-2021 start) rows — the migrated loser cohort.
        aid = generate_id()
        old_ids.append(aid)
        await conn.execute(
            "INSERT INTO role_assignments (id, role_id, person_id, start_date, end_date,"
            " is_current) VALUES ($1,$2,$3,$4::date,$5::date,FALSE)",
            aid,
            winner_role,
            person,
            datetime.date.fromisoformat(start),
            datetime.date.fromisoformat(end),
        )
    for start in ("2021-01-11", "2023-01-09"):
        aid = generate_id()
        new_ids.append(aid)
        await conn.execute(
            "INSERT INTO role_assignments (id, role_id, person_id, start_date, is_current)"
            " VALUES ($1,$2,$3,$4::date,TRUE)",
            aid,
            winner_role,
            person,
            datetime.date.fromisoformat(start),
        )

    for etype, eid in (("organization", pred_org_id), ("role", pred_role_id)):
        await conn.execute(
            "INSERT INTO deleted_entities (entity_type, entity_id) VALUES ($1,$2)",
            etype,
            eid,
        )

    plan = RestorePlan(
        winner_org_id=winner,
        predecessor_org_id=pred_org_id,
        predecessor_org_name="House Committee on Transportation",
        predecessor_role_id=pred_role_id,
        winner_member_role_id=winner_role,
        moved_identifier_value="3532",
        identifier_type_slug="org_wa_legislature_committee_id",
        cutoff=datetime.date(2021, 1, 1),
        succession_year=2020,
    )
    return {
        "plan": plan,
        "winner": winner,
        "pred_org": pred_org_id,
        "pred_role": pred_role_id,
        "winner_role": winner_role,
        "old_ids": old_ids,
        "new_ids": new_ids,
    }


async def test_dry_run_reports_without_writing(conn, merged_state):
    summary = await run_restore(conn, merged_state["plan"], execute=False)
    assert summary["moved_assignments"] == 2
    assert not await conn.fetchval(
        "SELECT 1 FROM organizations WHERE id=$1", merged_state["pred_org"]
    )
    assert await conn.fetchval(
        "SELECT 1 FROM deleted_entities WHERE entity_id=$1", merged_state["pred_org"]
    )


async def test_execute_restores_the_succession_shape(conn, merged_state):
    st = merged_state
    summary = await run_restore(conn, st["plan"], execute=True)
    assert summary["moved_assignments"] == 2

    # Org + role live again under their ORIGINAL ids; tombstones cleared.
    assert await conn.fetchval("SELECT 1 FROM organizations WHERE id=$1", st["pred_org"])
    role = await conn.fetchrow("SELECT * FROM roles WHERE id=$1", st["pred_role"])
    assert role and role["organization_id"] == st["pred_org"] and role["title"] == "Member"
    assert not await conn.fetchval(
        "SELECT 1 FROM deleted_entities WHERE entity_id IN ($1,$2)",
        st["pred_org"],
        st["pred_role"],
    )

    # Identifier 3532 moved; 31651 stays on the winner.
    owner = await conn.fetchval("SELECT entity_id FROM identifiers WHERE value='3532'")
    assert owner == st["pred_org"]
    assert (
        await conn.fetchval("SELECT entity_id FROM identifiers WHERE value='31651'") == st["winner"]
    )

    # Pre-cutoff assignments re-pointed (ULIDs kept); post-cutoff untouched.
    for aid in st["old_ids"]:
        assert (
            await conn.fetchval("SELECT role_id FROM role_assignments WHERE id=$1", aid)
            == st["pred_role"]
        )
    for aid in st["new_ids"]:
        assert (
            await conn.fetchval("SELECT role_id FROM role_assignments WHERE id=$1", aid)
            == st["winner_role"]
        )

    # Dated succession event: predecessor → winner; lifespan end derived.
    ev = await conn.fetchrow(
        """SELECT ev.* FROM entity_events ev
           JOIN entity_event_types t ON t.id = ev.event_type_id
           WHERE t.slug='succeeded_by' AND ev.entity_id=$1 AND ev.archived_at IS NULL""",
        st["pred_org"],
    )
    assert ev and ev["linked_entity_id"] == st["winner"] and ev["event_year"] == 2020
    assert await conn.fetchval(
        "SELECT ended_on FROM v_org_lifespan WHERE organization_id=$1", st["pred_org"]
    ) == datetime.date(2020, 12, 31)

    # Canonical name restored for display.
    assert (
        await conn.fetchval(
            "SELECT display_name FROM v_org_display_names WHERE organization_id=$1",
            st["pred_org"],
        )
        == "House Committee on Transportation"
    )


async def test_execute_twice_is_idempotent(conn, merged_state):
    st = merged_state
    await run_restore(conn, st["plan"], execute=True)
    summary = await run_restore(conn, st["plan"], execute=True)
    assert summary["moved_assignments"] == 0
    events = await conn.fetch(
        """SELECT ev.id FROM entity_events ev
           JOIN entity_event_types t ON t.id = ev.event_type_id
           WHERE t.slug='succeeded_by' AND ev.entity_id=$1""",
        st["pred_org"],
    )
    assert len(events) == 1
    names = await conn.fetch(
        "SELECT id FROM organization_names WHERE organization_id=$1", st["pred_org"]
    )
    assert len(names) == 1
