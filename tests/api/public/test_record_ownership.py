"""#882: a record's ownership, relative to the calling key, on reads — and the
writes a subscribed dataset would revert, refused or skipped.

``ownership`` (assignments) and ``parent_ownership`` (orgs) answer
``self | other | none | dataset``. ``dataset`` comes first: the nightly apply
reverts any observation write to what a dataset owns, whoever holds the row's
``source_key_id``. The ETag carries the value, so two keys never share a
validator for one row, and a dataset adopting a row moves it.
"""

import hashlib
import os

import pytest
import pytest_asyncio

from src.core.db import generate_id
from src.core.ingestion.crosswalk import PRODUCER_SOURCE

pytestmark = [pytest.mark.integration]

_ASSIGNMENTS = "/api/v1/assignments"
_ORGS = "/api/v1/orgs"


async def _write_key(db, label: str) -> tuple[str, str]:
    """An API key holding observations:write, as (raw key, key id)."""
    scope_id = "observations:write"
    await db.execute(
        "INSERT INTO api_key_scope_types (id, display_name, description)"
        " VALUES ($1, 'Observations Write', 'Create and update observations')"
        " ON CONFLICT (id) DO NOTHING",
        scope_id,
    )
    uid, kid = generate_id(), generate_id()
    raw = "pm_" + os.urandom(16).hex()
    await db.execute(
        "INSERT INTO app_users (id, email) VALUES ($1, $2)", uid, f"{kid.lower()}@own.test"
    )
    await db.execute(
        "INSERT INTO api_keys (id, user_id, label, key_prefix, key_hash) VALUES ($1,$2,$3,$4,$5)",
        kid,
        uid,
        label,
        raw[:8],
        hashlib.sha256(raw.encode()).hexdigest(),
    )
    await db.execute(
        "INSERT INTO api_key_scopes (api_key_id, scope_id) VALUES ($1, $2)", kid, scope_id
    )
    return raw, kid


async def _anchor(db, kind: str, pm_id: str, resolution: str = "live") -> None:
    """A subscribed dataset's crosswalk anchor on ``pm_id``."""
    await db.execute(
        "INSERT INTO producer_crosswalk"
        " (id, source, kind, producer_id, exported_pm_id, pm_id, resolution)"
        " VALUES ($1, $2, $3, $4, $5, $5, $6)",
        generate_id(),
        PRODUCER_SOURCE,
        kind,
        f"X-{pm_id}",
        pm_id,
        resolution,
    )


async def _assignment(db, source_key_id: str | None = None) -> str:
    person_id, org_id, role_id, ra_id = (generate_id() for _ in range(4))
    await db.execute("INSERT INTO people (id) VALUES ($1)", person_id)
    await db.execute("INSERT INTO organizations (id) VALUES ($1)", org_id)
    await db.execute(
        "INSERT INTO roles (id, organization_id, title) VALUES ($1, $2, 'Ownership Seat')",
        role_id,
        org_id,
    )
    await db.execute(
        "INSERT INTO role_assignments (id, person_id, role_id, start_date, source_key_id)"
        " VALUES ($1, $2, $3, '2021-01-11', $4)",
        ra_id,
        person_id,
        role_id,
        source_key_id,
    )
    return ra_id


async def _org(db, source_key_id: str | None = None, parent_id: str | None = None) -> str:
    org_id = generate_id()
    await db.execute(
        "INSERT INTO organizations (id, parent_id, source_key_id) VALUES ($1, $2, $3)",
        org_id,
        parent_id,
        source_key_id,
    )
    return org_id


@pytest_asyncio.fixture(loop_scope="session")
async def keys(db):
    return await _write_key(db, "Owner A"), await _write_key(db, "Owner B")


# --- assignment reads ---------------------------------------------------------------------


@pytest.mark.parametrize(
    ("owner", "anchored", "expected"),
    [
        (None, False, "none"),
        ("a", False, "self"),
        ("b", False, "other"),
        (None, True, "dataset"),
        ("a", True, "dataset"),  # dataset first: the nightly apply reverts even the owner
    ],
)
async def test_assignment_ownership_is_relative_to_the_caller(
    client, db, keys, owner, anchored, expected
):
    (raw_a, kid_a), (_, kid_b) = keys
    ra_id = await _assignment(db, {"a": kid_a, "b": kid_b, None: None}[owner])
    if anchored:
        await _anchor(db, "assignment", ra_id)

    r = await client.get(f"{_ASSIGNMENTS}/{ra_id}", headers={"X-API-Key": raw_a})

    assert r.status_code == 200, r.text
    assert r.json()["ownership"] == expected


