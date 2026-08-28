"""Core citation write/dedup/refine/retract logic (#319).

Identity = (entity_type, entity_id, field_name, url) with NULLS NOT DISTINCT
(active rows). title/excerpt/accessed_at are mutable payload. Observable +
retractable, mirroring the events model (#321/#322): natural-key observe
refines-in-place-or-creates, pm_citation_id refine is id-addressed, op="retract"
archives (never hard-delete) and is anti-resurrection-safe. field_name is
validated against a per-entity-type allowlist.
"""

from datetime import UTC, datetime

import pytest
import pytest_asyncio

from src.core.citations import (
    CitationClaim,
    CitationDisposition,
    CitationRejectReason,
    apply_citation_observations,
    write_citations,
)
from src.core.db import generate_id
from src.core.observation import ObservationRejected

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


async def _person(db) -> str:
    pid = generate_id()
    await db.execute("INSERT INTO people (id) VALUES ($1)", pid)
    return pid


async def _role_assignment(db) -> str:
    oid, rid, pid, raid = (generate_id() for _ in range(4))
    await db.execute("INSERT INTO organizations (id) VALUES ($1)", oid)
    await db.execute(
        "INSERT INTO roles (id, organization_id, title) VALUES ($1, $2, 'Director')", rid, oid
    )
    await db.execute("INSERT INTO people (id) VALUES ($1)", pid)
    await db.execute(
        "INSERT INTO role_assignments (id, person_id, role_id, is_current) VALUES ($1,$2,$3,TRUE)",
        raid,
        pid,
        rid,
    )
    return raid


async def _api_key(db) -> str:
    uid, kid = generate_id(), generate_id()
    await db.execute("INSERT INTO app_users (id, email) VALUES ($1, 'k@example.com')", uid)
    await db.execute(
        "INSERT INTO api_keys (id, user_id, label, key_prefix, key_hash)"
        " VALUES ($1, $2, 'k', 'kkkkkkkk', $3)",
        kid,
        uid,
        generate_id(),
    )
    return kid


async def _row(db, cid: str):
    return await db.fetchrow("SELECT * FROM citations WHERE id=$1", cid)


# ── create ───────────────────────────────────────────────────────────────────


async def test_create_new_field_citation(db):
    raid = await _role_assignment(db)
    [res] = await write_citations(
        db,
        "role_assignment",
        raid,
        None,
        [CitationClaim(field_name="start_date", url="https://src.example/a", title="Src A")],
    )
    assert res.disposition is CitationDisposition.NEW
    row = await _row(db, res.citation_id)
    assert row["entity_type"] == "role_assignment"
    assert row["field_name"] == "start_date"
    assert row["url"] == "https://src.example/a"
    assert row["archived_at"] is None


async def test_whole_entity_citation_field_name_null(db):
    pid = await _person(db)
    [res] = await write_citations(
        db, "person", pid, None, [CitationClaim(url="https://src.example/whole")]
    )
    assert res.disposition is CitationDisposition.NEW
    assert (await _row(db, res.citation_id))["field_name"] is None


# ── natural-key dedup / refine ────────────────────────────────────────────────


async def test_reobserve_identical_is_noop_auto_attached(db):
    pid = await _person(db)
    claim = CitationClaim(field_name="notes", url="https://src.example/x", title="X")
    [first] = await write_citations(db, "person", pid, None, [claim])
    before = (await _row(db, first.citation_id))["updated_at"]
    [second] = await write_citations(db, "person", pid, None, [claim])
    assert second.disposition is CitationDisposition.AUTO_ATTACHED
    assert second.citation_id == first.citation_id
    assert second.attached_archived is False  # #477: live row, not an archived twin
    assert (await _row(db, first.citation_id))["updated_at"] == before  # no clock bump
    assert await db.fetchval("SELECT count(*) FROM citations WHERE entity_id=$1", pid) == 1


async def test_reobserve_changed_payload_refines_in_place(db):
    pid = await _person(db)
    [first] = await write_citations(
        db, "person", pid, None, [CitationClaim(field_name="notes", url="https://s/x", title="old")]
    )
    [second] = await write_citations(
        db, "person", pid, None, [CitationClaim(field_name="notes", url="https://s/x", title="new")]
    )
    assert second.disposition is CitationDisposition.UPDATED
    assert second.citation_id == first.citation_id
    assert (await _row(db, first.citation_id))["title"] == "new"


async def test_same_url_different_field_are_distinct_rows(db):
    raid = await _role_assignment(db)
    [a] = await write_citations(
        db,
        "role_assignment",
        raid,
        None,
        [CitationClaim(field_name="start_date", url="https://s/1")],
    )
    [b] = await write_citations(
        db, "role_assignment", raid, None, [CitationClaim(field_name="end_date", url="https://s/1")]
    )
    assert a.citation_id != b.citation_id


