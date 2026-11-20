"""Integration tests for src.core.person_name_observations (#845).

Per-name results, citations on a name, and ``op="retract"`` — the person-name
half of ``POST /people/observations``. The wire mapping is covered in
``tests/api/public/test_observations_people_names.py``.
"""

import asyncio
import contextlib
import hashlib
import os

import asyncpg
import pytest
import pytest_asyncio

from src.api.public.schemas import CitationObservationItem, ObservationPersonName
from src.core.db import generate_id
from src.core.observation import ObservationRejected
from src.core.person_name_observations import (
    NameDisposition,
    NameRejectReason,
    write_person_names,
)

pytestmark = [pytest.mark.integration]


@pytest_asyncio.fixture(loop_scope="session")
async def db(db_pool):
    """Pool-acquired connection wrapped in a rolled-back transaction."""
    async with db_pool.acquire() as conn:
        tr = conn.transaction()
        await tr.start()
        try:
            yield conn
        finally:
            await tr.rollback()


async def _api_key(db) -> str:
    uid, kid = generate_id(), generate_id()
    raw = "pm_" + os.urandom(16).hex()
    await db.execute(
        "INSERT INTO app_users (id, email) VALUES ($1, $2)", uid, f"{kid.lower()}@names.test"
    )
    await db.execute(
        "INSERT INTO api_keys (id, user_id, label, key_prefix, key_hash) VALUES ($1,$2,$3,$4,$5)",
        kid,
        uid,
        "Name Obs Test",
        raw[:8],
        hashlib.sha256(raw.encode()).hexdigest(),
    )
    return kid


@pytest_asyncio.fixture(loop_scope="session")
async def key_id(db):
    """The calling key."""
    return await _api_key(db)


@pytest_asyncio.fixture(loop_scope="session")
async def person_id(db):
    """A person displaying a curated legal name (NULL source, as production's are)."""
    pid = generate_id()
    await db.execute("INSERT INTO people (id) VALUES ($1)", pid)
    await _name(db, pid, "Jodi Example", "legal", is_canonical=True)
    return pid


async def _name(
    db,
    person_id: str,
    name: str,
    name_type: str = "variant",
    *,
    source_key_id: str | None = None,
    is_canonical: bool = False,
    visibility: str = "public",
    reading_of_id: str | None = None,
) -> str:
    nid = generate_id()
    await db.execute(
        "INSERT INTO person_names"
        " (id, person_id, name, name_type, source_key_id, is_canonical, visibility, reading_of_id)"
        " VALUES ($1, $2, $3, $4, $5, $6, $7, $8)",
        nid,
        person_id,
        name,
        name_type,
        source_key_id,
        is_canonical,
        visibility,
        reading_of_id,
    )
    return nid


def _variant(name: str = "Jody Example", **kwargs) -> ObservationPersonName:
    return ObservationPersonName(name=name, name_type="variant", **kwargs)


def _retract(name: str = "Jody Example", name_type: str = "variant") -> ObservationPersonName:
    return ObservationPersonName(name=name, name_type=name_type, op="retract")


async def _names(db, person_id: str) -> set[tuple[str, str]]:
    rows = await db.fetch("SELECT name, name_type FROM person_names WHERE person_id=$1", person_id)
    return {(r["name"], r["name_type"]) for r in rows}


async def _name_citations(db, person_id: str) -> int:
    """Citations on any of the person's names (they outlive nothing: no FK)."""
    return await db.fetchval(
        "SELECT count(*) FROM citations c JOIN person_names n ON n.id = c.entity_id"
        " WHERE c.entity_type='person_name' AND n.person_id=$1",
        person_id,
    )


# ---------------------------------------------------------------------------
# Per-name results
# ---------------------------------------------------------------------------


async def test_a_new_name_reports_new_with_its_id(db, person_id, key_id):
    (result,) = await write_person_names(db, person_id, key_id, [_variant()])

    assert result.disposition is NameDisposition.NEW
    assert result.name_id == await db.fetchval(
        "SELECT id FROM person_names WHERE person_id=$1 AND name='Jody Example'", person_id
    )


