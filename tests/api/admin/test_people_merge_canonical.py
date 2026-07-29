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
        " (id, person_id, name, name_type, locale, script, visibility, is_canonical,"
        "  reading_of_id)"
        " VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)",
        nid,
        pid,
        name,
        kw.get("name_type", "legal"),
        kw.get("locale"),
        kw.get("script"),
        kw.get("visibility", "public"),
        kw.get("is_canonical", False),
        kw.get("reading_of_id"),
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


async def test_merge_keeps_public_name_absorbed_by_non_public_winner_row(conn):
    """Visibility is part of the claim (CR5 #43).

    Treating any two display types as interchangeable ignored visibility, so a
    `hidden` winner row absorbed a `public` loser row with the same text. The
    loser's canonical is demoted on the way in and its only public name is then
    deleted, leaving the heal nothing to promote — the exact blank-person
    outcome the heal was added to prevent, caused by the dedup that runs
    just before it.
    """
    winner, loser = await _person(conn), await _person(conn)
    await _name(conn, winner, "Old Name", name_type="variant", visibility="hidden")
    await _name(conn, loser, "Old Name", name_type="legal", is_canonical=True)
    await _merge(conn, winner, loser)
    assert (
        await conn.fetchval(
            "SELECT display_name FROM v_person_display_names WHERE person_id=$1", winner
        )
        == "Old Name"
    )


async def test_merge_keeps_non_public_claim_with_same_text(conn):
    """#121: the winner inherits the loser's legal_only / hidden names.

    A `legal_only` claim is not a duplicate of a `public` one carrying the same
    text — the visibility *is* the difference (CR5 #44).
    """
    winner, loser = await _person(conn), await _person(conn)
    await _name(conn, winner, "Jane Doe", name_type="variant", is_canonical=True)
    await _name(conn, loser, "Jane Doe", name_type="legal", visibility="legal_only")
    await _merge(conn, winner, loser)
    rows = await conn.fetch(
        "SELECT name_type, visibility FROM person_names WHERE person_id=$1 ORDER BY name_type",
        winner,
    )
    assert [(r["name_type"], r["visibility"]) for r in rows] == [
        ("legal", "legal_only"),
        ("variant", "public"),
    ]


# --- #309: dedup must not cascade away the loser's reading/romanization rows ---


async def test_merge_transfers_reading_when_parent_deduped(conn):
    """A furigana row survives when its parent legal row is deduped away (#309).

    Winner and loser both hold the same legal name; the loser additionally has a
    `reading` pointing at its legal row. Dedup deletes the loser's legal row —
    the reading must re-point at the winner's surviving legal row (ON DELETE
    CASCADE would otherwise destroy the name-family edge), not vanish.
    """
    winner, loser = await _person(conn), await _person(conn)
    await _name(conn, winner, "山田太郎", name_type="legal", is_canonical=True)
    loser_legal = await _name(conn, loser, "山田太郎", name_type="legal")
    await _name(conn, loser, "やまだたろう", name_type="reading", reading_of_id=loser_legal)
    await _merge(conn, winner, loser)
    rows = await conn.fetch(
        "SELECT r.name AS reading, p.name AS parent"
        " FROM person_names r JOIN person_names p ON p.id = r.reading_of_id"
        " WHERE r.person_id=$1 AND r.name_type='reading'",
        winner,
    )
    assert [(r["reading"], r["parent"]) for r in rows] == [("やまだたろう", "山田太郎")]


async def test_merge_leaves_reading_untouched_when_parent_survives(conn):
    """A reading whose parent is not deduped follows the parent unchanged (#309).

    The loser's legal name has no winner counterpart, so it is reassigned rather
    than deleted; the reading must still point at that same (now winner-owned)
    row, not get needlessly re-pointed elsewhere.
    """
    winner, loser = await _person(conn), await _person(conn)
    await _name(conn, winner, "Robert Smith", name_type="legal", is_canonical=True)
    loser_legal = await _name(conn, loser, "田中花子", name_type="legal")
    await _name(conn, loser, "たなかはなこ", name_type="reading", reading_of_id=loser_legal)
    await _merge(conn, winner, loser)
    parent = await conn.fetchval(
        "SELECT p.id FROM person_names r JOIN person_names p ON p.id = r.reading_of_id"
        " WHERE r.person_id=$1 AND r.name_type='reading'",
        winner,
    )
    assert parent == loser_legal


