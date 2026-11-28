"""The review queue: what bulk accept leaves for an operator (#877 phase 3b, design D5).

The queue is every active org with no kind and no deciding ``manual`` row
whose answers bulk accept would not take: the reason comes from
``bulk_outcome`` itself, so the page and the script can't disagree. A cleared
org is back in the queue whatever its answers say. An untouched org that bulk
accept *would* take isn't queued; it is counted as ready for the next run.
"""

import pytest
import pytest_asyncio

from src.core import classification as cls
from src.core.db import generate_id

T = 0.7
TRUSTED = "jev-1.14.0"


def _item(org_id, reason, confidence=None, cleared=False):
    return cls.ReviewItem(
        org_id=org_id,
        name=None,
        rules=None,
        jev=None,
        confidence=confidence,
        reason=reason,
        cleared=cleared,
    )


def test_review_reasons_are_the_queue_reasons_plus_cleared():
    assert cls.REVIEW_REASONS == (*cls.QUEUE_REASONS, "cleared")


def test_order_puts_disputes_first_then_confidence_ascending_with_no_answer_last():
    items = [
        _item("01H", "unanswered"),
        _item("01G", "low", 0.65),
        _item("01F", "untrusted", 0.95),
        _item("01E", "low", 0.30),
        _item("01D", "disagree", 0.99),
        _item("01C", "conflict"),
        _item("01B", "none", 0.30),
        _item("01A", "cleared"),
    ]
    assert [i.org_id for i in cls.order_review(items)] == [
        "01D",  # disputes first, by confidence, no answer last
        "01C",
        "01B",  # then confidence ascending; ties end on the org id
        "01E",
        "01G",
        "01F",
        "01A",  # no answer: by org id
        "01H",
    ]


# --- DB-backed -------------------------------------------------------------


@pytest_asyncio.fixture(loop_scope="session")
async def db(db_pool):
    async with db_pool.acquire() as conn:
        tr = conn.transaction()
        await tr.start()
        try:
            yield conn
        finally:
            await tr.rollback()


async def _org(db, archived=False, name=None) -> str:
    oid = generate_id()
    await db.execute(
        "INSERT INTO organizations (id, archived_at) VALUES ($1, CASE WHEN $2 THEN NOW() END)",
        oid,
        archived,
    )
    if name:
        await db.execute(
            "INSERT INTO organization_names (id, organization_id, name, is_canonical)"
            " VALUES ($1, $2, $3, TRUE)",
            generate_id(),
            oid,
            name,
        )
    return oid


async def _suggest(db, org_id, source, value, confidence=None, model=None, note=None):
    await db.execute(
        "INSERT INTO classification_suggestions"
        " (id, entity_type, entity_id, field, source, value, model, confidence, note)"
        " VALUES ($1,'organization',$2,'org_kind',$3,$4,$5,$6,$7)",
        generate_id(),
        org_id,
        source,
        value,
        model or (cls.RULES_VERSION if source == "rules" else TRUSTED),
        confidence,
        note,
    )


def _mine(queue, *ids):
    return {i.org_id: i for i in queue.items if i.org_id in ids}


@pytest.mark.integration
async def test_the_queue_holds_what_bulk_accept_leaves_with_its_reason(db):
    disputed, conflicted, low, untrusted, nothing, waiting = [await _org(db) for _ in range(6)]
    await _suggest(db, disputed, "rules", "business")
    await _suggest(db, disputed, "jev", "tribe", 0.97)
    await _suggest(db, conflicted, "rules", "none", note="conflict: a (x), b (y)")
    await _suggest(db, low, "jev", "government", 0.4)
    await _suggest(db, untrusted, "jev", "nonprofit", 0.99)
    await _suggest(db, nothing, "jev", "none", 0.8)

    queue = await cls.review_queue(db, threshold=T)
    got = _mine(queue, disputed, conflicted, low, untrusted, nothing, waiting)

    assert {o: i.reason for o, i in got.items()} == {
        disputed: "disagree",
        conflicted: "conflict",
        low: "low",
        untrusted: "untrusted",
        nothing: "none",
        waiting: "unanswered",
    }
    assert (got[disputed].rules, got[disputed].jev, got[disputed].confidence) == (
        "business",
        "tribe",
        0.97,
    )
    assert not any(i.cleared for i in got.values())
    assert queue.items == cls.order_review(queue.items)