async def test_a_name_already_held_reports_auto_attached_with_its_id(db, person_id, key_id):
    held = await _name(db, person_id, "Jody Example")

    (result,) = await write_person_names(db, person_id, key_id, [_variant()])

    assert (result.disposition, result.name_id) == (NameDisposition.AUTO_ATTACHED, held)


async def test_results_come_back_in_request_order(db, person_id, key_id):
    await _name(db, person_id, "Gone Example", source_key_id=key_id)

    results = await write_person_names(
        db,
        person_id,
        key_id,
        [_variant("First Example"), _retract("Gone Example"), _variant("Third Example")],
    )

    assert [r.disposition for r in results] == [
        NameDisposition.NEW,
        NameDisposition.RETRACTED,
        NameDisposition.NEW,
    ]


async def test_a_nameless_call_still_heals_the_display_pointer(db, key_id):
    """write_names' heal ran on every people observation, names or not (#308)."""
    pid = generate_id()
    await db.execute("INSERT INTO people (id) VALUES ($1)", pid)
    await _name(db, pid, "Jodi Example", "legal")

    assert await write_person_names(db, pid, key_id, []) == []

    assert await db.fetchval("SELECT is_canonical FROM person_names WHERE person_id=$1", pid)


# ---------------------------------------------------------------------------
# A name that is not public reports nothing a read would not
# ---------------------------------------------------------------------------


async def test_a_deadname_reports_not_public_whether_new_or_held(db, person_id, key_id):
    """The trigger forces a deadname to legal_only. `new` versus `auto-attached`
    would tell a caller whether the person already held it."""
    deadname = ObservationPersonName(name="Old Example", name_type="deadname")

    (first,) = await write_person_names(db, person_id, key_id, [deadname])
    (second,) = await write_person_names(db, person_id, key_id, [deadname])

    assert first == second
    assert (first.disposition, first.name_id) == (NameDisposition.NOT_PUBLIC, None)


async def test_a_name_a_curator_hid_reports_not_public(db, person_id, key_id):
    await _name(db, person_id, "Jody Example", source_key_id=key_id, visibility="hidden")

    (result,) = await write_person_names(db, person_id, key_id, [_variant()])

    assert (result.disposition, result.name_id) == (NameDisposition.NOT_PUBLIC, None)


# ---------------------------------------------------------------------------
# Citations on a name
# ---------------------------------------------------------------------------


def _cite(**kwargs) -> CitationObservationItem:
    return CitationObservationItem(
        **{"url": "https://hearings.example/42", "title": "Hearing 42", **kwargs}
    )


async def test_a_citation_lands_on_the_name_it_rides_with(db, person_id, key_id):
    (result,) = await write_person_names(
        db, person_id, key_id, [_variant(citations=[_cite(field_name="name")])]
    )

    row = await db.fetchrow(
        "SELECT id, entity_id, field_name, url, source_key_id FROM citations"
        " WHERE entity_type='person_name' AND entity_id=$1",
        result.name_id,
    )
    assert (row["field_name"], row["url"], row["source_key_id"]) == (
        "name",
        "https://hearings.example/42",
        key_id,
    )
    assert [(c.disposition.value, c.citation_id) for c in result.citations] == [("new", row["id"])]


async def test_a_citation_attaches_to_a_name_someone_else_wrote(db, person_id, key_id):
    legal = await db.fetchval("SELECT id FROM person_names WHERE person_id=$1", person_id)

    (result,) = await write_person_names(
        db,
        person_id,
        key_id,
        [ObservationPersonName(name="Jodi Example", name_type="legal", citations=[_cite()])],
    )

    assert result.name_id == legal
    assert (
        await db.fetchval(
            "SELECT count(*) FROM citations WHERE entity_type='person_name' AND entity_id=$1",
            legal,
        )
        == 1
    )


async def test_a_name_without_citations_reports_none(db, person_id, key_id):
    (result,) = await write_person_names(db, person_id, key_id, [_variant()])

    assert result.citations is None


async def test_a_rejected_citation_rejects_the_observation(db, person_id, key_id):
    with pytest.raises(ObservationRejected) as exc:
        await write_person_names(
            db, person_id, key_id, [_variant(citations=[_cite(field_name="notes")])]
        )

    assert exc.value.detail == "citable_field_unknown"


