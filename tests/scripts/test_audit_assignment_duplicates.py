"""Tests for the duplicate-assignment audit (#311).

The producer-side start_date correction path used to mint a new assignment and
orphan the previously anchored row. Findings are overlapping active pairs for
the same (person, role), both dated:

- ``deepened_start`` — the #311 signature: the wider (earlier-start) row was
  created *later* — a producer correction; auto-mergeable **only when it also
  proves coverage** (#476).
- ``subsumed`` — the wider row provably covers the narrower one; auto-mergeable.
- ``overlapping_review`` — any other overlap, including a later-created row
  whose window does not cover the orphan's; report only.

``--execute`` merges auto-mergeable pairs: side data moves to the survivor,
notes concatenate, the orphan is archived (never deleted) with a provenance
note. Non-overlapping tenures (returning legislators) and NULL-start rows are
never flagged.
"""

import datetime

import pytest
import pytest_asyncio

from scripts.audit_assignment_duplicates import _merge_pair, find_duplicates, run_audit
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
    """Later-created *and* provably covering: the auto-mergeable #311 signature."""
    orphan = await _seed_assignment(
        db, pair_base, start="2019-01-01", end="2020-12-31", created_at="2025-01-01"
    )
    survivor = await _seed_assignment(
        db, pair_base, start="2017-01-01", end="2024-12-31", created_at="2026-07-20"
    )
    findings = await find_duplicates(db)
    assert (survivor, orphan) in _pairs(findings, "deepened_start")


async def test_deepened_start_current_survivor_covers_dated_orphan(db, pair_base):
    """An open, ``is_current`` survivor covers a dated orphan — same proof ``subsumed`` uses."""
    orphan = await _seed_assignment(
        db, pair_base, start="2019-01-01", end="2020-12-31", created_at="2025-01-01"
    )
    survivor = await _seed_assignment(
        db, pair_base, start="2017-01-01", is_current=True, created_at="2026-07-20"
    )
    findings = await find_duplicates(db)
    assert (survivor, orphan) in _pairs(findings, "deepened_start")


async def test_deepened_start_without_coverage_is_review(db, pair_base):
    """#476, the #474 Slatter shape: survivor ends, orphan is open-ended.

    ``created_at`` ordering alone proved nothing about the windows, so this pair
    used to auto-merge and silently discard the longer, still-open tenure. It
    must now fall through to ``overlapping_review`` and survive ``--execute``.
    """
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
    link_id = generate_id()
    await db.execute(
        "INSERT INTO links (id, entity_type, entity_id, url, link_type_id)"
        " VALUES ($1,'role_assignment',$2,'https://dup.example/uncovered',$3)",
        link_id,
        orphan,
        lt_id,
    )

    findings = await find_duplicates(db)
    assert (survivor, orphan) in _pairs(findings, "overlapping_review")
    assert (survivor, orphan) not in _pairs(findings, "deepened_start")

    await run_audit(db, execute=True)

    row = await db.fetchrow("SELECT archived_at, notes FROM role_assignments WHERE id=$1", orphan)
    assert row["archived_at"] is None
    assert row["notes"] == "orphan curation note"  # no provenance note appended
    assert await db.fetchval("SELECT entity_id FROM links WHERE id=$1", link_id) == orphan
    survivor_notes = await db.fetchval("SELECT notes FROM role_assignments WHERE id=$1", survivor)
    assert survivor_notes is None


async def test_deepened_start_ending_before_the_orphan_is_review(db, pair_base):
    """Both ends dated, survivor's is earlier: a longer tenure, not a duplicate."""
    orphan = await _seed_assignment(
        db, pair_base, start="2019-01-01", end="2026-01-31", created_at="2025-01-01"
    )
    survivor = await _seed_assignment(
        db, pair_base, start="2017-01-01", end="2024-12-31", created_at="2026-07-20"
    )
    findings = await find_duplicates(db)
    assert (survivor, orphan) in _pairs(findings, "overlapping_review")

    await run_audit(db, execute=True)

    assert (
        await db.fetchval("SELECT archived_at FROM role_assignments WHERE id=$1", orphan)
    ) is None


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
        end="2020-12-31",
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
        db, pair_base, start="2019-01-01", end="2020-12-31", created_at="2025-01-01"
    )
    await _seed_assignment(
        db, pair_base, start="2017-01-01", end="2024-12-31", created_at="2026-07-20"
    )
    await run_audit(db, execute=False)
    row = await db.fetchrow("SELECT archived_at FROM role_assignments WHERE id=$1", orphan)
    assert row["archived_at"] is None


