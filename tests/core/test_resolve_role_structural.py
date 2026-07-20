"""Integration tests: structural-field-aware resolve_role (#261).

Roles with a jurisdiction match by (org, role_type, jurisdiction, qualifier);
distinct roles sharing a title must not collapse. Title matching for a role
without a jurisdiction is unchanged. A superseded/historical district is a valid
jurisdiction reference; only a soft-deleted (archived) district is rejected.
"""

import pytest
import pytest_asyncio

from src.core.db import generate_id
from src.core.observation import Disposition, resolve_role

pytestmark = [pytest.mark.integration]


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


async def _jur(db) -> str:
    jid = generate_id()
    type_id = await db.fetchval(
        "SELECT id FROM jurisdiction_types WHERE slug='legislative_district'"
    )
    await db.execute(
        "INSERT INTO jurisdictions (id, slug, name, type_id) VALUES ($1,$2,$3,$4)",
        jid,
        f"ld-{jid[-8:].lower()}",
        "Test LD",
        type_id,
    )
    return jid


async def test_distinct_qualifiers_create_distinct_roles(db):
    org, jur = await _org(db), await _jur(db)
    id1, disp1, _ = await resolve_role(
        db,
        org,
        "State Representative",
        role_type="state_representative",
        jurisdiction_id=jur,
        qualifier="Position 1",
    )
    id2, disp2, _ = await resolve_role(
        db,
        org,
        "State Representative",
        role_type="state_representative",
        jurisdiction_id=jur,
        qualifier="Position 2",
    )
    assert disp1 is Disposition.NEW
    assert disp2 is Disposition.NEW
    assert id1 != id2


async def test_same_role_auto_attaches(db):
    org, jur = await _org(db), await _jur(db)
    kw = dict(role_type="state_representative", jurisdiction_id=jur, qualifier="Position 1")
    id1, disp1, _ = await resolve_role(db, org, "State Representative", **kw)
    id2, disp2, _ = await resolve_role(db, org, "State Representative", **kw)
    assert disp1 is Disposition.NEW
    assert disp2 is Disposition.AUTO_ATTACHED
    assert id1 == id2


async def test_senate_role_null_qualifier_auto_attaches(db):
    org, jur = await _org(db), await _jur(db)
    kw = dict(role_type="state_senator", jurisdiction_id=jur)
    id1, disp1, _ = await resolve_role(db, org, "State Senator", **kw)
    id2, disp2, _ = await resolve_role(db, org, "State Senator", **kw)
    assert disp1 is Disposition.NEW
    assert disp2 is Disposition.AUTO_ATTACHED
    assert id1 == id2


async def test_title_only_path_unchanged(db):
    org = await _org(db)
    id1, disp1, _ = await resolve_role(db, org, "Speaker")
    id2, disp2, _ = await resolve_role(db, org, "speaker")
    assert disp1 is Disposition.NEW
    assert disp2 is Disposition.AUTO_ATTACHED
    assert id1 == id2


async def test_typed_observation_upgrades_untyped_match(db):
    """A typed observation fills role_type_id on a matched untyped role (#266).

    Ongoing ingest self-classifies: a pre-existing free-text role AUTO_ATTACHes
    and gets its NULL role_type_id upgraded in place.
    """
    org = await _org(db)
    untyped_id, disp0, _ = await resolve_role(db, org, "Chair")
    assert disp0 is Disposition.NEW
    assert await db.fetchval("SELECT role_type_id FROM roles WHERE id=$1", untyped_id) is None

    matched_id, disp1, _ = await resolve_role(db, org, "Chair", role_type="committee_chair")
    assert disp1 is Disposition.AUTO_ATTACHED
    assert matched_id == untyped_id
    rt_id = await db.fetchval("SELECT role_type_id FROM roles WHERE id=$1", untyped_id)
    expected = await db.fetchval("SELECT id FROM role_types WHERE slug='committee_chair'")
    assert rt_id == expected


async def test_typed_observation_does_not_reclassify_typed_match(db):
    """Upgrade-on-match only fills a NULL role_type_id — never reclassifies (#266)."""
    org = await _org(db)
    first_id, _, _ = await resolve_role(db, org, "Chair", role_type="committee_chair")
    orig = await db.fetchval("SELECT role_type_id FROM roles WHERE id=$1", first_id)

    same_id, disp, _ = await resolve_role(db, org, "Chair", role_type="committee_vice_chair")
    assert same_id == first_id
    assert disp is Disposition.AUTO_ATTACHED
    assert await db.fetchval("SELECT role_type_id FROM roles WHERE id=$1", first_id) == orig