async def test_a_non_public_name_keeps_its_citation_but_reports_none(db, person_id, key_id):
    (result,) = await write_person_names(
        db,
        person_id,
        key_id,
        [ObservationPersonName(name="Old Example", name_type="deadname", citations=[_cite()])],
    )

    assert result.citations is None
    assert await _name_citations(db, person_id) == 1


# ---------------------------------------------------------------------------
# op="retract"
# ---------------------------------------------------------------------------


async def test_retract_deletes_the_callers_own_name(db, person_id, key_id):
    own = await _name(db, person_id, "Jody Example", source_key_id=key_id)

    (result,) = await write_person_names(db, person_id, key_id, [_retract()])

    assert (result.disposition, result.name_id) == (NameDisposition.RETRACTED, own)
    assert await _names(db, person_id) == {("Jodi Example", "legal")}


async def test_retract_drops_the_names_citations(db, person_id, key_id):
    (added,) = await write_person_names(db, person_id, key_id, [_variant(citations=[_cite()])])

    await write_person_names(db, person_id, key_id, [_retract()])

    assert (
        await db.fetchval(
            "SELECT count(*) FROM citations WHERE entity_type='person_name' AND entity_id=$1",
            added.name_id,
        )
        == 0
    )


async def test_retract_refuses_a_name_another_key_cited(db, person_id, key_id):
    """The same-key gate covers the row. Its citations are deleted with it, so a
    citation someone else wrote is theirs to lose (CR 1)."""
    other = await _api_key(db)
    await write_person_names(db, person_id, key_id, [_variant()])
    await write_person_names(db, person_id, other, [_variant(citations=[_cite()])])

    with pytest.raises(ObservationRejected) as exc:
        await write_person_names(db, person_id, key_id, [_retract()])

    assert exc.value.detail == NameRejectReason.HAS_FOREIGN_CITATIONS
    assert exc.value.detail == "name_has_foreign_citations"


async def test_retract_refuses_a_name_a_curator_cited(db, person_id, key_id):
    """A citation added in admin carries no key (CR 1)."""
    (added,) = await write_person_names(db, person_id, key_id, [_variant()])
    await db.execute(
        "INSERT INTO citations (id, entity_type, entity_id, url)"
        " VALUES ($1, 'person_name', $2, $3)",
        generate_id(),
        added.name_id,
        "https://curator.example/source",
    )

    with pytest.raises(ObservationRejected) as exc:
        await write_person_names(db, person_id, key_id, [_retract()])

    assert exc.value.detail == "name_has_foreign_citations"
    assert ("Jody Example", "variant") in await _names(db, person_id)


async def test_retract_then_observe_adds_the_name_again(db, person_id, key_id):
    await write_person_names(db, person_id, key_id, [_variant()])
    await write_person_names(db, person_id, key_id, [_retract()])

    (result,) = await write_person_names(db, person_id, key_id, [_variant()])

    assert result.disposition is NameDisposition.NEW
    assert ("Jody Example", "variant") in await _names(db, person_id)


async def test_retract_of_a_name_not_held_is_a_quiet_noop(db, person_id, key_id):
    (result,) = await write_person_names(db, person_id, key_id, [_retract()])

    assert (result.disposition, result.name_id) == (NameDisposition.RETRACTED, None)
    assert await _names(db, person_id) == {("Jodi Example", "legal")}


async def test_retract_matches_the_whole_identity_not_the_string(db, person_id, key_id):
    """A curator who retypes the row has taken it: the variant is no longer there."""
    await _name(db, person_id, "Jody Example", "preferred", source_key_id=key_id)

    (result,) = await write_person_names(db, person_id, key_id, [_retract()])

    assert result.name_id is None
    assert ("Jody Example", "preferred") in await _names(db, person_id)


async def test_retract_of_another_keys_name_is_rejected(db, person_id, key_id):
    await _name(db, person_id, "Jody Example", source_key_id=await _api_key(db))

    with pytest.raises(ObservationRejected) as exc:
        await write_person_names(db, person_id, key_id, [_retract()])

    assert exc.value.detail == NameRejectReason.PROVENANCE_CONFLICT == "name_provenance_conflict"


