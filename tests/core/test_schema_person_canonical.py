"""Integration tests for the person canonical-name constraints (issue #308, Option A).

`is_canonical` is the **display pointer**: exactly one per person, and always
visible. Two constraints carry that, replacing the per-(name_type, locale,
script) key that let one person hold several canonical rows and forced
`v_person_display_names` to disambiguate them.

This mirrors `uq_org_canonical_name`, which was itself narrowed from
`(organization_id, name_type)` to `(organization_id)` for the same reason.
"""

import asyncpg
import pytest
import pytest_asyncio

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


async def _person(conn):
    pid = generate_id()
    await conn.execute("INSERT INTO people (id) VALUES ($1)", pid)
    return pid


async def _add(conn, pid, name, **kw):
    nid = generate_id()
    await conn.execute(
        "INSERT INTO person_names"
        " (id, person_id, name, name_type, locale, script, visibility, is_canonical)"
        " VALUES ($1, $2, $3, $4, $5, $6, $7, $8)",
        nid,
        pid,
        name,
        kw.get("name_type", "legal"),
        kw.get("locale"),
        kw.get("script"),
        kw.get("visibility", "public"),
        kw.get("is_canonical", False),
    )
    return nid


# --- one canonical per person ----------------------------------------------


async def test_second_canonical_same_name_type_rejected(conn):
    pid = await _person(conn)
    await _add(conn, pid, "Alice Smith", is_canonical=True)
    with pytest.raises(asyncpg.exceptions.UniqueViolationError):
        await _add(conn, pid, "Alice J. Smith", is_canonical=True)


async def test_second_canonical_different_name_type_rejected(conn):
    """The case the old per-name_type key allowed — the source of view duplication."""
    pid = await _person(conn)
    await _add(conn, pid, "Alice Smith", name_type="legal", is_canonical=True)
    with pytest.raises(asyncpg.exceptions.UniqueViolationError):
        await _add(conn, pid, "Alice", name_type="preferred", is_canonical=True)


async def test_second_canonical_different_locale_script_rejected(conn):
    """Also allowed by the old key: a Latn and a Jpan `legal` row both canonical."""
    pid = await _person(conn)
    await _add(conn, pid, "Yamada Taro", locale="en", script="Latn", is_canonical=True)
    with pytest.raises(asyncpg.exceptions.UniqueViolationError):
        await _add(conn, pid, "山田太郎", locale="ja", script="Jpan", is_canonical=True)


async def test_canonical_names_are_independent_across_people(conn):
    a, b = await _person(conn), await _person(conn)
    await _add(conn, a, "Alice", is_canonical=True)
    await _add(conn, b, "Bob", is_canonical=True)  # must not conflict
    assert (
        await conn.fetchval(
            "SELECT count(*) FROM person_names WHERE person_id = ANY($1) AND is_canonical",
            [a, b],
        )
        == 2
    )


async def test_many_non_canonical_names_allowed(conn):
    pid = await _person(conn)
    await _add(conn, pid, "Alice Smith", is_canonical=True)
    await _add(conn, pid, "Alice", name_type="preferred")
    await _add(conn, pid, "A. Smith", name_type="alias")
    assert await conn.fetchval("SELECT count(*) FROM person_names WHERE person_id=$1", pid) == 3


# --- the canonical name is always displayable -------------------------------


async def test_non_public_canonical_rejected(conn):
    """A canonical row the display view filters out is a contradiction."""
    pid = await _person(conn)
    with pytest.raises(asyncpg.exceptions.CheckViolationError):
        await _add(conn, pid, "Sealed Name", visibility="legal_only", is_canonical=True)


async def test_hidden_canonical_rejected(conn):
    pid = await _person(conn)
    with pytest.raises(asyncpg.exceptions.CheckViolationError):
        await _add(conn, pid, "Hidden Name", visibility="hidden", is_canonical=True)


async def test_canonical_deadname_rejected(conn):
    """trg_deadname_visibility forces legal_only, so the CHECK rejects it.

    Previously this silently produced a canonical row invisible to the view —
    a blank person whose display slot was permanently occupied.
    """
    pid = await _person(conn)
    with pytest.raises(asyncpg.exceptions.CheckViolationError):
        await _add(conn, pid, "Old Name", name_type="deadname", is_canonical=True)


async def test_demoting_then_hiding_is_allowed(conn):
    """Non-public is fine as long as the row is not the display pointer."""
    pid = await _person(conn)
    nid = await _add(conn, pid, "Sealed Name")
    await conn.execute("UPDATE person_names SET visibility='legal_only' WHERE id=$1", nid)
    assert (
        await conn.fetchval("SELECT visibility FROM person_names WHERE id=$1", nid) == "legal_only"
    )


async def test_cannot_hide_the_canonical_name(conn):
    pid = await _person(conn)
    nid = await _add(conn, pid, "Alice", is_canonical=True)
    with pytest.raises(asyncpg.exceptions.CheckViolationError):
        await conn.execute("UPDATE person_names SET visibility='hidden' WHERE id=$1", nid)


# --- the view no longer needs to disambiguate -------------------------------


async def test_view_returns_exactly_one_row_per_person(conn):
    pid = await _person(conn)
    await _add(conn, pid, "Alice Smith", is_canonical=True)
    await _add(conn, pid, "Alice", name_type="preferred")
    rows = await conn.fetch(
        "SELECT display_name FROM v_person_display_names WHERE person_id=$1", pid
    )
    assert len(rows) == 1
    assert rows[0]["display_name"] == "Alice Smith"


async def test_view_keeps_person_with_no_names(conn):
    pid = await _person(conn)
    rows = await conn.fetch(
        "SELECT display_name FROM v_person_display_names WHERE person_id=$1", pid
    )
    assert len(rows) == 1
    assert rows[0]["display_name"] is None


# --- reading_of_id: the family link is an explicit edge, not an implied group -


async def test_reading_row_links_to_its_source_name(conn):
    """`reading`/`romanization` rows point at their source via reading_of_id.

    This is the capability the narrowed canonical key does *not* remove: name
    families are modelled as FK edges between rows, independent of which row is
    the display pointer.
    """
    pid = await _person(conn)
    legal = await _add(conn, pid, "山田太郎", name_type="legal", is_canonical=True)
    nid = generate_id()
    await conn.execute(
        "INSERT INTO person_names"
        " (id, person_id, name, name_type, locale, script, reading_of_id)"
        " VALUES ($1, $2, 'Yamada Taro', 'romanization', 'ja', 'Latn', $3)",
        nid,
        pid,
        legal,
    )
    assert await conn.fetchval("SELECT reading_of_id FROM person_names WHERE id=$1", nid) == legal
    # The romanization is a sibling row, not a competing display pointer.
    assert (
        await conn.fetchval(
            "SELECT display_name FROM v_person_display_names WHERE person_id=$1", pid
        )
        == "山田太郎"
    )


async def test_deleting_source_name_cascades_to_its_readings(conn):
    """ON DELETE CASCADE — a reading cannot outlive the name it reads."""
    pid = await _person(conn)
    legal = await _add(conn, pid, "山田太郎", name_type="legal", is_canonical=True)
    nid = generate_id()
    await conn.execute(
        "INSERT INTO person_names (id, person_id, name, name_type, reading_of_id)"
        " VALUES ($1, $2, 'Yamada Taro', 'romanization', $3)",
        nid,
        pid,
        legal,
    )
    await conn.execute("DELETE FROM person_names WHERE id=$1", legal)
    assert await conn.fetchval("SELECT count(*) FROM person_names WHERE id=$1", nid) == 0