async def test_title_observation_does_not_attach_to_structural_role(db):
    """A title-only resolve must not glue onto a role with a jurisdiction of the same title."""
    org, jur = await _org(db), await _jur(db)
    structural_id, _, _ = await resolve_role(
        db,
        org,
        "State Representative",
        role_type="state_representative",
        jurisdiction_id=jur,
        qualifier="Position 1",
    )
    title_id, disp, _ = await resolve_role(db, org, "State Representative")
    assert disp is Disposition.NEW
    assert title_id != structural_id


async def test_role_with_jurisdiction_requires_role_type(db):
    org, jur = await _org(db), await _jur(db)
    role_id, disp, reason = await resolve_role(
        db, org, "State Representative", jurisdiction_id=jur, qualifier="Position 1"
    )
    assert disp is Disposition.REJECTED
    assert role_id == ""
    assert reason == "role_type_required_for_jurisdiction"


async def test_unknown_jurisdiction_rejected(db):
    org = await _org(db)
    role_id, disp, reason = await resolve_role(
        db,
        org,
        "State Representative",
        role_type="state_representative",
        jurisdiction_id=generate_id(),
    )
    assert disp is Disposition.REJECTED
    assert "jurisdiction_not_found" in reason


async def test_unknown_role_type_rejected(db):
    org, jur = await _org(db), await _jur(db)
    role_id, disp, reason = await resolve_role(
        db, org, "State Representative", role_type="not_a_real_office", jurisdiction_id=jur
    )
    assert disp is Disposition.REJECTED
    assert "role_type_not_found" in reason


async def test_superseded_jurisdiction_allows_historical_role(db):
    """A redistricted (superseded, past-valid) district is still referenceable.

    Supersession sets superseded_at / valid_until, not archived_at, so a
    historical role can be created against the district that was in effect.
    """
    org = await _org(db)
    type_id = await db.fetchval(
        "SELECT id FROM jurisdiction_types WHERE slug='legislative_district'"
    )
    jid = generate_id()
    await db.execute(
        "INSERT INTO jurisdictions (id, slug, name, type_id, valid_until, superseded_at)"
        " VALUES ($1,$2,$3,$4, DATE '2012-01-01', NOW() - INTERVAL '10 years')",
        jid,
        f"ld-old-{jid[-8:].lower()}",
        "LD-5 (2002 plan)",
        type_id,
    )
    role_id, disp, _ = await resolve_role(
        db,
        org,
        "State Representative",
        role_type="state_representative",
        jurisdiction_id=jid,
        qualifier="Position 1",
    )
    assert disp is Disposition.NEW
    assert role_id != ""


async def test_archived_jurisdiction_rejected(db):
    """A soft-deleted (archived) district is not a valid jurisdiction reference."""
    org = await _org(db)
    type_id = await db.fetchval(
        "SELECT id FROM jurisdiction_types WHERE slug='legislative_district'"
    )
    jid = generate_id()
    await db.execute(
        "INSERT INTO jurisdictions (id, slug, name, type_id, archived_at)"
        " VALUES ($1,$2,$3,$4, NOW())",
        jid,
        f"ld-arch-{jid[-8:].lower()}",
        "Deleted LD",
        type_id,
    )
    role_id, disp, reason = await resolve_role(
        db,
        org,
        "State Representative",
        role_type="state_representative",
        jurisdiction_id=jid,
    )
    assert disp is Disposition.REJECTED
    assert "jurisdiction_archived" in reason


async def test_qualifier_dropped_for_role_without_jurisdiction(db):
    """resolve_role ignores qualifier without a jurisdiction (it only disambiguates roles)."""
    org = await _org(db)
    role_id, disp, _ = await resolve_role(db, org, "Speaker", qualifier="Ignored")
    assert disp is Disposition.NEW
    stored = await db.fetchval("SELECT qualifier FROM roles WHERE id=$1", role_id)
    assert stored is None


# ---------------------------------------------------------------------------
# Title synthesis on create (#267): title optional for a role with a
# jurisdiction; PM curates it
# ---------------------------------------------------------------------------


