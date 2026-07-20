"""Tests for the duplicate-assignment audit (#311).

The producer-side start_date correction path used to mint a new assignment and
orphan the previously anchored row. Findings are overlapping active pairs for
the same (person, role), both dated:

- ``deepened_start`` — the #311 signature: the wider (earlier-start) row was
  created *later* — a producer correction; auto-mergeable.
- ``subsumed`` — the wider row provably covers the narrower one; auto-mergeable.
- ``overlapping_review`` — any other overlap; report only.

``--execute`` merges auto-mergeable pairs: side data moves to the survivor,
notes concatenate, the orphan is archived (never deleted) with a provenance
note. Non-overlapping tenures (returning legislators) and NULL-start rows are
never flagged.
"""

import datetime

import pytest
import pytest_asyncio

from scripts.audit_assignment_duplicates import find_duplicates, run_audit
from src.core.db import generate_id

pytestmark = [
    pytest.mark.integration,
]


@pytest_asyncio.fixture(loop_scope="session")
async def db(db_pool):
    async with db_pool.acquire() as conn:
        tr = conn.transaction()
        await tr.start()
        try:
            yield conn
        finally:
            await tr.rollback()


@pytest_asyncio.fixture(loop_scope="session")
async def pair_base(db):
    """Person + role shared by a duplicate pair."""
    org_id = generate_id()
    role_id = generate_id()
    person_id = generate_id()
    await db.execute("INSERT INTO organizations (id) VALUES ($1)", org_id)
    await db.execute(
        "INSERT INTO roles (id, organization_id, title) VALUES ($1,$2,'Dup Audit Role')",
        role_id,
        org_id,
    )
    await db.execute("INSERT INTO people (id) VALUES ($1)", person_id)
    return {"person_id": person_id, "role_id": role_id, "org_id": org_id}


async def _seed_assignment(
    db,
    base,
    *,
    start,
    end=None,
    is_current=False,
    created_at="2026-01-01",
    notes=None,
):
    aid = generate_id()
    await db.execute(
        """INSERT INTO role_assignments
               (id, person_id, role_id, start_date, end_date, is_current, created_at, notes)
           VALUES ($1, $2, $3, $4, $5, $6, $7::timestamptz, $8)""",
        aid,
        base["person_id"],
        base["role_id"],
        datetime.date.fromisoformat(start) if start else None,
        datetime.date.fromisoformat(end) if end else None,
        is_current,
        datetime.datetime.fromisoformat(created_at).replace(tzinfo=datetime.UTC),
        notes,
    )
    return aid


def _pairs(findings, category):
    return {(f["survivor_id"], f["orphan_id"]) for f in findings[category]}


async def test_deepened_start_flagged(db, pair_base):
    """The issue's Slatter case: later-created row with a deepened start."""
    orphan = await _seed_assignment(
        db, pair_base, start="2019-01-01", is_current=True, created_at="2025-01-01"
    )
    survivor = await _seed_assignment(
        db, pair_base, start="2017-01-01", end="2024-12-31", created_at="2026-07-20"
    )
    findings = await find_duplicates(db)
    assert (survivor, orphan) in _pairs(findings, "deepened_start")


async def test_subsumed_flagged(db, pair_base):
    """A wider window provably covering a narrower one is auto-mergeable."""
    survivor = await _seed_assignment(
        db, pair_base, start="2013-01-01", end="2024-12-31", created_at="2024-01-01"
    )
    orphan = await _seed_assignment(
        db, pair_base, start="2015-01-01", end="2016-12-31", created_at="2025-01-01"
    )
    findings = await find_duplicates(db)
    # Not deepened (survivor created first) but provably subsumed.
    assert (survivor, orphan) in _pairs(findings, "subsumed")


async def test_ambiguous_overlap_report_only(db, pair_base):
    """Unprovable coverage (unknown end on the wider row) → review, never merged."""
    survivor = await _seed_assignment(
        db, pair_base, start="2017-01-01", end=None, is_current=False, created_at="2024-01-01"
    )
    orphan = await _seed_assignment(
        db, pair_base, start="2019-01-01", end="2022-01-01", created_at="2025-01-01"
    )
    findings = await find_duplicates(db)
    assert (survivor, orphan) in _pairs(findings, "overlapping_review")
    await run_audit(db, execute=True)
    row = await db.fetchrow("SELECT archived_at FROM role_assignments WHERE id=$1", orphan)
    assert row["archived_at"] is None  # not touched


async def test_non_overlapping_terms_not_flagged(db, pair_base):
    """Returning legislator: two disjoint terms are legitimate, never flagged."""
    await _seed_assignment(db, pair_base, start="2013-01-14", end="2016-12-31")
    await _seed_assignment(db, pair_base, start="2021-01-11", end="2024-12-31")
    findings = await find_duplicates(db)
    for category, rows in findings.items():
        for f in rows:
            assert f["person_id"] != pair_base["person_id"], category


async def test_null_start_rows_ignored(db, pair_base):
    """Undated tenures coexist with dated ones by design (#289) — never flagged."""
    await _seed_assignment(db, pair_base, start=None, is_current=True)
    await _seed_assignment(db, pair_base, start="2021-01-11", end="2024-12-31")
    findings = await find_duplicates(db)
    for category, rows in findings.items():
        for f in rows:
            assert f["person_id"] != pair_base["person_id"], category