async def test_an_out_of_scope_anchor_is_not_dataset(client, db, keys):
    """Only an in-scope anchor (live, merged) is the applier's row scope."""
    (raw_a, _), _ = keys
    ra_id = await _assignment(db)
    await _anchor(db, "assignment", ra_id, resolution="archived")

    r = await client.get(f"{_ASSIGNMENTS}/{ra_id}", headers={"X-API-Key": raw_a})

    assert r.json()["ownership"] == "none"


async def test_two_keys_get_different_etags_for_one_assignment(client, db, keys):
    (raw_a, kid_a), (raw_b, _) = keys
    ra_id = await _assignment(db, kid_a)

    r_a = await client.get(f"{_ASSIGNMENTS}/{ra_id}", headers={"X-API-Key": raw_a})
    r_b = await client.get(f"{_ASSIGNMENTS}/{ra_id}", headers={"X-API-Key": raw_b})

    assert r_a.headers["etag"] != r_b.headers["etag"]
    # Each key's own validator still answers 304.
    again = await client.get(
        f"{_ASSIGNMENTS}/{ra_id}",
        headers={"X-API-Key": raw_a, "If-None-Match": r_a.headers["etag"]},
    )
    assert again.status_code == 304
    crossed = await client.get(
        f"{_ASSIGNMENTS}/{ra_id}",
        headers={"X-API-Key": raw_b, "If-None-Match": r_a.headers["etag"]},
    )
    assert crossed.status_code == 200
    assert crossed.json()["ownership"] == "other"


async def test_a_dataset_adopting_a_row_moves_its_etag(client, db, keys):
    """The anchor touches no ``updated_at``; the ETag must move all the same."""
    (raw_a, _), _ = keys
    ra_id = await _assignment(db)
    before = await client.get(f"{_ASSIGNMENTS}/{ra_id}", headers={"X-API-Key": raw_a})

    await _anchor(db, "assignment", ra_id)
    after = await client.get(
        f"{_ASSIGNMENTS}/{ra_id}",
        headers={"X-API-Key": raw_a, "If-None-Match": before.headers["etag"]},
    )

    assert after.status_code == 200
    assert after.json()["ownership"] == "dataset"


# --- assignment writes --------------------------------------------------------------------


async def test_an_update_to_a_dataset_assignment_is_refused(client, db, keys):
    (raw_a, kid_a), _ = keys
    ra_id = await _assignment(db, kid_a)  # even the row's own key
    await _anchor(db, "assignment", ra_id)

    r = await client.post(
        f"{_ASSIGNMENTS}/observations",
        json={
            "identifier_type": "pm_assignment_id",
            "identifier_value": ra_id,
            "end_date": "2024-01-08",
        },
        headers={"X-API-Key": raw_a},
    )

    body = r.json()
    assert (body["disposition"], body["reason_code"]) == ("rejected", "owned_by_dataset")
    assert await db.fetchval("SELECT end_date FROM role_assignments WHERE id=$1", ra_id) is None


async def test_an_identical_assertion_does_not_claim_a_dataset_assignment(client, db, keys):
    """#478's claim-by-agreement stops at a dataset row: no one claims what it owns."""
    (raw_a, _), _ = keys
    ra_id = await _assignment(db)
    await _anchor(db, "assignment", ra_id)

    r = await client.post(
        f"{_ASSIGNMENTS}/observations",
        json={
            "identifier_type": "pm_assignment_id",
            "identifier_value": ra_id,
            "start_date": "2021-01-11",
        },
        headers={"X-API-Key": raw_a},
    )

    body = r.json()
    assert body["disposition"] == "auto-attached"
    assert body["provenance_claimed"] is None
    stamp = await db.fetchval("SELECT source_key_id FROM role_assignments WHERE id=$1", ra_id)
    assert stamp is None


async def test_a_retract_of_a_dataset_assignment_is_refused(client, db, keys):
    (raw_a, _), _ = keys
    ra_id = await _assignment(db)
    await _anchor(db, "assignment", ra_id)

    r = await client.post(
        f"{_ASSIGNMENTS}/observations",
        json={"identifier_type": "pm_assignment_id", "identifier_value": ra_id, "op": "retract"},
        headers={"X-API-Key": raw_a},
    )

    assert r.json()["reason_code"] == "owned_by_dataset"
    archived = await db.fetchval("SELECT archived_at FROM role_assignments WHERE id=$1", ra_id)
    assert archived is None


async def test_a_natural_key_close_skips_a_dataset_assignment(client, db, keys):
    """The natural-key close-enrichment leaves a dataset row open and says so."""
    (raw_a, _), _ = keys
    ra_id = await _assignment(db)
    await _anchor(db, "assignment", ra_id)
    row = await db.fetchrow("SELECT person_id, role_id FROM role_assignments WHERE id=$1", ra_id)

    r = await client.post(
        f"{_ASSIGNMENTS}/observations",
        json={
            "person_id": row["person_id"],
            "role_id": row["role_id"],
            "start_date": "2021-01-11",
            "end_date": "2024-01-08",
        },
        headers={"X-API-Key": raw_a},
    )

    body = r.json()
    assert body["disposition"] == "auto-attached"
    assert "end_date" in body["unapplied"]
    assert await db.fetchval("SELECT end_date FROM role_assignments WHERE id=$1", ra_id) is None