# ── URL-less identity (NULLS NOT DISTINCT) ────────────────────────────────────


async def test_urlless_single_row_per_entity_field(db):
    pid = await _person(db)
    [first] = await write_citations(
        db, "person", pid, None, [CitationClaim(field_name="notes", title="Book, p.12")]
    )
    [second] = await write_citations(
        db, "person", pid, None, [CitationClaim(field_name="notes", title="Book, p.99")]
    )
    # Same NULL-url slot for (person, notes) → refines the one row, no duplicate.
    assert second.citation_id == first.citation_id
    assert second.disposition is CitationDisposition.UPDATED
    assert await db.fetchval("SELECT count(*) FROM citations WHERE entity_id=$1", pid) == 1


async def test_urlless_requires_title(db):
    pid = await _person(db)
    [res] = await apply_citation_observations(
        db, "person", pid, None, [CitationClaim(field_name="notes")]
    )
    assert res.disposition is CitationDisposition.REJECTED
    assert res.reason == CitationRejectReason.MISSING_REQUIRED_FIELD


# ── field_name allowlist ──────────────────────────────────────────────────────


async def test_unknown_field_name_rejected(db):
    raid = await _role_assignment(db)
    [res] = await apply_citation_observations(
        db,
        "role_assignment",
        raid,
        None,
        [CitationClaim(field_name="not_a_field", url="https://s/x")],
    )
    assert res.disposition is CitationDisposition.REJECTED
    assert res.reason == CitationRejectReason.CITABLE_FIELD_UNKNOWN


# ── pm_citation_id refine ─────────────────────────────────────────────────────


async def test_refine_by_id_updates_payload(db):
    pid = await _person(db)
    [first] = await write_citations(
        db, "person", pid, None, [CitationClaim(field_name="notes", url="https://s/x", title="old")]
    )
    [res] = await write_citations(
        db,
        "person",
        pid,
        None,
        [CitationClaim(pm_citation_id=first.citation_id, url="https://s/x", title="new")],
    )
    assert res.disposition is CitationDisposition.UPDATED
    assert (await _row(db, first.citation_id))["title"] == "new"


async def test_refine_by_id_needs_no_url_or_title(db):
    """An id-addressed refine needn't resupply url/title — identity is pinned by the
    id, so the url-or-title requirement is scoped to genuine creates (#319 CR).
    Also pins the full-replace contract: omitted mutable fields clear to NULL."""
    pid = await _person(db)
    ts = datetime(2026, 7, 1, 12, 0, tzinfo=UTC)
    [first] = await write_citations(
        db,
        "person",
        pid,
        None,
        [CitationClaim(field_name="notes", url="https://s/x", title="t", excerpt="e")],
    )
    [res] = await apply_citation_observations(
        db, "person", pid, None, [CitationClaim(pm_citation_id=first.citation_id, accessed_at=ts)]
    )
    assert res.disposition is CitationDisposition.UPDATED
    row = await _row(db, first.citation_id)
    assert row["accessed_at"] == ts
    assert row["url"] == "https://s/x"  # identity, untouched
    assert row["title"] is None and row["excerpt"] is None  # full-replace clears omitted


async def test_refine_by_id_url_change_is_identity_immutable(db):
    pid = await _person(db)
    [first] = await write_citations(
        db, "person", pid, None, [CitationClaim(field_name="notes", url="https://s/x")]
    )
    [res] = await apply_citation_observations(
        db,
        "person",
        pid,
        None,
        [CitationClaim(pm_citation_id=first.citation_id, url="https://s/DIFFERENT")],
    )
    assert res.disposition is CitationDisposition.REJECTED
    assert res.reason == CitationRejectReason.IDENTITY_IMMUTABLE


async def test_refine_unknown_id_not_found(db):
    pid = await _person(db)
    [res] = await apply_citation_observations(
        db, "person", pid, None, [CitationClaim(pm_citation_id=generate_id(), url="https://s/x")]
    )
    assert res.disposition is CitationDisposition.REJECTED
    assert res.reason == CitationRejectReason.CITATION_NOT_FOUND


# ── provenance gate ───────────────────────────────────────────────────────────


async def test_refine_foreign_source_conflict(db):
    pid = await _person(db)
    owner = await _api_key(db)
    other = await _api_key(db)
    [first] = await write_citations(
        db, "person", pid, owner, [CitationClaim(field_name="notes", url="https://s/x", title="a")]
    )
    [res] = await apply_citation_observations(
        db,
        "person",
        pid,
        other,
        [CitationClaim(pm_citation_id=first.citation_id, url="https://s/x", title="b")],
    )
    assert res.disposition is CitationDisposition.REJECTED
    assert res.reason == CitationRejectReason.PROVENANCE_CONFLICT
    assert (await _row(db, first.citation_id))["title"] == "a"  # unchanged


