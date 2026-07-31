"""Tests for scripts/sweep_role_data_quality.py (#304).

Data-only sweep over non-jurisdictional free-text roles: archive non-role
observation artifacts (`Guest`, `Visitor or Guest`) and normalize typo'd titles
(`Principle` → `Principal`) so they stop orphaning the ``(org, lower(title))``
match key. Rename merges-if-it-would-collide (never trips ``uq_role_org_title``).

Unit tests cover the pure planners; integration tests (require TEST_DATABASE_URL
+ schema-applied DB) cover the archive/rename/merge flow.

Run via:
    uv run pytest tests/scripts/test_sweep_role_data_quality.py
"""

from datetime import date

import pytest
import pytest_asyncio

from scripts.sweep_role_data_quality import (
    ARCHIVE_TITLES,
    RENAME_MAP,
    canonical_rename,
    sweep_role_data_quality,
)
from src.core.db import generate_id

# ---------------------------------------------------------------------------
# Pure planners
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("Principle", "Principal"),
        ("principle", "Principal"),  # case-insensitive match key
        ("  Principle  ", "Principal"),  # whitespace tolerant
        ("Principal", None),  # already canonical — no-op (idempotent)
        ("Guest", None),  # archive candidate, not a rename
        ("Research Analyst", None),  # not in the typo map
        ("", None),
    ],
)
def test_canonical_rename(title: str, expected: str | None) -> None:
    assert canonical_rename(title) == expected


def test_config_is_lowercased_and_disjoint() -> None:
    # Match keys are compared lowercased — the config must already be lower.
    assert all(t == t.lower() for t in ARCHIVE_TITLES)
    assert all(k == k.lower() for k in RENAME_MAP)
    # A title is either an archive artifact or a rename source, never both.
    assert ARCHIVE_TITLES.isdisjoint(RENAME_MAP)
    # Canonical targets are never themselves typo sources (no rename chains).
    assert all(v.lower() not in RENAME_MAP for v in RENAME_MAP.values())


# ---------------------------------------------------------------------------
# Integration
# ---------------------------------------------------------------------------

pytestmark_integration = pytest.mark.integration


@pytest_asyncio.fixture(loop_scope="session")
async def db(db_pool):
    async with db_pool.acquire() as conn:
        tr = conn.transaction()
        await tr.start()
        try:
            yield conn
        finally:
            await tr.rollback()


async def _org(db) -> str:
    oid = generate_id()
    await db.execute("INSERT INTO organizations (id) VALUES ($1)", oid)
    return oid


async def _person(db) -> str:
    pid = generate_id()
    await db.execute("INSERT INTO people (id) VALUES ($1)", pid)
    return pid


async def _role(db, org_id: str, title: str) -> str:
    """A plain non-jurisdictional free-text role (role_type_id/jurisdiction_id NULL)."""
    rid = generate_id()
    await db.execute(
        "INSERT INTO roles (id, organization_id, title) VALUES ($1,$2,$3)",
        rid,
        org_id,
        title,
    )
    return rid


async def _assign(db, role_id: str, person_id: str, *, start=None) -> str:
    aid = generate_id()
    await db.execute(
        "INSERT INTO role_assignments (id, role_id, person_id, start_date) VALUES ($1,$2,$3,$4)",
        aid,
        role_id,
        person_id,
        start,
    )
    return aid


async def _role_state(db, role_id: str):
    return await db.fetchrow("SELECT title, archived_at FROM roles WHERE id=$1", role_id)


async def _active_assign_count(db, role_id: str) -> int:
    return await db.fetchval(
        "SELECT count(*) FROM role_assignments WHERE role_id=$1 AND archived_at IS NULL",
        role_id,
    )


# --- archive path ---------------------------------------------------------