@pytest.mark.integration
async def test_orgs_bulk_accept_would_take_are_counted_as_ready_not_queued(db):
    before = (await cls.review_queue(db, threshold=T)).ready
    by_rules, by_jev = await _org(db), await _org(db)
    await _suggest(db, by_rules, "rules", "legislature")
    await _suggest(db, by_jev, "jev", "business", 0.9)

    queue = await cls.review_queue(db, threshold=T)

    assert _mine(queue, by_rules, by_jev) == {}
    assert queue.ready == before + 2


@pytest.mark.integration
async def test_a_cleared_org_is_back_in_the_queue_whatever_its_answers(db):
    takeable, low = await _org(db), await _org(db)
    await _suggest(db, takeable, "rules", "legislature")
    await _suggest(db, low, "jev", "government", 0.4)
    for org in (takeable, low):
        await cls.record_decision(db, org_id=org, action="set", kind="business")
        await cls.record_decision(db, org_id=org, action="clear")

    got = _mine(await cls.review_queue(db, threshold=T), takeable, low)

    assert {o: (i.reason, i.cleared) for o, i in got.items()} == {
        takeable: ("cleared", True),
        low: ("low", True),
    }


@pytest.mark.integration
async def test_decided_archived_and_kinded_orgs_are_not_queued(db):
    archived = await _org(db, archived=True)
    accepted, set_, dismissed = await _org(db), await _org(db), await _org(db)
    for org in (archived, accepted, set_, dismissed):
        await _suggest(db, org, "jev", "government", 0.4)
    await _suggest(db, accepted, "rules", "business")
    await cls.record_decision(db, org_id=accepted, action="accept", kind="business", source="rules")
    await cls.record_decision(db, org_id=set_, action="set", kind="tribe")
    await cls.record_decision(db, org_id=dismissed, action="dismiss")

    got = _mine(await cls.review_queue(db, threshold=T), archived, accepted, set_, dismissed)

    assert got == {}


@pytest.mark.integration
async def test_an_ignored_answer_is_not_read(db):
    org = await _org(db)
    await _suggest(db, org, "jev", "business", 0.4)
    await db.execute(
        "UPDATE classification_suggestions SET ignored_at = NOW() WHERE entity_id = $1", org
    )
    (item,) = _mine(await cls.review_queue(db, threshold=T), org).values()
    assert item.reason == "unanswered"


@pytest.mark.integration
async def test_items_carry_the_display_name(db):
    named, unnamed = await _org(db, name="Review Queue Test Org"), await _org(db)
    got = _mine(await cls.review_queue(db, threshold=T), named, unnamed)
    assert (got[named].name, got[unnamed].name) == ("Review Queue Test Org", None)


# --- record_decision(undecided_only=True): the queue page's guard ----------


@pytest.mark.integration
async def test_undecided_only_refuses_an_org_decided_since_the_page_loaded(db):
    org = await _org(db)
    await cls.record_decision(db, org_id=org, action="dismiss")
    with pytest.raises(cls.DecisionError, match="already decided"):
        await cls.record_decision(
            db, org_id=org, action="set", kind="business", undecided_only=True
        )
    assert await cls.is_decided(db, org)
    assert await db.fetchval("SELECT org_kind_id FROM organizations WHERE id=$1", org) is None


@pytest.mark.integration
async def test_undecided_only_decides_an_untouched_or_cleared_org(db):
    untouched, cleared = await _org(db), await _org(db)
    await cls.record_decision(db, org_id=cleared, action="dismiss")
    await cls.record_decision(db, org_id=cleared, action="clear")
    for org in (untouched, cleared):
        await cls.record_decision(
            db, org_id=org, action="set", kind="business", undecided_only=True
        )
        assert await cls.is_decided(db, org)