async def test_retract_of_an_unstamped_name_is_rejected(db, person_id, key_id):
    """Not the same-or-NULL gate of the other retract paths: every curated and
    usa-wa name carries a NULL stamp, and none of them is a key's to delete."""
    with pytest.raises(ObservationRejected) as exc:
        await write_person_names(db, person_id, key_id, [_retract("Jodi Example", "legal")])

    assert exc.value.detail == "name_provenance_conflict"
    assert await _names(db, person_id) == {("Jodi Example", "legal")}


async def test_retract_of_the_displayed_name_is_rejected(db, key_id):
    pid = generate_id()
    await db.execute("INSERT INTO people (id) VALUES ($1)", pid)
    await _name(db, pid, "Jodi Example", "legal")
    await _name(db, pid, "Jody Example", source_key_id=key_id, is_canonical=True)

    with pytest.raises(ObservationRejected) as exc:
        await write_person_names(db, pid, key_id, [_retract()])

    assert exc.value.detail == NameRejectReason.IS_CANONICAL == "name_is_canonical"


async def test_retract_of_a_persons_only_name_is_rejected(db, key_id):
    pid = generate_id()
    await db.execute("INSERT INTO people (id) VALUES ($1)", pid)
    await _name(db, pid, "Jody Example", source_key_id=key_id)

    with pytest.raises(ObservationRejected) as exc:
        await write_person_names(db, pid, key_id, [_retract()])

    assert exc.value.detail == NameRejectReason.IS_LAST == "name_is_last"


async def test_retract_of_the_only_public_name_is_rejected_whatever_is_hidden(db, key_id):
    """The guard counts what a read shows (CR 2). Counting a hidden name would
    answer differently for two people who read the same — telling the caller one
    of them holds a name no read returns — and would leave the person no public name."""
    pid = generate_id()
    await db.execute("INSERT INTO people (id) VALUES ($1)", pid)
    await _name(db, pid, "Jody Example", source_key_id=key_id)
    await _name(db, pid, "Old Example", "deadname", visibility="legal_only")
    await _name(db, pid, "Kept Quiet", "alias", visibility="hidden")

    with pytest.raises(ObservationRejected) as exc:
        await write_person_names(db, pid, key_id, [_retract()])

    assert exc.value.detail == "name_is_last"


async def test_retract_of_a_name_with_readings_is_rejected(db, person_id, key_id):
    """reading_of_id is ON DELETE CASCADE: the delete would take a curator's row."""
    own = await _name(db, person_id, "Jody Example", source_key_id=key_id)
    await _name(db, person_id, "JOH-dee", "reading", reading_of_id=own)

    with pytest.raises(ObservationRejected) as exc:
        await write_person_names(db, person_id, key_id, [_retract()])

    assert exc.value.detail == NameRejectReason.HAS_READINGS == "name_has_readings"


async def test_retracts_run_before_the_heal(db, key_id):
    """On a person with no displayed name, write_names' heal would promote the
    very row being retracted, and the retract would refuse it as canonical —
    about a row every read shows as not canonical, on every attempt (CR 4)."""
    pid = generate_id()
    await db.execute("INSERT INTO people (id) VALUES ($1)", pid)
    await _name(db, pid, "Jodi Example", "legal", source_key_id=key_id)  # lower id: the heal's pick
    await _name(db, pid, "Josephine Example", "legal")

    (result,) = await write_person_names(db, pid, key_id, [_retract("Jodi Example", "legal")])

    assert result.disposition is NameDisposition.RETRACTED
    assert await db.fetch(
        "SELECT name, is_canonical FROM person_names WHERE person_id=$1", pid
    ) == [("Josephine Example", True)]


async def test_a_name_added_beside_a_retract_does_not_lift_the_last_name_guard(db, key_id):
    """Retracts are judged against the person as the request found them (CR 4)."""
    pid = generate_id()
    await db.execute("INSERT INTO people (id) VALUES ($1)", pid)
    await _name(db, pid, "Jody Example", source_key_id=key_id)

    with pytest.raises(ObservationRejected) as exc:
        await write_person_names(db, pid, key_id, [_variant("Jo Example"), _retract()])

    assert exc.value.detail == "name_is_last"


