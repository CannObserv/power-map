"""#812: one value of a ``*_cannabis_observer`` identifier type names one entity.

``uq_identifiers_cannabis_observer`` is a partial unique index on
``identifiers (entity_identifier_type_id, value)`` over the four seeded type ids.
It covers every row, archived holders included (archiving keeps identifiers,
#481), and no other type: the WA scheme types legitimately repeat a value
across entities. Its ``DO`` block warns and skips on existing duplicates rather
than abort ``apply_schema`` — and with it the service start (#398/#748).
"""

import asyncio
import re

import asyncpg
import pytest
import pytest_asyncio

from src.core.db import SCHEMA_PATH, _parse_schema_statements, apply_schema, generate_id
from src.core.identifier_taken import IDENTIFIER_TAKEN_GUARD, UNIQUE_IDENTIFIER_TYPE_IDS
from tests.db_utils import drop_index
from tests.polymorphic_seeders import archived_entity

pytestmark = [pytest.mark.integration]

#: The type each entity type carries under the index.
UNIQUE_SLUGS = {
    "organization": "org_cannabis_observer",
    "person": "person_cannabis_observer",
    "role": "role_cannabis_observer",
    "role_assignment": "role_assignment_cannabis_observer",
}
#: Types outside the index whose values legitimately repeat across entities.
REPEATABLE_SLUGS = {
    "person": "person_wa_pdc_filer",
    "organization": "org_ubi",
    "role_assignment": "role_wa_pdc",
}


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


async def _identify(conn, entity_id: str, slug: str, value: str) -> None:
    await conn.execute(
        "INSERT INTO identifiers (id, entity_id, entity_identifier_type_id, value)"
        " SELECT $1, $2, t.id, $3 FROM entity_identifier_types t WHERE t.slug = $4",
        generate_id(),
        entity_id,
        value,
        slug,
    )


async def _index_exists(conn) -> bool:
    return await conn.fetchval(
        "SELECT EXISTS (SELECT 1 FROM pg_indexes WHERE schemaname = 'public' AND indexname = $1)",
        IDENTIFIER_TAKEN_GUARD,
    )


def _guard_statement() -> str:
    """The schema.sql ``DO`` block that creates the index."""
    stmts = [
        s
        for s in _parse_schema_statements(SCHEMA_PATH.read_text())
        if re.search(r"^DO \$\$", s, re.M) and IDENTIFIER_TAKEN_GUARD in s
    ]
    assert len(stmts) == 1, stmts
    return stmts[0]


@pytest.mark.parametrize("entity_type", sorted(UNIQUE_SLUGS))
async def test_one_value_on_a_second_entity_is_refused(conn, entity_type):
    a = await archived_entity(conn, entity_type)
    b = await archived_entity(conn, entity_type)
    await _identify(conn, a, UNIQUE_SLUGS[entity_type], "01SITEULID")

    with pytest.raises(asyncpg.UniqueViolationError) as exc:
        await _identify(conn, b, UNIQUE_SLUGS[entity_type], "01SITEULID")
    assert exc.value.constraint_name == IDENTIFIER_TAKEN_GUARD


@pytest.mark.parametrize("entity_type", sorted(UNIQUE_SLUGS))
async def test_one_value_twice_on_one_entity_is_refused(conn, entity_type):
    a = await archived_entity(conn, entity_type)
    await _identify(conn, a, UNIQUE_SLUGS[entity_type], "01SITEULID")

    with pytest.raises(asyncpg.UniqueViolationError):
        await _identify(conn, a, UNIQUE_SLUGS[entity_type], "01SITEULID")


async def test_a_live_entity_is_refused_a_value_an_archived_one_holds(conn):
    """#481: archiving keeps identifiers, so an archived holder still owns the value."""
    archived = await archived_entity(conn, "organization")
    live = generate_id()
    await conn.execute("INSERT INTO organizations (id) VALUES ($1)", live)
    await _identify(conn, archived, "org_cannabis_observer", "01SITEULID")

    with pytest.raises(asyncpg.UniqueViolationError):
        await _identify(conn, live, "org_cannabis_observer", "01SITEULID")


@pytest.mark.parametrize("entity_type", sorted(REPEATABLE_SLUGS))
async def test_other_types_still_repeat_a_value_across_entities(conn, entity_type):
    a = await archived_entity(conn, entity_type)
    b = await archived_entity(conn, entity_type)
    await _identify(conn, a, REPEATABLE_SLUGS[entity_type], "12345")
    await _identify(conn, b, REPEATABLE_SLUGS[entity_type], "12345")


async def test_distinct_values_of_one_type_coexist(conn):
    a = await archived_entity(conn, "person")
    b = await archived_entity(conn, "person")
    await _identify(conn, a, "person_cannabis_observer", "01SITEA")
    await _identify(conn, b, "person_cannabis_observer", "01SITEB")


async def test_the_code_constant_names_the_four_seeded_type_ids(conn):
    seeded = {
        r["id"]
        for r in await conn.fetch(
            "SELECT id FROM entity_identifier_types WHERE slug = ANY($1::text[])",
            list(UNIQUE_SLUGS.values()),
        )
    }
    assert seeded == UNIQUE_IDENTIFIER_TYPE_IDS


async def test_the_index_predicate_names_exactly_the_code_constant(conn):
    indexdef = await conn.fetchval(
        "SELECT indexdef FROM pg_indexes WHERE schemaname = 'public' AND indexname = $1",
        IDENTIFIER_TAKEN_GUARD,
    )
    assert indexdef is not None
    assert indexdef.startswith("CREATE UNIQUE INDEX")
    assert set(re.findall(r"'([0-9A-HJKMNP-TV-Z]{26})'", indexdef)) == UNIQUE_IDENTIFIER_TYPE_IDS


async def _plant_duplicate(conn) -> tuple[str, str]:
    await drop_index(conn, IDENTIFIER_TAKEN_GUARD)
    a = await archived_entity(conn, "person")
    b = await archived_entity(conn, "person")
    await _identify(conn, a, "person_cannabis_observer", "01DUPLICATE")
    await _identify(conn, b, "person_cannabis_observer", "01DUPLICATE")
    return a, b


async def _warnings_while(conn, sql: str) -> list[str]:
    seen: list[str] = []

    def _listen(_conn, message):
        if message.severity == "WARNING":
            seen.append(message.message)

    conn.add_log_listener(_listen)
    try:
        await conn.execute(sql)
        await asyncio.sleep(0)  # listeners are dispatched on the loop
    finally:
        conn.remove_log_listener(_listen)
    return seen


async def test_a_seeded_duplicate_warns_and_skips_the_index(conn):
    """The warn-don't-abort path: a restart applies the schema, so it must not raise."""
    await _plant_duplicate(conn)

    warnings = await _warnings_while(conn, _guard_statement())

    assert not await _index_exists(conn)
    assert len(warnings) == 1
    assert IDENTIFIER_TAKEN_GUARD in warnings[0]
    assert "not created" in warnings[0]


async def test_the_index_builds_once_the_duplicate_is_resolved(conn):
    a, _ = await _plant_duplicate(conn)
    await _warnings_while(conn, _guard_statement())
    await conn.execute("DELETE FROM identifiers WHERE entity_id = $1", a)

    warnings = await _warnings_while(conn, _guard_statement())

    assert warnings == []
    assert await _index_exists(conn)


async def test_apply_schema_completes_over_a_seeded_duplicate(conn):
    """The whole apply — what ExecStartPre runs — survives the duplicate."""
    await _plant_duplicate(conn)

    await apply_schema(conn)

    assert not await _index_exists(conn)