async def test_archived_rows_ignored(db, pair_base):
    await _seed_assignment(db, pair_base, start="2019-01-01", end="2020-12-31")
    aid = await _seed_assignment(
        db, pair_base, start="2017-01-01", end="2024-12-31", created_at="2026-07-20"
    )
    await db.execute("UPDATE role_assignments SET archived_at=NOW() WHERE id=$1", aid)
    findings = await find_duplicates(db)
    for category, rows in findings.items():
        for f in rows:
            assert f["person_id"] != pair_base["person_id"], category


async def test_orphan_note_records_the_discarded_span(db, pair_base):
    """#476: the merge drops the orphan's window — the archive note must carry it."""
    orphan = await _seed_assignment(
        db, pair_base, start="2019-01-01", end="2020-12-31", created_at="2025-01-01"
    )
    survivor = await _seed_assignment(
        db, pair_base, start="2017-01-01", end="2024-12-31", created_at="2026-07-20"
    )

    await run_audit(db, execute=True)

    notes = await db.fetchval("SELECT notes FROM role_assignments WHERE id=$1", orphan)
    assert notes == (
        f"Archived as duplicate of {survivor} (#311 audit). Span was 2019-01-01..2020-12-31."
    )


async def test_orphan_note_span_marks_an_open_end(db, pair_base):
    """An open orphan end reads as ``open``, never ``None``.

    Unreachable through ``--execute`` after #476 (coverage needs a dated orphan
    end), so the primitive is called directly to pin the rendering.
    """
    orphan = await _seed_assignment(
        db, pair_base, start="2019-01-01", is_current=True, created_at="2025-01-01"
    )
    survivor = await _seed_assignment(
        db, pair_base, start="2017-01-01", end="2024-12-31", created_at="2026-07-20"
    )

    assert await _merge_pair(db, survivor, orphan) is True

    notes = await db.fetchval("SELECT notes FROM role_assignments WHERE id=$1", orphan)
    assert notes == f"Archived as duplicate of {survivor} (#311 audit). Span was 2019-01-01..open."


async def test_merge_pair_skips_when_a_row_is_already_archived(db, pair_base):
    """CR round 1 (#311): data must never move onto a row archived earlier this run."""
    survivor = await _seed_assignment(db, pair_base, start="2015-01-01", end="2020-12-31")
    orphan = await _seed_assignment(db, pair_base, start="2017-01-01", end="2018-12-31")
    lt_id = await db.fetchval("SELECT id FROM link_types LIMIT 1")
    link_id = generate_id()
    await db.execute(
        "INSERT INTO links (id, entity_type, entity_id, url, link_type_id)"
        " VALUES ($1,'role_assignment',$2,'https://dup.example/skip',$3)",
        link_id,
        orphan,
        lt_id,
    )
    await db.execute("UPDATE role_assignments SET archived_at=NOW() WHERE id=$1", survivor)

    assert await _merge_pair(db, survivor, orphan) is False

    assert await db.fetchval("SELECT entity_id FROM links WHERE id=$1", link_id) == orphan
    assert (
        await db.fetchval("SELECT archived_at FROM role_assignments WHERE id=$1", orphan)
    ) is None


async def test_chain_merge_never_touches_archived_rows(db, pair_base):
    """CR round 1 (#311), re-shaped for the #476 coverage gate.

    A nested chain a ⊃ b ⊃ c: every pair provably covers, so (a,b), (a,c) and
    (b,c) are all ``deepened_start``. (a,b) archives b, which leaves one of the
    two same-orphan c pairs stale-referencing an archived row. Whichever order
    Postgres returns those two rows in, the end state is identical: c's side
    data lands on the one active row, never on the archived b.

    The pre-#476 shape (a and c disjoint, so no (a,c) pair) is unreachable now —
    an a that covers b and overlaps it necessarily overlaps c as well.
    """
    c = await _seed_assignment(
        db, pair_base, start="2017-01-01", end="2018-12-31", created_at="2024-01-01"
    )
    b = await _seed_assignment(
        db, pair_base, start="2015-01-01", end="2020-12-31", created_at="2025-01-01"
    )
    a = await _seed_assignment(
        db, pair_base, start="2013-01-01", end="2024-12-31", created_at="2026-07-20"
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
    assert _pairs(findings, "deepened_start") >= {(a, b), (a, c), (b, c)}

    await run_audit(db, execute=True)

    rows = {
        r["id"]: r["archived_at"]
        for r in await db.fetch(
            "SELECT id, archived_at FROM role_assignments WHERE person_id=$1",
            pair_base["person_id"],
        )
    }
    assert rows[a] is None
    assert rows[b] is not None
    assert rows[c] is not None
    # The link followed the chain to the one active row; the archived b got nothing.
    assert await db.fetchval("SELECT entity_id FROM links WHERE id=$1", link_c) == a
    assert (
        await db.fetchval(
            "SELECT count(*) FROM links WHERE entity_type='role_assignment' AND entity_id=$1", b
        )
        == 0
    )