async def test_retract_leaves_a_name_a_curator_hid(db, person_id, key_id):
    """Hiding is the curator's durable no (a re-send stays a no-op). Deleting the
    hidden row would forget it, and the next re-send would publish the name."""
    hidden = await _name(db, person_id, "Jody Example", source_key_id=key_id, visibility="hidden")

    (result,) = await write_person_names(db, person_id, key_id, [_retract()])

    assert (result.disposition, result.name_id) == (NameDisposition.RETRACTED, None)
    assert await db.fetchval("SELECT visibility FROM person_names WHERE id=$1", hidden) == "hidden"


async def test_retract_ignores_any_payload_on_the_item(db, person_id, key_id):
    await _name(db, person_id, "Jody Example", source_key_id=key_id)

    await write_person_names(
        db,
        person_id,
        key_id,
        [
            ObservationPersonName(
                name="Jody Example",
                name_type="variant",
                op="retract",
                is_canonical=True,
                citations=[_cite()],
            )
        ],
    )

    assert await _names(db, person_id) == {("Jodi Example", "legal")}
    assert await _name_citations(db, person_id) == 0


# ---------------------------------------------------------------------------
# A promote that commits between the retract's checks and its delete (CR 7)
# ---------------------------------------------------------------------------

# db_pool is max_size=2 and this test holds both connections; bound the acquire
# so a third consumer fails instead of hanging the suite.
_POOL_ACQUIRE_TIMEOUT_S = 10


async def test_retract_loses_to_a_promote_that_commits_first(db, db_pool):
    """The retract checks the row, then deletes it. A curator's promote landing
    between the two must win: the delete re-checks, and refuses the displayed name.

    Two real connections, as the heal's race test uses — the interleaving cannot
    be staged on one. Deterministically:
      1. `other` promotes the variant, uncommitted.
      2. The retract's probe cannot see that, so every check passes.
      3. Its DELETE blocks on the promote's row lock.
      4. `other` commits; the DELETE re-evaluates its WHERE against the new row.
    Without the re-check in that WHERE it deletes the displayed name.
    """
    pid = generate_id()
    async with db_pool.acquire(timeout=_POOL_ACQUIRE_TIMEOUT_S) as other:
        # Committed out-of-band so the retract's connection can see the rows.
        kid = await _api_key(other)
        uid = await other.fetchval("SELECT user_id FROM api_keys WHERE id=$1", kid)
        await other.execute("INSERT INTO people (id) VALUES ($1)", pid)
        legal = await _name(other, pid, "Jodi Example", "legal", is_canonical=True)
        variant = await _name(other, pid, "Jody Example", source_key_id=kid)
        promote = other.transaction()
        await promote.start()
        # A savepoint, as the route's transaction is: rolling it back releases
        # the row locks the retract took, so the cleanup below cannot block.
        attempt = db.transaction()
        await attempt.start()
        committed = False
        task = None
        try:
            await other.execute("UPDATE person_names SET is_canonical=FALSE WHERE id=$1", legal)
            await other.execute("UPDATE person_names SET is_canonical=TRUE WHERE id=$1", variant)
            task = asyncio.create_task(write_person_names(db, pid, kid, [_retract()]))
            await asyncio.sleep(0.5)
            assert not task.done(), "the retract should be blocked on the uncommitted promote"
            await promote.commit()
            committed = True
            with pytest.raises(ObservationRejected) as exc:
                await asyncio.wait_for(task, timeout=10)
            assert exc.value.detail == "name_is_canonical"
        finally:
            if not committed:
                with contextlib.suppress(asyncpg.exceptions.InterfaceError):
                    await promote.rollback()
            if task is not None and not task.done():
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
            with contextlib.suppress(asyncpg.exceptions.InterfaceError):
                await attempt.rollback()
            # These rows are COMMITTED: clean up outside any transaction.
            await other.execute("DELETE FROM person_names WHERE person_id=$1", pid)
            await other.execute("DELETE FROM people WHERE id=$1", pid)
            await other.execute("DELETE FROM api_keys WHERE id=$1", kid)
            await other.execute("DELETE FROM app_users WHERE id=$1", uid)
