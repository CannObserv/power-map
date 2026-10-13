"""Which links a dataset owns outright (#592): the lock's one predicate.

A link is the dataset's when its type is one the applier owns and both orgs it
joins are in a producer's scope — an anchored pair. Every PM write door asks
this before touching a link.
"""

import pytest
import pytest_asyncio

from src.core.dataset_ownership import DATASET_OWNED_LINK_TYPES, dataset_owned_link
from src.core.db import generate_id

pytestmark = [pytest.mark.integration]

SOURCE = "test-592-lock"


@pytest_asyncio.fixture(loop_scope="session")
async def db(db_pool):
    async with db_pool.acquire() as conn:
        tr = conn.transaction()
        await tr.start()
        try:
            yield conn
        finally:
            await tr.rollback()


async def _org(db, resolution: str | None = "live") -> str:
    org = generate_id()
    await db.execute("INSERT INTO organizations (id) VALUES ($1)", org)
    if resolution is not None:
        await db.execute(
            "INSERT INTO producer_crosswalk"
            " (id, source, kind, producer_id, exported_pm_id, pm_id, resolution)"
            " VALUES ($1, $2, 'organization', $3, $4, $4, $5)",
            generate_id(),
            SOURCE,
            f"P-{org}",
            org,
            resolution,
        )
    return org


@pytest_asyncio.fixture(loop_scope="session")
async def orgs(db):
    return {
        "a": await _org(db),
        "b": await _org(db),
        "merged": await _org(db, "merged"),
        "archived": await _org(db, "archived"),
        "bare": await _org(db, None),
    }


def test_the_locked_types_are_the_three_org_links():
    assert DATASET_OWNED_LINK_TYPES == {"succeeded_by", "merged_with", "split_from"}


@pytest.mark.parametrize("slug", sorted(DATASET_OWNED_LINK_TYPES))
async def test_an_owned_type_between_two_anchored_orgs_is_locked(db, orgs, slug):
    assert await dataset_owned_link(db, "organization", orgs["a"], slug, "organization", orgs["b"])


async def test_a_merged_anchor_is_in_scope(db, orgs):
    assert await dataset_owned_link(
        db, "organization", orgs["a"], "succeeded_by", "organization", orgs["merged"]
    )


@pytest.mark.parametrize("other", ["archived", "bare"])
async def test_a_link_with_an_end_out_of_scope_is_pms_own(db, orgs, other):
    assert not await dataset_owned_link(
        db, "organization", orgs["a"], "succeeded_by", "organization", orgs[other]
    )
    assert not await dataset_owned_link(
        db, "organization", orgs[other], "succeeded_by", "organization", orgs["a"]
    )


async def test_an_unowned_type_is_never_locked(db, orgs):
    assert not await dataset_owned_link(
        db, "organization", orgs["a"], "renamed", "organization", orgs["b"]
    )


async def test_a_link_to_a_person_is_never_locked(db, orgs):
    assert not await dataset_owned_link(
        db, "organization", orgs["a"], "succeeded_by", "person", orgs["b"]
    )


async def test_an_event_without_a_link_is_never_locked(db, orgs):
    assert not await dataset_owned_link(db, "organization", orgs["a"], "succeeded_by", None, None)


async def test_a_person_event_is_never_locked(db, orgs):
    assert not await dataset_owned_link(
        db, "person", orgs["a"], "succeeded_by", "organization", orgs["b"]
    )
