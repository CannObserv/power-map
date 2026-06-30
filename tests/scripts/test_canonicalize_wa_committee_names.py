"""Tests for the WA Joint/Other committee canonical-name cleanup (issue #254).

Producer ordering-accident left 21 orgs with an agency-double-prefixed `legal`
name marked canonical (e.g. "Joint Joint Transportation Committee"). The clean
name is already present as a non-canonical `legal` observation. Each action:

- promotes the clean name to ``is_canonical=TRUE``
- deletes (retires) the prefixed garbage row

Both happen in the caller's transaction; ``run_cleanup`` wraps the batch in a
savepoint that rolls back on dry-run or on any error.
"""

import asyncpg
import pytest
import pytest_asyncio

from scripts.canonicalize_wa_committee_names import (
    CANONICALIZE_ACTIONS,
    CanonicalizeName,
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
        try:
            yield conn
        finally:
            await tr.rollback()


async def _seed_org_with_names(
    conn: asyncpg.Connection,
    *,
    clean_name: str,
    prefixed_name: str,
    clean_is_canonical: bool = False,
    prefixed_is_canonical: bool = True,
) -> tuple[str, str, str]:
    """Seed an org mirroring the production state: prefixed row canonical,
    clean row non-canonical. Returns (org_id, clean_name_id, prefixed_name_id)."""
    oid = generate_id()
    clean_id = generate_id()
    prefixed_id = generate_id()
    await conn.execute("INSERT INTO organizations (id) VALUES ($1)", oid)
    await conn.execute(
        "INSERT INTO organization_names (id, organization_id, name, name_type, is_canonical)"
        " VALUES ($1, $2, $3, 'legal', $4)",
        prefixed_id,
        oid,
        prefixed_name,
        prefixed_is_canonical,
    )
    await conn.execute(
        "INSERT INTO organization_names (id, organization_id, name, name_type, is_canonical)"
        " VALUES ($1, $2, $3, 'legal', $4)",
        clean_id,
        oid,
        clean_name,
        clean_is_canonical,
    )
    return oid, clean_id, prefixed_id


def _action(oid: str) -> CanonicalizeName:
    return CanonicalizeName(
        org_id=oid,
        clean_name="Joint Transportation Committee",
        prefixed_name="Joint Joint Transportation Committee",
    )


# ---- apply_action ----------------------------------------------------------


async def test_apply_action_promotes_clean_and_deletes_prefixed(db):
    oid, clean_id, prefixed_id = await _seed_org_with_names(
        db,
        clean_name="Joint Transportation Committee",
        prefixed_name="Joint Joint Transportation Committee",
    )
    outcome = await apply_action(db, _action(oid))

    rows = await db.fetch(
        "SELECT id, name, is_canonical FROM organization_names WHERE organization_id=$1", oid
    )
    # Prefixed row is gone; only the clean name remains and it is canonical.
    assert [(r["name"], r["is_canonical"]) for r in rows] == [
        ("Joint Transportation Committee", True)
    ]
    assert outcome.promoted is True
    assert outcome.deleted_prefixed is True


async def test_apply_action_leaves_exactly_one_canonical(db):
    oid, _, _ = await _seed_org_with_names(
        db,
        clean_name="Joint Transportation Committee",
        prefixed_name="Joint Joint Transportation Committee",
    )
    await apply_action(db, _action(oid))
    canon_count = await db.fetchval(
        "SELECT count(*) FROM organization_names WHERE organization_id=$1 AND is_canonical=TRUE",
        oid,
    )
    assert canon_count == 1


async def test_apply_action_raises_when_clean_name_missing(db):
    """Defensive — a stale clean_name surfaces rather than silently no-op'ing."""
    oid = generate_id()
    await db.execute("INSERT INTO organizations (id) VALUES ($1)", oid)
    await db.execute(
        "INSERT INTO organization_names (id, organization_id, name, name_type, is_canonical)"
        " VALUES ($1, $2, 'Joint Joint Transportation Committee', 'legal', TRUE)",
        generate_id(),
        oid,
    )
    with pytest.raises(ValueError, match="clean name"):
        await apply_action(db, _action(oid))


async def test_apply_action_is_idempotent(db):
    """Second run is a clean no-op: clean already canonical, prefixed already gone."""
    oid, _, _ = await _seed_org_with_names(
        db,
        clean_name="Joint Transportation Committee",
        prefixed_name="Joint Joint Transportation Committee",
    )
    await apply_action(db, _action(oid))
    outcome = await apply_action(db, _action(oid))  # re-run

    assert outcome.promoted is False
    assert outcome.deleted_prefixed is False
    rows = await db.fetch(
        "SELECT name, is_canonical FROM organization_names WHERE organization_id=$1", oid
    )
    assert [(r["name"], r["is_canonical"]) for r in rows] == [
        ("Joint Transportation Committee", True)
    ]


async def test_apply_action_when_clean_already_canonical_only_deletes_prefixed(db):
    """Partially-applied state: clean already canonical, prefixed still present."""
    oid, _, _ = await _seed_org_with_names(
        db,
        clean_name="Joint Transportation Committee",
        prefixed_name="Joint Joint Transportation Committee",
        clean_is_canonical=True,
        prefixed_is_canonical=False,
    )
    outcome = await apply_action(db, _action(oid))

    assert outcome.promoted is False
    assert outcome.deleted_prefixed is True
    rows = await db.fetch(
        "SELECT name, is_canonical FROM organization_names WHERE organization_id=$1", oid
    )
    assert [(r["name"], r["is_canonical"]) for r in rows] == [
        ("Joint Transportation Committee", True)
    ]


async def test_apply_action_emits_entity_change_updated(db):
    """Promotion must broadcast an `updated` change on the outbox (parity contract)."""
    oid, _, _ = await _seed_org_with_names(
        db,
        clean_name="Joint Transportation Committee",
        prefixed_name="Joint Joint Transportation Committee",
    )
    before = await db.fetchval("SELECT COALESCE(max(id), 0) FROM entity_changes")
    await apply_action(db, _action(oid))
    row = await db.fetchrow(
        "SELECT change_kind FROM entity_changes"
        " WHERE entity_type='organization' AND entity_id=$1 AND id > $2"
        " ORDER BY id DESC LIMIT 1",
        oid,
        before,
    )
    assert row is not None, "name change must emit an entity_changes row"
    assert row["change_kind"] == "updated"


# ---- run_cleanup wrapper ---------------------------------------------------


async def test_run_cleanup_dry_run_rolls_back(db):
    oid, _, _ = await _seed_org_with_names(
        db,
        clean_name="Joint Transportation Committee",
        prefixed_name="Joint Joint Transportation Committee",
    )
    stats = await run_cleanup(db, actions=[_action(oid)], dry_run=True)
    assert stats.applied == 1
    assert stats.dry_run is True
    # Rolled back — prefixed still present and canonical, clean still non-canonical.
    rows = await db.fetch(
        "SELECT name, is_canonical FROM organization_names"
        " WHERE organization_id=$1 ORDER BY is_canonical DESC",
        oid,
    )
    assert [(r["name"], r["is_canonical"]) for r in rows] == [
        ("Joint Joint Transportation Committee", True),
        ("Joint Transportation Committee", False),
    ]


async def test_run_cleanup_execute_persists_and_counts(db):
    oid, _, _ = await _seed_org_with_names(
        db,
        clean_name="Joint Transportation Committee",
        prefixed_name="Joint Joint Transportation Committee",
    )
    stats = await run_cleanup(db, actions=[_action(oid)], dry_run=False)
    assert stats.applied == 1
    assert stats.promoted == 1
    assert stats.deleted_prefixed == 1
    assert stats.dry_run is False
    rows = await db.fetch(
        "SELECT name, is_canonical FROM organization_names WHERE organization_id=$1", oid
    )
    assert [(r["name"], r["is_canonical"]) for r in rows] == [
        ("Joint Transportation Committee", True)
    ]


async def test_run_cleanup_rolls_back_on_action_error(db):
    """All-or-nothing — if one action raises, none persist."""
    oid, _, _ = await _seed_org_with_names(
        db,
        clean_name="Joint Transportation Committee",
        prefixed_name="Joint Joint Transportation Committee",
    )
    bad = CanonicalizeName(
        org_id="nonexistent-org",
        clean_name="No Such Name",
        prefixed_name="No Such Prefixed Name",
    )
    with pytest.raises(ValueError):
        await run_cleanup(db, actions=[_action(oid), bad], dry_run=False)
    # First action rolled back — prefixed still canonical.
    canon = await db.fetchval(
        "SELECT name FROM organization_names WHERE organization_id=$1 AND is_canonical=TRUE",
        oid,
    )
    assert canon == "Joint Joint Transportation Committee"


# ---- the curated action list ----------------------------------------------


def test_canonicalize_actions_list_shape():
    """The 21 curated actions: distinct orgs, distinct clean/prefixed, and the
    prefixed name is exactly the clean name with a single 'Joint '/'Other ' prefix."""
    assert len(CANONICALIZE_ACTIONS) == 21
    org_ids = [a.org_id for a in CANONICALIZE_ACTIONS]
    assert len(set(org_ids)) == 21, "org ids must be distinct"
    for a in CANONICALIZE_ACTIONS:
        assert a.clean_name != a.prefixed_name
        assert a.prefixed_name in (f"Joint {a.clean_name}", f"Other {a.clean_name}"), (
            f"prefixed name for {a.org_id} is not a single Joint/Other prefix of the clean name"
        )
