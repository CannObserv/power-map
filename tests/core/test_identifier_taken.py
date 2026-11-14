"""#812: naming who holds a ``*_cannabis_observer`` value, for a pre-check and a lost race."""

import asyncpg
import pytest
import pytest_asyncio

from src.core.db import generate_id
from src.core.identifier_taken import (
    IDENTIFIER_TAKEN_GUARD,
    Holder,
    identifier_holder,
    is_identifier_taken,
    taken_key,
    taken_reason,
    unique_violation_reason,
)
from tests.polymorphic_seeders import archived_entity

_TYPE_ID = "01M7DPQMH6CXKNTY183BXDMN1V"  # person_cannabis_observer


def _violation(constraint: str, detail: str) -> asyncpg.UniqueViolationError:
    exc = asyncpg.UniqueViolationError("duplicate key value violates unique constraint")
    exc.constraint_name = constraint
    exc.detail = detail
    return exc


def _key_detail(type_id: str, value: str) -> str:
    return f"Key (entity_identifier_type_id, value)=({type_id}, {value}) already exists."


# --- unit -------------------------------------------------------------------


def test_reason_names_a_live_holder():
    assert (
        taken_reason("person_cannabis_observer", Holder("01HOLDER", archived=False))
        == "identifier_taken: 'person_cannabis_observer' held by '01HOLDER'"
    )


def test_reason_says_an_archived_holder_is_archived():
    """#481: the fix is an unarchive (or a merge), not another value."""
    assert (
        taken_reason("person_cannabis_observer", Holder("01HOLDER", archived=True))
        == "identifier_taken: 'person_cannabis_observer' held by archived '01HOLDER'"
    )


def test_reason_without_a_holder_still_names_the_type():
    assert taken_reason("person_cannabis_observer", None) == (
        "identifier_taken: 'person_cannabis_observer'"
    )


def test_is_identifier_taken_reads_the_constraint_name():
    assert is_identifier_taken(_violation(IDENTIFIER_TAKEN_GUARD, ""))
    assert not is_identifier_taken(_violation("uq_role_org_title", ""))


def test_taken_key_parses_the_violation_detail():
    exc = _violation(IDENTIFIER_TAKEN_GUARD, _key_detail(_TYPE_ID, "01SITEULID"))
    assert taken_key(exc) == (_TYPE_ID, "01SITEULID")


def test_taken_key_keeps_commas_and_parentheses_in_the_value():
    exc = _violation(IDENTIFIER_TAKEN_GUARD, _key_detail(_TYPE_ID, "a, b (c)"))
    assert taken_key(exc) == (_TYPE_ID, "a, b (c)")


@pytest.mark.parametrize("detail", [None, "", "Key (id)=(x) already exists."])
def test_taken_key_is_none_on_an_unexpected_detail(detail):
    assert taken_key(_violation(IDENTIFIER_TAKEN_GUARD, detail)) is None


async def test_another_constraint_stays_a_generic_violation():
    """No read for a violation that isn't the index's: conn is never touched."""
    exc = _violation("uq_role_org_title", "Key (organization_id, lower(title))=(x, y) ...")
    assert await unique_violation_reason(None, exc) == "db_constraint_violation"


# --- integration ------------------------------------------------------------


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


async def _person(conn, *, archived: bool = False) -> str:
    if archived:
        return await archived_entity(conn, "person")
    pid = generate_id()
    await conn.execute("INSERT INTO people (id) VALUES ($1)", pid)
    return pid


async def _identify(conn, entity_id: str, value: str) -> None:
    await conn.execute(
        "INSERT INTO identifiers (id, entity_id, entity_identifier_type_id, value)"
        " VALUES ($1, $2, $3, $4)",
        generate_id(),
        entity_id,
        _TYPE_ID,
        value,
    )


@pytest.mark.integration
@pytest.mark.parametrize("archived", [False, True])
async def test_identifier_holder_finds_the_holder(conn, archived):
    holder = await _person(conn, archived=archived)
    await _identify(conn, holder, "01SITEULID")

    assert await identifier_holder(conn, _TYPE_ID, "01SITEULID") == Holder(holder, archived)


@pytest.mark.integration
async def test_identifier_holder_skips_the_entity_asking(conn):
    holder = await _person(conn)
    await _identify(conn, holder, "01SITEULID")

    assert await identifier_holder(conn, _TYPE_ID, "01SITEULID", other_than=holder) is None
    assert await identifier_holder(conn, _TYPE_ID, "01UNHELD") is None


@pytest.mark.integration
async def test_a_real_violation_names_the_type_and_the_holder(conn):
    holder, loser = await _person(conn), await _person(conn)
    await _identify(conn, holder, "01SITEULID")
    try:
        async with conn.transaction():
            await _identify(conn, loser, "01SITEULID")
    except asyncpg.UniqueViolationError as exc:
        caught = exc
    else:
        pytest.fail("the index admitted a second holder")

    assert await unique_violation_reason(conn, caught) == (
        f"identifier_taken: 'person_cannabis_observer' held by '{holder}'"
    )


@pytest.mark.integration
async def test_an_unparseable_violation_still_answers_identifier_taken(conn):
    exc = _violation(IDENTIFIER_TAKEN_GUARD, "unexpected")
    assert await unique_violation_reason(conn, exc) == "identifier_taken"
