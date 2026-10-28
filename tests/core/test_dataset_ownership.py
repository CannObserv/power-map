"""Which links a dataset owns outright (#592): the lock's one predicate — and (#710)
which person identifiers.

A link is the dataset's when its type is one the applier owns and both orgs it
joins are in a producer's scope — an anchored pair. Every PM write door asks
this before touching a link. An identifier is the dataset's when its type is one
the applier asserts and its person is in that scope.
"""

import pytest
import pytest_asyncio

from src.core.dataset_ownership import (
    DATASET_OWNED_IDENTIFIER_TYPES,
    DATASET_OWNED_LINK_TYPES,
    dataset_owned_identifier,
    dataset_owned_link,
)
from src.core.db import generate_id
from src.core.ingestion.crosswalk import PRODUCER_SOURCE

pytestmark = [pytest.mark.integration]

SOURCE = PRODUCER_SOURCE  # the applier's scope is this source's anchors (CR 2)


@pytest_asyncio.fixture(loop_scope="session")
async def db(db_pool):
    async with db_pool.acquire() as conn:
        tr = conn.transaction()
        await tr.start()
        try:
            yield conn
        finally:
            await tr.rollback()


async def _entity(
    db, table: str, kind: str, resolution: str | None = "live", *, source: str = SOURCE
) -> str:
    row = generate_id()
    await db.execute(f"INSERT INTO {table} (id) VALUES ($1)", row)
    if resolution is not None:
        await db.execute(
            "INSERT INTO producer_crosswalk"
            " (id, source, kind, producer_id, exported_pm_id, pm_id, resolution)"
            " VALUES ($1, $2, $6, $3, $4, $4, $5)",
            generate_id(),
            source,
            f"P-{row}",
            row,
            resolution,
            kind,
        )
    return row


async def _org(db, resolution: str | None = "live", *, source: str = SOURCE) -> str:
    return await _entity(db, "organizations", "organization", resolution, source=source)


@pytest_asyncio.fixture(loop_scope="session")
async def orgs(db):
    return {
        "a": await _org(db),
        "b": await _org(db),
        "merged": await _org(db, "merged"),
        "archived": await _org(db, "archived"),
        "bare": await _org(db, None),
        "foreign": await _org(db, source="another-producer"),
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


@pytest.mark.parametrize("other", ["archived", "bare", "foreign"])
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


# --- #710: person identifiers ------------------------------------------------------


@pytest_asyncio.fixture(loop_scope="session")
async def people(db):
    return {
        label: await _entity(db, "people", "person", resolution, source=source)
        for label, resolution, source in (
            ("live", "live", SOURCE),
            ("merged", "merged", SOURCE),
            ("archived", "archived", SOURCE),
            ("bare", None, SOURCE),
            ("foreign", "live", "another-producer"),
        )
    }


async def _type(db, slug):
    return await db.fetchval("SELECT id FROM entity_identifier_types WHERE slug = $1", slug)


def test_the_locked_identifier_types_are_the_two_wa_legislature_keys():
    assert DATASET_OWNED_IDENTIFIER_TYPES == {
        "person_wa_legislature_roster",
        "person_wa_legislature_member_id",
    }


@pytest.mark.parametrize("person", ["live", "merged"])
@pytest.mark.parametrize("slug", sorted(DATASET_OWNED_IDENTIFIER_TYPES))
async def test_an_owned_type_on_an_anchored_person_is_locked(db, people, person, slug):
    assert await dataset_owned_identifier(db, "person", people[person], await _type(db, slug))


@pytest.mark.parametrize("person", ["archived", "bare", "foreign"])
async def test_an_owned_type_on_a_person_out_of_scope_is_pms_own(db, people, person):
    type_id = await _type(db, "person_wa_legislature_roster")

    assert not await dataset_owned_identifier(db, "person", people[person], type_id)


async def test_another_identifier_type_is_never_locked(db, people):
    other = await db.fetchval(
        "SELECT id FROM entity_identifier_types WHERE entity_type = 'person'"
        " AND slug <> ALL($1::text[]) LIMIT 1",
        sorted(DATASET_OWNED_IDENTIFIER_TYPES),
    )

    assert not await dataset_owned_identifier(db, "person", people["live"], other)


async def test_an_org_identifier_is_never_locked(db, orgs):
    type_id = await _type(db, "person_wa_legislature_roster")

    assert not await dataset_owned_identifier(db, "organization", orgs["a"], type_id)