async def test_merge_drops_duplicate_reading(conn):
    """An identical reading already held by the winner still collapses (#309).

    Both sides carry the same legal row and the same furigana reading. The
    winner keeps exactly one of each — no leak, no orphan.
    """
    winner, loser = await _person(conn), await _person(conn)
    w_legal = await _name(conn, winner, "山田太郎", name_type="legal", is_canonical=True)
    await _name(conn, winner, "やまだたろう", name_type="reading", reading_of_id=w_legal)
    l_legal = await _name(conn, loser, "山田太郎", name_type="legal")
    await _name(conn, loser, "やまだたろう", name_type="reading", reading_of_id=l_legal)
    await _merge(conn, winner, loser)
    assert (
        await conn.fetchval(
            "SELECT count(*) FROM person_names WHERE person_id=$1 AND name_type='reading'", winner
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


# --- #323: curated keep_name_ids must not cascade away a *kept* reading -------
# #309 guarded the dedup DELETE; the curated drop had the same reading_of_id
# ON DELETE CASCADE exposure. If the admin checks a reading but leaves its parent
# unchecked, dropping the parent would silently destroy the explicitly-kept child
# (case C). The guard keeps parents of kept children; unchecked children stay
# dropped (case A).


async def test_curated_merge_keeps_reading_when_parent_unchecked(conn):
    """#323 case C: a kept reading whose parent is unchecked must survive.

    Dropping the parent would cascade the reading away (reading_of_id ON DELETE
    CASCADE) even though the admin explicitly checked it to keep. The guard keeps
    the parent so the kept child survives and stays linked.
    """
    winner, loser = await _person(conn), await _person(conn)
    await _name(conn, winner, "Robert Smith", name_type="legal", is_canonical=True)
    loser_legal = await _name(conn, loser, "山田太郎", name_type="legal")
    loser_reading = await _name(
        conn, loser, "やまだたろう", name_type="reading", reading_of_id=loser_legal
    )
    await merge_person_into(
        conn,
        winner_id=winner,
        loser_id=loser,
        actor_email="admin@test.com",
        keep_name_ids=[loser_reading],  # keep ONLY the reading; parent unchecked
    )
    rows = await conn.fetch(
        "SELECT r.name AS reading, p.name AS parent"
        " FROM person_names r JOIN person_names p ON p.id = r.reading_of_id"
        " WHERE r.person_id=$1 AND r.name_type='reading'",
        winner,
    )
    assert [(r["reading"], r["parent"]) for r in rows] == [("やまだたろう", "山田太郎")]


async def test_curated_merge_reading_repoints_when_kept_parent_dedups(conn):
    """#323 + #309 compose: the guard keeps the unchecked parent, then dedup
    collapses it into the winner's identical legal row and the reading re-points
    at the survivor — no orphan, no duplicate.
    """
    winner, loser = await _person(conn), await _person(conn)
    await _name(conn, winner, "山田太郎", name_type="legal", is_canonical=True)
    loser_legal = await _name(conn, loser, "山田太郎", name_type="legal")
    loser_reading = await _name(
        conn, loser, "やまだたろう", name_type="reading", reading_of_id=loser_legal
    )
    await merge_person_into(
        conn,
        winner_id=winner,
        loser_id=loser,
        actor_email="admin@test.com",
        keep_name_ids=[loser_reading],
    )
    rows = await conn.fetch(
        "SELECT r.name AS reading, p.name AS parent"
        " FROM person_names r JOIN person_names p ON p.id = r.reading_of_id"
        " WHERE r.person_id=$1 AND r.name_type='reading'",
        winner,
    )
    assert [(r["reading"], r["parent"]) for r in rows] == [("やまだたろう", "山田太郎")]
    assert (
        await conn.fetchval(
            "SELECT count(*) FROM person_names WHERE person_id=$1 AND name='山田太郎'", winner
        )
        == 1
    )


async def test_curated_merge_drops_unchecked_reading_keeps_parent(conn):
    """#323 case A stays a deliberate drop: unchecking a reading while keeping its
    parent drops only the reading. The guard resurrects parents of *kept*
    children, never children the admin unchecked.
    """
    winner, loser = await _person(conn), await _person(conn)
    await _name(conn, winner, "Robert Smith", name_type="legal", is_canonical=True)
    loser_legal = await _name(conn, loser, "田中花子", name_type="legal")
    await _name(conn, loser, "たなかはなこ", name_type="reading", reading_of_id=loser_legal)
    await merge_person_into(
        conn,
        winner_id=winner,
        loser_id=loser,
        actor_email="admin@test.com",
        keep_name_ids=[loser_legal],  # keep parent, drop reading
    )
    assert (
        await conn.fetchval(
            "SELECT count(*) FROM person_names WHERE person_id=$1 AND name_type='reading'", winner
        )
        == 0
    )
    assert (
        await conn.fetchval(
            "SELECT count(*) FROM person_names WHERE person_id=$1 AND name='田中花子'", winner
        )
        == 1
    )


async def test_curated_merge_drops_both_reading_and_parent(conn):
    """#323 case B: unchecking both a reading and its parent drops both cleanly.

    The guard only resurrects parents of *kept* children — with neither in the
    keep-set, both go (the parent's DELETE cascades the reading anyway) and no
    NOT-IN/NULL interaction leaves a stray row or errors.
    """
    winner, loser = await _person(conn), await _person(conn)
    await _name(conn, winner, "Robert Smith", name_type="legal", is_canonical=True)
    keeper = await _name(conn, loser, "Bob Smith", name_type="preferred")
    loser_legal = await _name(conn, loser, "田中花子", name_type="legal")
    await _name(conn, loser, "たなかはなこ", name_type="reading", reading_of_id=loser_legal)
    await merge_person_into(
        conn,
        winner_id=winner,
        loser_id=loser,
        actor_email="admin@test.com",
        keep_name_ids=[keeper],  # keep an unrelated name; drop both parent + reading
    )
    assert (
        await conn.fetchval(
            "SELECT count(*) FROM person_names"
            " WHERE person_id=$1 AND name IN ('田中花子', 'たなかはなこ')",
            winner,
        )
        == 0
    )
    winner_names = {
        r["name"]
        for r in await conn.fetch("SELECT name FROM person_names WHERE person_id=$1", winner)
    }
    assert "Bob Smith" in winner_names


# --- #319: citations on sub-entities must not orphan on merge -----------------


async def _cite_name(conn, name_id, url, field="name"):
    cid = generate_id()
    await conn.execute(
        "INSERT INTO citations (id, entity_type, entity_id, field_name, url, title)"
        " VALUES ($1,'person_name',$2,$3,$4,'t')",
        cid,
        name_id,
        field,
        url,
    )
    return cid


async def test_merge_rehomes_citation_off_deduped_name(conn):
    """A citation on a loser name that dedups into a winner name follows the winner."""
    winner, loser = await _person(conn), await _person(conn)
    w_name = await _name(conn, winner, "Robert Smith", name_type="legal", is_canonical=True)
    l_name = await _name(conn, loser, "Robert Smith", name_type="legal")
    cid = await _cite_name(conn, l_name, "https://s/dup")
    await _merge(conn, winner, loser)
    # Citation re-homed onto the surviving winner name; not orphaned.
    owner = await conn.fetchval("SELECT entity_id FROM citations WHERE id=$1", cid)
    assert owner == w_name


async def test_merge_keeps_citation_on_repointed_name(conn):
    """A loser name with no winner duplicate keeps its id → its citation survives."""
    winner, loser = await _person(conn), await _person(conn)
    await _name(conn, winner, "Robert Smith", name_type="legal", is_canonical=True)
    l_name = await _name(conn, loser, "Bob Smith", name_type="preferred")
    cid = await _cite_name(conn, l_name, "https://s/keep")
    await _merge(conn, winner, loser)
    owner = await conn.fetchval("SELECT entity_id FROM citations WHERE id=$1", cid)
    assert owner == l_name  # same name id, now under winner
    assert await conn.fetchval("SELECT person_id FROM person_names WHERE id=$1", l_name) == winner


async def test_merge_drops_citation_on_curated_dropped_name(conn):
    """A curated-dropped loser name's citation is deleted (name assertion gone)."""
    winner, loser = await _person(conn), await _person(conn)
    keep = await _name(conn, loser, "Keep Me", name_type="legal", is_canonical=True)
    drop = await _name(conn, loser, "Drop Me", name_type="variant")
    await _name(conn, winner, "Winner Name", name_type="legal", is_canonical=True)
    cid = await _cite_name(conn, drop, "https://s/drop")
    await merge_person_into(
        conn, winner_id=winner, loser_id=loser, actor_email="a@test.com", keep_name_ids=[keep]
    )
    assert await conn.fetchval("SELECT count(*) FROM citations WHERE id=$1", cid) == 0


async def test_merge_drops_loser_event_citations(conn):
    """The loser's entity_events dangle on merge; their citations are dropped, not orphaned."""
    winner, loser = await _person(conn), await _person(conn)
    await _name(conn, winner, "W", is_canonical=True)
    await _name(conn, loser, "L", is_canonical=True)
    eid = generate_id()
    await conn.execute(
        "INSERT INTO entity_events (id, entity_type, entity_id, event_type_id)"
        " VALUES ($1,'person',$2,(SELECT id FROM entity_event_types WHERE applies_to IN"
        " ('person','both') LIMIT 1))",
        eid,
        loser,
    )
    cid = generate_id()
    await conn.execute(
        "INSERT INTO citations (id, entity_type, entity_id, url, title)"
        " VALUES ($1,'entity_event',$2,'https://s/evt','t')",
        cid,
        eid,
    )
    await _merge(conn, winner, loser)
    assert await conn.fetchval("SELECT count(*) FROM citations WHERE id=$1", cid) == 0