async def _wa_ld(db, n: int) -> str:
    jid = generate_id()
    type_id = await db.fetchval(
        "SELECT id FROM jurisdiction_types WHERE slug='legislative_district'"
    )
    await db.execute(
        "INSERT INTO jurisdictions (id, slug, name, type_id) VALUES ($1,$2,$3,$4)",
        jid,
        f"usa-wa-ld-{n}",
        f"Washington Legislative District {n}",
        type_id,
    )
    return jid


async def test_structural_create_without_title_synthesizes_senator(db):
    org, jur = await _org(db), await _wa_ld(db, 7)
    rid, disp, reason = await resolve_role(
        db, org, None, role_type="state_senator", jurisdiction_id=jur
    )
    assert disp is Disposition.NEW
    title = await db.fetchval("SELECT title FROM roles WHERE id=$1", rid)
    assert title == "Washington State Senator, LD-7"


async def test_structural_create_without_title_synthesizes_representative_position(db):
    org, jur = await _org(db), await _wa_ld(db, 7)
    rid, disp, _ = await resolve_role(
        db,
        org,
        None,
        role_type="state_representative",
        jurisdiction_id=jur,
        qualifier="Position 2",
    )
    assert disp is Disposition.NEW
    title = await db.fetchval("SELECT title FROM roles WHERE id=$1", rid)
    assert title == "Washington State Representative, LD-7, Position 2"


async def test_structural_create_prefers_synthesis_over_supplied_title(db):
    """PM prefers the synthesized title over a supplied one (no upstream drift)."""
    org, jur = await _org(db), await _wa_ld(db, 7)
    rid, disp, _ = await resolve_role(
        db, org, "Custom Senator Title", role_type="state_senator", jurisdiction_id=jur
    )
    assert disp is Disposition.NEW
    title = await db.fetchval("SELECT title FROM roles WHERE id=$1", rid)
    assert title == "Washington State Senator, LD-7"


async def test_structural_create_unsynthesizable_falls_back_to_supplied_title(db):
    """A role whose title can't be synthesized uses the supplied title as fallback."""
    org, jur = await _org(db), await _jur(db)  # slug ld-<hex>, not usa-wa-ld-N
    rid, disp, _ = await resolve_role(
        db, org, "Fallback Title", role_type="state_senator", jurisdiction_id=jur
    )
    assert disp is Disposition.NEW
    title = await db.fetchval("SELECT title FROM roles WHERE id=$1", rid)
    assert title == "Fallback Title"


async def test_structural_create_unsynthesizable_without_title_rejected(db):
    """Non-usa-wa-ld jurisdiction can't be synthesized; titleless create is rejected."""
    org, jur = await _org(db), await _jur(db)  # slug ld-<hex>, not usa-wa-ld-N
    rid, disp, reason = await resolve_role(
        db, org, None, role_type="state_senator", jurisdiction_id=jur
    )
    assert disp is Disposition.REJECTED
    assert reason.startswith("role_title_unavailable:")
    assert "state_senator" in reason
    assert rid == ""


async def test_non_structural_without_title_rejected(db):
    """A role without a jurisdiction and no title is rejected, not a DB error."""
    org = await _org(db)
    rid, disp, reason = await resolve_role(db, org, None)
    assert disp is Disposition.REJECTED
    assert reason == "title_required"
    assert rid == ""


async def test_structural_match_without_title_auto_attaches(db):
    """Title is not the match key — a titleless re-observation attaches."""
    org, jur = await _org(db), await _wa_ld(db, 7)
    kw = dict(role_type="state_senator", jurisdiction_id=jur)
    id1, disp1, _ = await resolve_role(db, org, None, **kw)
    id2, disp2, _ = await resolve_role(db, org, None, **kw)
    assert disp1 is Disposition.NEW
    assert disp2 is Disposition.AUTO_ATTACHED
    assert id1 == id2


# ---------------------------------------------------------------------------
# Jurisdiction-less typed roles — the coarse membership classifier
# (#269 `member`, split into `committee_member` by #266)
# ---------------------------------------------------------------------------