@pytest.mark.integration
async def test_dry_run_mutates_nothing(db):
    org = await _org(db)
    person = await _person(db)
    guest = await _role(db, org, "Guest")
    await _assign(db, guest, person)
    typo = await _role(db, org, "Principle")

    report = await sweep_role_data_quality(db, execute=False)

    # Reported as planned...
    archived = {a["role_id"]: a for a in report["archived"]}
    renamed = {a["role_id"]: a for a in report["renamed"]}
    assert archived[guest]["status"] == "planned"
    assert renamed[typo]["status"] == "would_rename"  # dry run distinguishes rename vs merge
    # ...but nothing changed.
    assert (await _role_state(db, guest))["archived_at"] is None
    assert await _active_assign_count(db, guest) == 1
    assert (await _role_state(db, typo))["title"] == "Principle"


@pytest.mark.integration
async def test_archive_artifact_role_and_assignments(db):
    org = await _org(db)
    p1, p2 = await _person(db), await _person(db)
    guest = await _role(db, org, "Guest")
    await _assign(db, guest, p1)
    await _assign(db, guest, p2)
    visitor = await _role(db, org, "Visitor or Guest")
    await _assign(db, visitor, p1)

    await sweep_role_data_quality(db, execute=True)

    for rid in (guest, visitor):
        assert (await _role_state(db, rid))["archived_at"] is not None
        assert await _active_assign_count(db, rid) == 0


@pytest.mark.integration
async def test_archive_matches_case_insensitively(db):
    org = await _org(db)
    guest = await _role(db, org, "guest")  # lowercase observed variant
    await sweep_role_data_quality(db, execute=True)
    assert (await _role_state(db, guest))["archived_at"] is not None


@pytest.mark.integration
async def test_non_artifact_role_untouched(db):
    org = await _org(db)
    keep = await _role(db, org, "Executive Director")
    await sweep_role_data_quality(db, execute=True)
    st = await _role_state(db, keep)
    assert st["archived_at"] is None and st["title"] == "Executive Director"


# --- rename path ----------------------------------------------------------


@pytest.mark.integration
async def test_rename_typo_in_place_when_no_collision(db):
    org = await _org(db)
    person = await _person(db)
    typo = await _role(db, org, "Principle")
    await _assign(db, typo, person)

    await sweep_role_data_quality(db, execute=True)

    st = await _role_state(db, typo)
    assert st["title"] == "Principal"
    assert st["archived_at"] is None
    assert await _active_assign_count(db, typo) == 1  # assignments untouched


@pytest.mark.integration
async def test_rename_is_idempotent(db):
    org = await _org(db)
    typo = await _role(db, org, "Principle")
    await sweep_role_data_quality(db, execute=True)
    # Second run finds nothing to do — no error, canonical row stays put.
    report = await sweep_role_data_quality(db, execute=True)
    assert all(a["role_id"] != typo for a in report["renamed"])
    assert (await _role_state(db, typo))["title"] == "Principal"


@pytest.mark.integration
async def test_rename_merges_into_existing_canonical_on_collision(db):
    """Same org already has the canonical role → merge, don't trip uq_role_org_title."""
    org = await _org(db)
    p1, p2 = await _person(db), await _person(db)
    canonical = await _role(db, org, "Principal")
    await _assign(db, canonical, p1)
    typo = await _role(db, org, "Principle")
    await _assign(db, typo, p2)

    report = await sweep_role_data_quality(db, execute=True)

    merged = {a["role_id"]: a for a in report["renamed"]}[typo]
    assert merged["status"] == "merged"
    assert merged["target_role_id"] == canonical
    # Loser role gone; its assignment re-homed onto the canonical role.
    assert await _role_state(db, typo) is None
    assert await _active_assign_count(db, canonical) == 2


@pytest.mark.integration
async def test_dry_run_flags_a_would_be_merge(db):
    """A dry run marks a colliding typo 'would_merge' so the destructive path is visible."""
    org = await _org(db)
    await _role(db, org, "Principal")
    typo = await _role(db, org, "Principle")

    report = await sweep_role_data_quality(db, execute=False)

    action = {a["role_id"]: a for a in report["renamed"]}[typo]
    assert action["status"] == "would_merge"
    assert await _role_state(db, typo) is not None  # dry run mutated nothing