# ── retract + anti-resurrection ───────────────────────────────────────────────


async def test_retract_archives(db):
    pid = await _person(db)
    [first] = await write_citations(
        db, "person", pid, None, [CitationClaim(field_name="notes", url="https://s/x")]
    )
    [res] = await write_citations(
        db, "person", pid, None, [CitationClaim(op="retract", pm_citation_id=first.citation_id)]
    )
    assert res.disposition is CitationDisposition.RETRACTED
    assert (await _row(db, first.citation_id))["archived_at"] is not None


async def test_retract_without_id_invalid(db):
    pid = await _person(db)
    [res] = await apply_citation_observations(
        db, "person", pid, None, [CitationClaim(op="retract")]
    )
    assert res.disposition is CitationDisposition.REJECTED
    assert res.reason == CitationRejectReason.INVALID


async def test_retract_already_archived_is_noop(db):
    pid = await _person(db)
    [first] = await write_citations(
        db, "person", pid, None, [CitationClaim(field_name="notes", url="https://s/x")]
    )
    await write_citations(
        db, "person", pid, None, [CitationClaim(op="retract", pm_citation_id=first.citation_id)]
    )
    archived_at = (await _row(db, first.citation_id))["archived_at"]
    [res] = await write_citations(
        db, "person", pid, None, [CitationClaim(op="retract", pm_citation_id=first.citation_id)]
    )
    assert res.disposition is CitationDisposition.AUTO_ATTACHED
    assert res.attached_archived is True  # #477: the row addressed is retracted
    assert (await _row(db, first.citation_id))["archived_at"] == archived_at  # no clock bump


async def test_reobserve_retracted_content_stays_retracted(db):
    pid = await _person(db)
    claim = CitationClaim(field_name="notes", url="https://s/x", title="t")
    [first] = await write_citations(db, "person", pid, None, [claim])
    await write_citations(
        db, "person", pid, None, [CitationClaim(op="retract", pm_citation_id=first.citation_id)]
    )
    [res] = await write_citations(db, "person", pid, None, [claim])  # same content again
    assert res.disposition is CitationDisposition.AUTO_ATTACHED
    assert res.citation_id == first.citation_id
    assert res.attached_archived is True  # #477: anti-resurrection attach, labelled
    assert (await _row(db, first.citation_id))["archived_at"] is not None  # not resurrected
    assert await db.fetchval("SELECT count(*) FROM citations WHERE entity_id=$1", pid) == 1


# ── entity resolution (native transport, target must exist) ───────────────────


async def test_native_unknown_entity_unresolved(db):
    [res] = await apply_citation_observations(
        db, "person", generate_id(), None, [CitationClaim(field_name="notes", url="https://s/x")]
    )
    assert res.disposition is CitationDisposition.REJECTED
    assert res.reason == CitationRejectReason.ENTITY_UNRESOLVED


# ── transports ────────────────────────────────────────────────────────────────


async def test_write_citations_all_or_nothing(db):
    pid = await _person(db)
    # write_citations relies on the caller's transaction to roll back on raise
    # (like write_entity_events) — wrap it so the good claim unwinds with the bad.
    with pytest.raises(ObservationRejected):
        async with db.transaction():
            await write_citations(
                db,
                "person",
                pid,
                None,
                [
                    CitationClaim(field_name="notes", url="https://s/ok"),
                    CitationClaim(field_name="bogus", url="https://s/bad"),  # → rolls back all
                ],
            )
    assert await db.fetchval("SELECT count(*) FROM citations WHERE entity_id=$1", pid) == 0


async def test_apply_observations_partial_success(db):
    pid = await _person(db)
    results = await apply_citation_observations(
        db,
        "person",
        pid,
        None,
        [
            CitationClaim(field_name="notes", url="https://s/ok"),
            CitationClaim(field_name="bogus", url="https://s/bad"),
        ],
    )
    assert results[0].disposition is CitationDisposition.NEW
    assert results[1].disposition is CitationDisposition.REJECTED
    assert results[1].reason == CitationRejectReason.CITABLE_FIELD_UNKNOWN
    assert await db.fetchval("SELECT count(*) FROM citations WHERE entity_id=$1", pid) == 1


async def test_accessed_at_persisted(db):
    pid = await _person(db)
    ts = datetime(2026, 7, 1, 12, 0, tzinfo=UTC)
    [res] = await write_citations(
        db,
        "person",
        pid,
        None,
        [CitationClaim(field_name="notes", url="https://s/x", title="t", accessed_at=ts)],
    )
    assert (await _row(db, res.citation_id))["accessed_at"] == ts
