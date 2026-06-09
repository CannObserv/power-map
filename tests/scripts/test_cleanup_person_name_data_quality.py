"""Tests for the data-quality cleanup script (issue #135 triage).

Three transformation kinds:
- StripSuffix: in-place name UPDATE (e.g. "Linda Thompson (2)" -> "Linda Thompson")
- SplitName:   in-place UPDATE legal row + INSERT sibling row (variant or maiden)
- MergePerson: delegates to merge_person_into

Plus a dry-run wrapper that runs everything inside a savepoint.
"""

import asyncpg
import pytest
import pytest_asyncio

from scripts.cleanup_person_name_data_quality import (
    MergePerson,
    SplitName,
    StripSuffix,
    apply_action,
    run_cleanup,
)
from src.core.db import generate_id

pytestmark = [
    pytest.mark.integration,
]


@pytest_asyncio.fixture(loop_scope="session")
async def db(db_pool):
    async with db_pool.acquire() as conn:
        tr = conn.transaction()
        await tr.start()
        # Seed FK lookup rows the cleanup script needs for new variant inserts.
        await conn.execute(
            "INSERT INTO iso15924_scripts (code, numeric_code, name)"
            " VALUES ('Latn', 215, 'Latin') ON CONFLICT (code) DO NOTHING"
        )
        await conn.execute(
            "INSERT INTO bcp47_locales (code, language, script, region, display_name)"
            " VALUES ('en-US', 'en', 'Latn', 'US', 'English (US)')"
            " ON CONFLICT (code) DO NOTHING"
        )
        try:
            yield conn
        finally:
            await tr.rollback()


async def _seed_person_with_name(
    conn: asyncpg.Connection,
    *,
    name: str,
    name_type: str = "legal",
    locale: str = "en-US",
    script: str = "Latn",
) -> tuple[str, str]:
    pid = generate_id()
    nid = generate_id()
    await conn.execute("INSERT INTO people (id) VALUES ($1)", pid)
    await conn.execute(
        "INSERT INTO person_names "
        "(id, person_id, name, name_type, is_canonical, locale, script)"
        " VALUES ($1, $2, $3, $4, TRUE, $5, $6)",
        nid,
        pid,
        name,
        name_type,
        locale,
        script,
    )
    return pid, nid


# ---- StripSuffix -----------------------------------------------------------


async def test_strip_suffix_updates_name_in_place(db):
    pid, nid = await _seed_person_with_name(db, name="Linda Thompson (2)")
    await apply_action(db, StripSuffix(name_id=nid, new_name="Linda Thompson", strip=" (2)"))
    row = await db.fetchrow("SELECT name FROM person_names WHERE id=$1", nid)
    assert row["name"] == "Linda Thompson"


async def test_strip_suffix_preserves_other_columns(db):
    pid, nid = await _seed_person_with_name(
        db,
        name="Mary Brown (2)",
        locale="en-US",
        script="Latn",
    )
    await apply_action(db, StripSuffix(name_id=nid, new_name="Mary Brown", strip=" (2)"))
    row = await db.fetchrow(
        "SELECT name, name_type, is_canonical, locale, script FROM person_names WHERE id=$1",
        nid,
    )
    assert row["name"] == "Mary Brown"
    assert row["name_type"] == "legal"
    assert row["is_canonical"] is True
    assert row["locale"] == "en-US"
    assert row["script"] == "Latn"


async def test_strip_suffix_raises_when_name_id_missing(db):
    """Defensive — surface stale-config issues rather than silently no-op."""
    with pytest.raises(ValueError, match="not found"):
        await apply_action(
            db,
            StripSuffix(name_id="nonexistent", new_name="x", strip=""),
        )


# ---- SplitName -------------------------------------------------------------


async def test_split_name_updates_legal_and_inserts_variant(db):
    pid, nid = await _seed_person_with_name(db, name="Rene or Renee")
    await apply_action(
        db,
        SplitName(
            name_id=nid,
            new_legal_name="Rene",
            sibling_name="Renee",
            sibling_type="variant",
        ),
    )
    rows = await db.fetch(
        "SELECT name, name_type, is_canonical FROM person_names "
        "WHERE person_id=$1 ORDER BY name_type",
        pid,
    )
    assert [(r["name"], r["name_type"], r["is_canonical"]) for r in rows] == [
        ("Rene", "legal", True),
        ("Renee", "variant", False),
    ]


async def test_split_name_inserts_maiden_with_correct_type(db):
    pid, nid = await _seed_person_with_name(db, name="Virginia (Webber) Hoyer")
    await apply_action(
        db,
        SplitName(
            name_id=nid,
            new_legal_name="Virginia Hoyer",
            sibling_name="Virginia Webber",
            sibling_type="maiden",
        ),
    )
    rows = await db.fetch(
        "SELECT name, name_type FROM person_names WHERE person_id=$1 ORDER BY name_type",
        pid,
    )
    assert [(r["name"], r["name_type"]) for r in rows] == [
        ("Virginia Hoyer", "legal"),
        ("Virginia Webber", "maiden"),
    ]