@pytest.mark.integration
async def test_rename_merges_into_typed_canonical_peer(db):
    """A typed non-jurisdictional canonical peer is inside uq_role_org_title's scope too
    (index is WHERE jurisdiction_id IS NULL — role_type irrelevant), so the typo must
    merge into it, never trip the constraint with a bare in-place UPDATE."""
    org = await _org(db)
    p1, p2 = await _person(db), await _person(db)
    rt_id = generate_id()
    await db.execute(
        "INSERT INTO role_types (id, slug, display_name) VALUES ($1,$2,'DQ Sweep Test Type')",
        rt_id,
        f"dq_sweep_test_{rt_id.lower()}",
    )
    canonical = generate_id()
    await db.execute(
        "INSERT INTO roles (id, organization_id, title, role_type_id)"
        " VALUES ($1,$2,'Principal',$3)",
        canonical,
        org,
        rt_id,
    )
    await _assign(db, canonical, p1)
    typo = await _role(db, org, "Principle")
    await _assign(db, typo, p2)

    report = await sweep_role_data_quality(db, execute=True)

    merged = {a["role_id"]: a for a in report["renamed"]}[typo]
    assert merged["status"] == "merged"
    assert merged["target_role_id"] == canonical
    # No unique_violation; typo folded into the typed canonical role.
    assert await _role_state(db, typo) is None
    assert await _active_assign_count(db, canonical) == 2


@pytest.mark.integration
async def test_merge_appends_loser_notes_to_winner(db):
    """Merge preserves the loser role's notes on the survivor (mirrors admin role_merge)."""
    org = await _org(db)
    canonical = await _role(db, org, "Principal")
    await db.execute("UPDATE roles SET notes='winner note' WHERE id=$1", canonical)
    typo = await _role(db, org, "Principle")
    await db.execute("UPDATE roles SET notes='typo note' WHERE id=$1", typo)

    await sweep_role_data_quality(db, execute=True)

    notes = await db.fetchval("SELECT notes FROM roles WHERE id=$1", canonical)
    assert "winner note" in notes
    assert "typo note" in notes
    assert "Merged from Principle" in notes


@pytest.mark.integration
async def test_merge_dedups_same_person_same_start(db):
    """Colliding assignment identity (person, start_date) folds, never duplicates."""
    org = await _org(db)
    person = await _person(db)
    canonical = await _role(db, org, "Principal")
    await _assign(db, canonical, person, start=date(2020, 1, 1))
    typo = await _role(db, org, "Principle")
    await _assign(db, typo, person, start=date(2020, 1, 1))  # same identity

    await sweep_role_data_quality(db, execute=True)

    assert await _role_state(db, typo) is None
    assert await _active_assign_count(db, canonical) == 1  # deduped, not doubled


@pytest.mark.integration
async def test_typed_roles_never_swept(db):
    """Only plain free-text roles are in scope — a typed role matches structurally.

    A typed role titled 'Guest' (an artifact key) or 'Principle' (a typo key) must
    be left untouched: its match key is ``role_type_id`` + structure, not
    ``(org, lower(title))``, so the sweep's title rules do not apply.
    """
    org = await _org(db)
    rt_id = generate_id()
    await db.execute(
        "INSERT INTO role_types (id, slug, display_name) VALUES ($1,$2,'DQ Sweep Test Type')",
        rt_id,
        f"dq_sweep_test_{rt_id.lower()}",
    )
    guest = generate_id()
    typo = generate_id()
    for rid, title in ((guest, "Guest"), (typo, "Principle")):
        await db.execute(
            "INSERT INTO roles (id, organization_id, title, role_type_id) VALUES ($1,$2,$3,$4)",
            rid,
            org,
            title,
            rt_id,
        )

    await sweep_role_data_quality(db, execute=True)

    assert (await _role_state(db, guest))["archived_at"] is None
    typo_st = await _role_state(db, typo)
    assert typo_st["archived_at"] is None and typo_st["title"] == "Principle"