async def test_member_role_persists_role_type_without_jurisdiction(db):
    """A `committee_member` role (no jurisdiction) is created with role_type_id set.

    #269's contract: the classifier lands on the row (so memberships aggregate)
    even though matching stays in title mode — the role carries no jurisdiction
    and no qualifier.
    """
    org = await _org(db)
    rid, disp, reason = await resolve_role(db, org, "Member", role_type="committee_member")
    assert disp is Disposition.NEW
    assert reason is None
    member_type_id = await db.fetchval("SELECT id FROM role_types WHERE slug='committee_member'")
    row = await db.fetchrow(
        "SELECT role_type_id, jurisdiction_id, qualifier FROM roles WHERE id=$1", rid
    )
    assert row["role_type_id"] == member_type_id
    assert row["jurisdiction_id"] is None
    assert row["qualifier"] is None


async def test_member_role_matches_by_title_not_jurisdiction_branch(db):
    """A re-observed membership role AUTO_ATTACHes by (org, lower(title))."""
    org = await _org(db)
    id1, disp1, _ = await resolve_role(db, org, "Member", role_type="committee_member")
    id2, disp2, _ = await resolve_role(db, org, "member", role_type="committee_member")
    assert disp1 is Disposition.NEW
    assert disp2 is Disposition.AUTO_ATTACHED
    assert id1 == id2


async def test_typed_member_attaches_to_preexisting_untyped_title(db):
    """(org, title) is the sole key: a `committee_member` observation attaches to a
    pre-existing untyped role of the same title — and upgrades it in place
    (upgrade-on-match, #266, supersedes the #269 leave-untyped behavior)."""
    org = await _org(db)
    untyped_id, disp1, _ = await resolve_role(db, org, "Member")
    typed_id, disp2, _ = await resolve_role(db, org, "Member", role_type="committee_member")
    assert disp1 is Disposition.NEW
    assert disp2 is Disposition.AUTO_ATTACHED
    assert typed_id == untyped_id
    # The matched row's NULL role_type_id is now filled in place (#266).
    rt_id = await db.fetchval("SELECT role_type_id FROM roles WHERE id=$1", typed_id)
    expected = await db.fetchval("SELECT id FROM role_types WHERE slug='committee_member'")
    assert rt_id == expected


# ---------------------------------------------------------------------------
# requires_qualifier guard — reject positionless districted seats (#273)
# ---------------------------------------------------------------------------


async def test_positionless_house_seat_rejected(db):
    """A `state_representative` + jurisdiction with NULL qualifier is rejected.

    House seats are per-position; a NULL-qualifier tuple would otherwise mint a
    spurious positionless seat (#267). requires_qualifier turns that into a loud
    reject instead of a silent create.
    """
    org, jur = await _org(db), await _wa_ld(db, 11)
    rid, disp, reason = await resolve_role(
        db, org, None, role_type="state_representative", jurisdiction_id=jur
    )
    assert disp is Disposition.REJECTED
    assert rid == ""
    assert reason is not None and "qualifier_required" in reason
    # Nothing minted.
    assert await db.fetchval("SELECT count(*) FROM roles WHERE organization_id=$1", org) == 0


async def test_positioned_house_seat_accepted(db):
    """The same office with a qualifier is created normally."""
    org, jur = await _org(db), await _wa_ld(db, 11)
    rid, disp, _ = await resolve_role(
        db,
        org,
        None,
        role_type="state_representative",
        jurisdiction_id=jur,
        qualifier="Position 1",
    )
    assert disp is Disposition.NEW
    assert rid != ""


async def test_senate_seat_null_qualifier_not_rejected(db):
    """`state_senator` (requires_qualifier=False) still accepts a NULL qualifier."""
    org, jur = await _org(db), await _wa_ld(db, 11)
    rid, disp, _ = await resolve_role(db, org, None, role_type="state_senator", jurisdiction_id=jur)
    assert disp is Disposition.NEW
    assert rid != ""


async def test_positionless_house_seat_empty_qualifier_rejected(db):
    """An empty/whitespace qualifier is treated as missing, not a distinct seat."""
    org, jur = await _org(db), await _wa_ld(db, 12)
    for blank in ("", "   "):
        rid, disp, reason = await resolve_role(
            db,
            org,
            None,
            role_type="state_representative",
            jurisdiction_id=jur,
            qualifier=blank,
        )
        assert disp is Disposition.REJECTED, f"qualifier={blank!r}"
        assert rid == ""
        assert reason is not None and "qualifier_required" in reason
