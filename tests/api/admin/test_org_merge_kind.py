"""An org merge carries the org-kind decision and drops derived suggestions (#877).

Through `_execute_merge` itself, so the wiring is tested, not just the helper:
the survivor keeps its own decision; when it has none it takes the loser's
manual rows and resolved kind; the loser's rules and Jev rows go with the loser,
leaving nothing for the ancillary-orphans audit.
"""

import pytest
import pytest_asyncio

from src.api.admin import orgs_merge
from src.core import classification as cls
from src.core.db import generate_id

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


async def _org(db) -> str:
    oid = generate_id()
    await db.execute("INSERT INTO organizations (id) VALUES ($1)", oid)
    return oid


async def _rules(db, org_id, value):
    await db.execute(
        "INSERT INTO classification_suggestions"
        " (id, entity_type, entity_id, field, source, value, model)"
        " VALUES ($1, 'organization', $2, 'org_kind', 'rules', $3, 'rules-1')",
        generate_id(),
        org_id,
        value,
    )


async def _kind(db, org_id):
    return await db.fetchval(
        "SELECT k.slug FROM organizations o LEFT JOIN org_kinds k ON k.id = o.org_kind_id"
        " WHERE o.id = $1",
        org_id,
    )


async def _rows(db, org_id):
    return await db.fetch(
        "SELECT source, value, manual_action FROM classification_suggestions"
        " WHERE entity_id = $1 ORDER BY created_at, id",
        org_id,
    )


async def test_undecided_survivor_takes_the_losers_decision(db):
    winner, loser = await _org(db), await _org(db)
    await _rules(db, loser, "business")
    await cls.record_decision(db, org_id=loser, action="set", kind="association")

    await orgs_merge._execute_merge(db, winner, loser)

    assert await _kind(db, winner) == "association"
    assert await cls.is_decided(db, winner)
    # The loser's decision moves over as history, then is restated on the survivor
    # so it is the survivor's latest manual row (CR 2).
    assert [(r["source"], r["manual_action"]) for r in await _rows(db, winner)] == [
        ("manual", "set"),
        ("manual", "set"),
    ]
    assert await _rows(db, loser) == []


async def test_decided_survivor_keeps_its_own_decision(db):
    winner, loser = await _org(db), await _org(db)
    await cls.record_decision(db, org_id=winner, action="set", kind="nonprofit")
    await cls.record_decision(db, org_id=loser, action="set", kind="business")

    await orgs_merge._execute_merge(db, winner, loser)

    assert await _kind(db, winner) == "nonprofit"
    assert [(r["source"], r["value"]) for r in await _rows(db, winner)] == [("manual", "nonprofit")]
    assert await _rows(db, loser) == []


async def test_a_carried_accept_keeps_its_source(db):
    winner, loser = await _org(db), await _org(db)
    await _rules(db, loser, "business")
    await cls.record_decision(db, org_id=loser, action="accept", kind="business", source="rules")

    await orgs_merge._execute_merge(db, winner, loser)

    latest = (await cls.latest_answers(db, winner))["manual"]
    assert (latest["manual_action"], latest["accepted_source"]) == ("accept", "rules")