async def test_split_name_inherits_locale_and_script_on_sibling(db):
    pid, nid = await _seed_person_with_name(
        db,
        name="Victor (Vic) Colman",
        locale="en-US",
        script="Latn",
    )
    await apply_action(
        db,
        SplitName(
            name_id=nid,
            new_legal_name="Victor Colman",
            sibling_name="Vic Colman",
            sibling_type="variant",
        ),
    )
    sibling = await db.fetchrow(
        "SELECT locale, script FROM person_names WHERE person_id=$1 AND name_type='variant'",
        pid,
    )
    assert sibling["locale"] == "en-US"
    assert sibling["script"] == "Latn"


# ---- MergePerson -----------------------------------------------------------


async def test_merge_person_action_consolidates_two_records(db):
    """The Jody-pair scenario: two records get split, then merged."""
    # Person A: split Jodi/Jody → legal:Jodi + variant:Jody
    pid_a, nid_a = await _seed_person_with_name(db, name="Jodi/Jody")
    # Person B: split Jody or Jodi → legal:Jody + variant:Jodi
    pid_b, nid_b = await _seed_person_with_name(db, name="Jody or Jodi")

    actions = [
        SplitName(
            name_id=nid_a, new_legal_name="Jodi", sibling_name="Jody", sibling_type="variant"
        ),
        SplitName(
            name_id=nid_b, new_legal_name="Jody", sibling_name="Jodi", sibling_type="variant"
        ),
        MergePerson(
            winner_id=pid_a,
            loser_id=pid_b,
            rationale="Jodi/Jody and Jody or Jodi are the same person",
        ),
    ]
    for a in actions:
        await apply_action(db, a)

    # Loser is gone.
    assert await db.fetchval("SELECT id FROM people WHERE id=$1", pid_b) is None
    # Winner has the canonical Jodi + Jody variant.
    rows = await db.fetch(
        "SELECT name, name_type, is_canonical FROM person_names "
        "WHERE person_id=$1 ORDER BY name_type",
        pid_a,
    )
    assert [(r["name"], r["name_type"], r["is_canonical"]) for r in rows] == [
        ("Jodi", "legal", True),
        ("Jody", "variant", False),
    ]


# ---- run_cleanup wrapper ---------------------------------------------------


async def test_run_cleanup_dry_run_rolls_back(db):
    pid, nid = await _seed_person_with_name(db, name="Linda Thompson (2)")
    actions = [
        StripSuffix(name_id=nid, new_name="Linda Thompson", strip=" (2)"),
    ]
    stats = await run_cleanup(db, actions=actions, dry_run=True)
    assert stats.applied == 1
    assert stats.dry_run is True
    row = await db.fetchrow("SELECT name FROM person_names WHERE id=$1", nid)
    # Rolled back — name is unchanged.
    assert row["name"] == "Linda Thompson (2)"


async def test_run_cleanup_execute_persists(db):
    pid, nid = await _seed_person_with_name(db, name="Linda Thompson (2)")
    actions = [
        StripSuffix(name_id=nid, new_name="Linda Thompson", strip=" (2)"),
    ]
    stats = await run_cleanup(db, actions=actions, dry_run=False)
    assert stats.applied == 1
    assert stats.dry_run is False
    row = await db.fetchrow("SELECT name FROM person_names WHERE id=$1", nid)
    assert row["name"] == "Linda Thompson"


async def test_run_cleanup_counts_each_action_kind(db):
    pid_strip, nid_strip = await _seed_person_with_name(db, name="X (2)")
    pid_split, nid_split = await _seed_person_with_name(db, name="A or B")
    actions = [
        StripSuffix(name_id=nid_strip, new_name="X", strip=" (2)"),
        SplitName(name_id=nid_split, new_legal_name="A", sibling_name="B", sibling_type="variant"),
    ]
    stats = await run_cleanup(db, actions=actions, dry_run=False)
    assert stats.kind_counts == {"StripSuffix": 1, "SplitName": 1}


async def test_run_cleanup_rolls_back_on_action_error(db):
    """All-or-nothing — if one action raises, none persist."""
    pid, nid = await _seed_person_with_name(db, name="Linda Thompson (2)")
    actions = [
        StripSuffix(name_id=nid, new_name="Linda Thompson", strip=" (2)"),
        StripSuffix(name_id="nonexistent-id", new_name="x", strip=""),
    ]
    with pytest.raises(ValueError):
        await run_cleanup(db, actions=actions, dry_run=False)
    row = await db.fetchrow("SELECT name FROM person_names WHERE id=$1", nid)
    # First action rolled back too.
    assert row["name"] == "Linda Thompson (2)"
