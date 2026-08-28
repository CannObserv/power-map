"""Integration tests for the #467 un-collapse script (#469).

Reconstructs the merged state synthetically — winner org holding both WSL
identifiers and the loser's reminted pre-2021 assignments — then runs the
restore and asserts the succession shape: predecessor org + role resurrected
under their original ULIDs, tombstones cleared, identifier moved, assignments
re-pointed, dated succeeded_by event created. Re-running must be a no-op.

Since #479 the restore also carries a subscriber duty: the resurrection is
unobservable through `GET /changes` to any key that unsubscribed on the
tombstone, so the winner's current watchers — the closest available stand-in for
the delete-time audience — are re-subscribed to the ids that came back.
"""

import datetime
import hashlib
import os

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


@pytest_asyncio.fixture(loop_scope="session")
async def api_key_id(conn):
    """A key that can hold subscriptions — the consumer side of the restore."""
    uid, kid = generate_id(), generate_id()
    raw_key = "pm_" + os.urandom(16).hex()
    await conn.execute("INSERT INTO app_users (id, email) VALUES ($1,$2)", uid, "restore@test.com")
    await conn.execute(
        "INSERT INTO api_keys (id, user_id, label, key_prefix, key_hash) VALUES ($1,$2,$3,$4,$5)",
        kid,
        uid,
        "Restore Unit",
        raw_key[:8],
        hashlib.sha256(raw_key.encode()).hexdigest(),
    )
    return kid


async def _subscribe(conn, kid, entity_id, entity_type):
    await conn.execute(
        "INSERT INTO api_key_entity_subscriptions (api_key_id, entity_id, entity_type)"
        " VALUES ($1,$2,$3)",
        kid,
        entity_id,
        entity_type,
    )


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


async def test_current_rows_in_cohort_are_counted_and_warned(conn, merged_state):
    """#469 CR: an is_current row moving onto the ended predecessor violates the
    hard #307 invariant — the dry run must surface it, not the audit later."""
    st = merged_state
    person = generate_id()
    await conn.execute("INSERT INTO people (id) VALUES ($1)", person)
    await conn.execute(
        "INSERT INTO role_assignments (id, role_id, person_id, start_date, is_current)"
        " VALUES ($1, $2, $3, DATE '2017-01-09', TRUE)",
        generate_id(),
        st["winner_role"],
        person,
    )
    summary = await run_restore(conn, st["plan"], execute=False)
    assert summary["current_moved"] == 1
    summary = await run_restore(conn, st["plan"], execute=True)
    assert summary["current_moved"] == 1


# ---------------------------------------------------------------------------
# The subscriber duty (#479)
# ---------------------------------------------------------------------------


async def test_dry_run_reports_the_audience_without_subscribing_it(conn, merged_state, api_key_id):
    """The count is the report; nothing is written until --execute."""
    st = merged_state
    await _subscribe(conn, api_key_id, st["winner"], "organization")
    await _subscribe(conn, api_key_id, st["winner_role"], "role")

    summary = await run_restore(conn, st["plan"], execute=False)

    assert summary["subscriptions_restored"] == 2
    assert (
        await conn.fetchval(
            "SELECT count(*) FROM api_key_entity_subscriptions WHERE entity_id = ANY($1::text[])",
            [st["pred_org"], st["pred_role"]],
        )
        == 0
    )


async def test_execute_resubscribes_the_winners_watchers(conn, merged_state, api_key_id):
    """The winner's current watchers stand in for the audience the tombstone lost.

    A consumer that processed the `deleted` event and unsubscribed can never be
    told through the feed that the id is live again — `GET /changes` joins on
    *current* subscriptions. Re-subscribing is the only path back.
    """
    st = merged_state
    await _subscribe(conn, api_key_id, st["winner"], "organization")
    await _subscribe(conn, api_key_id, st["winner_role"], "role")

    summary = await run_restore(conn, st["plan"], execute=True)

    assert summary["subscriptions_restored"] == 2
    rows = await conn.fetch(
        "SELECT entity_id, entity_type FROM api_key_entity_subscriptions WHERE api_key_id=$1",
        api_key_id,
    )
    assert {(r["entity_id"], r["entity_type"]) for r in rows} == {
        (st["winner"], "organization"),
        (st["winner_role"], "role"),
        (st["pred_org"], "organization"),
        (st["pred_role"], "role"),
    }


async def test_resubscribe_counts_only_the_keys_that_are_missing_it(conn, merged_state, api_key_id):
    """Already-subscribed keys are not re-reported, so a second run reports zero."""
    st = merged_state
    await _subscribe(conn, api_key_id, st["winner"], "organization")
    await _subscribe(conn, api_key_id, st["pred_org"], "organization")

    first = await run_restore(conn, st["plan"], execute=True)
    assert first["subscriptions_restored"] == 0

    second = await run_restore(conn, st["plan"], execute=True)
    assert second["subscriptions_restored"] == 0


async def test_no_watchers_means_nothing_to_restore(conn, merged_state):
    """Nobody watching the winner means no audience to approximate — not an error."""
    summary = await run_restore(conn, merged_state["plan"], execute=True)
    assert summary["subscriptions_restored"] == 0