async def test_execute_merges_and_archives(db, pair_base):
    """--execute moves side data to the survivor and archives the orphan."""
    orphan = await _seed_assignment(
        db,
        pair_base,
        start="2019-01-01",
        is_current=True,
        created_at="2025-01-01",
        notes="orphan curation note",
    )
    survivor = await _seed_assignment(
        db, pair_base, start="2017-01-01", end="2024-12-31", created_at="2026-07-20"
    )
    lt_id = await db.fetchval("SELECT id FROM link_types LIMIT 1")
    shared = generate_id()
    orphan_only = generate_id()
    await db.execute(
        "INSERT INTO links (id, entity_type, entity_id, url, link_type_id)"
        " VALUES ($1,'role_assignment',$2,'https://dup.example/shared',$3)",
        shared,
        orphan,
        lt_id,
    )
    await db.execute(
        "INSERT INTO links (id, entity_type, entity_id, url, link_type_id)"
        " VALUES ($1,'role_assignment',$2,'https://dup.example/shared',$3)",
        generate_id(),
        survivor,
        lt_id,
    )
    await db.execute(
        "INSERT INTO links (id, entity_type, entity_id, url, link_type_id)"
        " VALUES ($1,'role_assignment',$2,'https://dup.example/orphan-only',$3)",
        orphan_only,
        orphan,
        lt_id,
    )

    await run_audit(db, execute=True)

    row = await db.fetchrow("SELECT archived_at, notes FROM role_assignments WHERE id=$1", orphan)
    assert row["archived_at"] is not None
    assert "#311" in row["notes"]
    survivor_row = await db.fetchrow("SELECT notes FROM role_assignments WHERE id=$1", survivor)
    assert "orphan curation note" in survivor_row["notes"]
    moved = await db.fetchval("SELECT entity_id FROM links WHERE id=$1", orphan_only)
    assert moved == survivor
    unmoved = await db.fetchval("SELECT entity_id FROM links WHERE id=$1", shared)
    assert unmoved == orphan  # duplicate stayed on the archived row
    survivor_links = await db.fetch(
        "SELECT url FROM links WHERE entity_type='role_assignment' AND entity_id=$1", survivor
    )
    assert len(survivor_links) == 2  # shared (pre-existing) + orphan-only


async def test_dry_run_changes_nothing(db, pair_base):
    orphan = await _seed_assignment(
        db, pair_base, start="2019-01-01", is_current=True, created_at="2025-01-01"
    )
    await _seed_assignment(
        db, pair_base, start="2017-01-01", end="2024-12-31", created_at="2026-07-20"
    )
    await run_audit(db, execute=False)
    row = await db.fetchrow("SELECT archived_at FROM role_assignments WHERE id=$1", orphan)
    assert row["archived_at"] is None


async def test_archived_rows_ignored(db, pair_base):
    await _seed_assignment(db, pair_base, start="2019-01-01", is_current=True)
    aid = await _seed_assignment(
        db, pair_base, start="2017-01-01", end="2024-12-31", created_at="2026-07-20"
    )
    await db.execute("UPDATE role_assignments SET archived_at=NOW() WHERE id=$1", aid)
    findings = await find_duplicates(db)
    for category, rows in findings.items():
        for f in rows:
            assert f["person_id"] != pair_base["person_id"], category


async def test_chain_merge_never_touches_archived_rows(db, pair_base):
    """CR round 1 (#311): a merge whose row was archived by an earlier pair is skipped.

    Chain a⊃b, b⊃c with a and c disjoint: pair (a,b) archives b; pair (b,c) must
    then be skipped — c stays active and its side data must not move onto the
    archived b.
    """
    c = await _seed_assignment(
        db, pair_base, start="2017-01-01", end="2018-12-31", created_at="2024-01-01"
    )
    b = await _seed_assignment(
        db, pair_base, start="2015-01-01", end="2020-12-31", created_at="2025-01-01"
    )
    a = await _seed_assignment(
        db, pair_base, start="2013-01-01", end="2016-06-30", created_at="2026-07-20"
    )
    lt_id = await db.fetchval("SELECT id FROM link_types LIMIT 1")
    link_c = generate_id()
    await db.execute(
        "INSERT INTO links (id, entity_type, entity_id, url, link_type_id)"
        " VALUES ($1,'role_assignment',$2,'https://dup.example/chain-c',$3)",
        link_c,
        c,
        lt_id,
    )

    findings = await find_duplicates(db)
    assert (a, b) in _pairs(findings, "deepened_start")
    assert (b, c) in _pairs(findings, "deepened_start")

    await run_audit(db, execute=True)

    rows = {
        r["id"]: r["archived_at"]
        for r in await db.fetch(
            "SELECT id, archived_at FROM role_assignments WHERE person_id=$1",
            pair_base["person_id"],
        )
    }
    assert rows[b] is not None  # merged into a
    assert rows[a] is None
    assert rows[c] is None  # (b,c) skipped — b was already archived this run
    assert await db.fetchval("SELECT entity_id FROM links WHERE id=$1", link_c) == c