# --- org reads ----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("owner", "anchored", "expected"),
    [(None, False, "none"), ("a", False, "self"), ("b", False, "other"), ("a", True, "dataset")],
)
async def test_org_parent_ownership_is_relative_to_the_caller(
    client, db, keys, owner, anchored, expected
):
    (raw_a, kid_a), (_, kid_b) = keys
    org_id = await _org(db, {"a": kid_a, "b": kid_b, None: None}[owner])
    if anchored:
        await _anchor(db, "organization", org_id)

    r = await client.get(f"{_ORGS}/{org_id}", headers={"X-API-Key": raw_a})

    assert r.status_code == 200, r.text
    assert r.json()["parent_ownership"] == expected


async def test_batch_get_orgs_matches_get_org(client, db, keys):
    """The batch answers each id as getOrg would: the same ownership and ETag."""
    (raw_a, kid_a), _ = keys
    mine, theirs = await _org(db, kid_a), await _org(db)
    await _anchor(db, "organization", theirs)

    batch = await client.post(
        f"{_ORGS}/batch-get", json={"ids": [mine, theirs]}, headers={"X-API-Key": raw_a}
    )

    results = {r["id"]: r for r in batch.json()["results"]}
    for org_id, expected in ((mine, "self"), (theirs, "dataset")):
        single = await client.get(f"{_ORGS}/{org_id}", headers={"X-API-Key": raw_a})
        assert results[org_id]["org"]["parent_ownership"] == expected
        assert results[org_id]["etag"] == single.headers["etag"]


# --- org writes ---------------------------------------------------------------------------


async def test_a_reparent_of_a_dataset_org_is_refused(client, db, keys):
    (raw_a, kid_a), _ = keys
    parent = await _org(db)
    org_id = await _org(db, kid_a)
    await _anchor(db, "organization", org_id)

    r = await client.post(
        f"{_ORGS}/observations",
        json={
            "identifier_type": "pm_org_id",
            "identifier_value": org_id,
            "organization_parent_id": parent,
        },
        headers={"X-API-Key": raw_a},
    )

    assert r.json()["reason_code"] == "owned_by_dataset"
    assert await db.fetchval("SELECT parent_id FROM organizations WHERE id=$1", org_id) is None


async def test_a_parent_fill_on_a_dataset_org_is_skipped_and_said(client, db, keys):
    """Natural key: the fill is skipped, not refused, so the rest still lands (operator,
    2026-11-25), and ``unapplied`` names the parent field the producer sent."""
    (raw_a, _), _ = keys
    parent = await _org(db)
    org_id = await _org(db)
    await _anchor(db, "organization", org_id)
    ubi = "UBI-" + generate_id()
    eit = await db.fetchval("SELECT id FROM entity_identifier_types WHERE slug='org_ubi'")
    await db.execute(
        "INSERT INTO identifiers (id, entity_id, entity_identifier_type_id, value)"
        " VALUES ($1, $2, $3, $4)",
        generate_id(),
        org_id,
        eit,
        ubi,
    )

    r = await client.post(
        f"{_ORGS}/observations",
        json={
            "identifier_type": "org_ubi",
            "identifier_value": ubi,
            "organization_parent_id": parent,
            "links": [{"url": f"https://example.com/{org_id}", "link_type_slug": "website"}],
        },
        headers={"X-API-Key": raw_a},
    )

    body = r.json()
    assert body["disposition"] == "auto-attached", body
    assert body["unapplied"] == ["organization_parent_id"]
    assert await db.fetchval("SELECT parent_id FROM organizations WHERE id=$1", org_id) is None
    links = await db.fetchval(
        "SELECT count(*) FROM links WHERE entity_type='organization' AND entity_id=$1", org_id
    )
    assert links == 1


async def test_a_parent_already_held_is_agreement(client, db, keys):
    """Re-asserting the parent a dataset org already has is a quiet no-op, as before."""
    (raw_a, _), _ = keys
    parent = await _org(db)
    org_id = await _org(db, parent_id=parent)
    await _anchor(db, "organization", org_id)

    r = await client.post(
        f"{_ORGS}/observations",
        json={
            "identifier_type": "pm_org_id",
            "identifier_value": org_id,
            "organization_parent_id": parent,
        },
        headers={"X-API-Key": raw_a},
    )

    body = r.json()
    assert body["disposition"] == "auto-attached", body
    assert body["unapplied"] is None
