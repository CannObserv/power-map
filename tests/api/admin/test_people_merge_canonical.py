"""Merge must preserve the person-name invariants #308 established (CR4 #29/#30).

Merge is the last mutation that can leave a person without a display pointer:
it demotes the loser's canonical unconditionally, and before this change it
promoted nothing on the winner. The observation path, the name-delete path and
the one-off backfill all repair that state; merge did not.

It also deduplicated by name string alone, which contradicts the identity-based
dedup `write_names` adopted in CR3 #22 — a `legal` and an `mrz` row can carry
the same text while being different claims.
"""

import pytest
import pytest_asyncio

from src.api.admin.people_merge import merge_person_into
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


async def _name(conn, pid, name, **kw):
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


async def _merge(conn, winner, loser):
    await merge_person_into(
        conn,
        winner_id=winner,
        loser_id=loser,
        actor_email="admin@test.com",
    )


# --- #29: the winner must end up displaying -------------------------------


async def test_merge_heals_canonical_less_winner(conn):
    """The loser's canonical is demoted on the way in — something must replace it.

    Production holds 567 canonical-less people; merging a perfectly good name
    into one of them left the merged person blank.
    """
    winner, loser = await _person(conn), await _person(conn)
    await _name(conn, winner, "Robert Smith", name_type="legal")  # not canonical
    await _name(conn, loser, "Bob Smith", name_type="preferred", is_canonical=True)
    await _merge(conn, winner, loser)
    assert await conn.fetchval(
        "SELECT display_name FROM v_person_display_names WHERE person_id=$1", winner
    )


async def test_merge_leaves_exactly_one_canonical(conn):
    """Both sides canonical — the invariant must survive the reassignment."""
    winner, loser = await _person(conn), await _person(conn)
    await _name(conn, winner, "Robert Smith", is_canonical=True)
    await _name(conn, loser, "Bob Smith", name_type="preferred", is_canonical=True)
    await _merge(conn, winner, loser)
    assert (
        await conn.fetchval(
            "SELECT count(*) FROM person_names WHERE person_id=$1 AND is_canonical", winner
        )
        == 1
    )


async def test_merge_keeps_winner_existing_canonical(conn):
    """The heal must not displace a canonical the winner already had."""
    winner, loser = await _person(conn), await _person(conn)
    await _name(conn, winner, "Robert Smith", is_canonical=True)
    await _name(conn, loser, "Bob Smith", name_type="preferred")
    await _merge(conn, winner, loser)
    assert (
        await conn.fetchval(
            "SELECT display_name FROM v_person_display_names WHERE person_id=$1", winner
        )
        == "Robert Smith"
    )


async def test_merge_leaves_deadname_only_winner_blank(conn):
    """A person carrying only a deadname stays deliberately blank, not promoted."""
    winner, loser = await _person(conn), await _person(conn)
    await _name(conn, winner, "Old Name", name_type="deadname")
    await _name(conn, loser, "Hidden", name_type="alias", visibility="hidden")
    await _merge(conn, winner, loser)
    assert (
        await conn.fetchval(
            "SELECT display_name FROM v_person_display_names WHERE person_id=$1", winner
        )
        is None
    )


# --- #30: dedup is by identity, not by name string ------------------------


async def test_merge_keeps_same_text_different_name_type(conn):
    """A `legal` and an `mrz` row can share text — dropping one is data loss.

    This is CR3 #22 applied to the merge path: `write_names` dedups on
    (name, name_type, locale, script) precisely because the string alone does
    not identify a name claim.
    """
    winner, loser = await _person(conn), await _person(conn)
    await _name(conn, winner, "YAMADA TARO", name_type="legal", is_canonical=True)
    await _name(conn, loser, "YAMADA TARO", name_type="mrz")
    await _merge(conn, winner, loser)
    assert (
        await conn.fetchval(
            "SELECT count(*) FROM person_names WHERE person_id=$1 AND name_type='mrz'", winner
        )
        == 1
    )


async def test_merge_keeps_same_text_different_script(conn):
    """Script is part of the identity too — a Latn and a Jpan row are distinct."""
    winner, loser = await _person(conn), await _person(conn)
    await _name(conn, winner, "Yamada Taro", locale="en", script="Latn", is_canonical=True)
    await _name(conn, loser, "Yamada Taro", locale="ja", script="Jpan")
    await _merge(conn, winner, loser)
    assert (
        await conn.fetchval(
            "SELECT count(*) FROM person_names WHERE person_id=$1 AND script='Jpan'", winner
        )
        == 1
    )


async def test_merge_still_drops_true_duplicates(conn):
    """Identical identity on both sides collapses to one row, as before."""
    winner, loser = await _person(conn), await _person(conn)
    await _name(conn, winner, "Robert Smith", name_type="legal", is_canonical=True)
    await _name(conn, loser, "Robert Smith", name_type="legal")
    await _merge(conn, winner, loser)
    assert (
        await conn.fetchval(
            "SELECT count(*) FROM person_names WHERE person_id=$1 AND name='Robert Smith'", winner
        )
        == 1
    )


async def test_merge_collapses_same_text_across_display_name_types(conn):
    """Two ordinary display types with the same text are redundant, not distinct.

    The counterpart to the mrz/script cases above, and the reason the dedup key
    is not simply the four-column identity: consolidating two records that were
    each split into legal + variant would otherwise leave the winner holding
    `Jody` as both. Regression guard for
    tests/scripts/test_cleanup_person_name_data_quality.py.
    """
    winner, loser = await _person(conn), await _person(conn)
    await _name(conn, winner, "Jodi", name_type="legal", is_canonical=True)
    await _name(conn, winner, "Jody", name_type="variant")
    await _name(conn, loser, "Jody", name_type="legal")
    await _name(conn, loser, "Jodi", name_type="variant")
    await _merge(conn, winner, loser)
    rows = await conn.fetch(
        "SELECT name, name_type FROM person_names WHERE person_id=$1 ORDER BY name_type", winner
    )
    assert [(r["name"], r["name_type"]) for r in rows] == [("Jodi", "legal"), ("Jody", "variant")]
