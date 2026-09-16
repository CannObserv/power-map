"""The roles binding against Postgres (#529 step 5).

What the fake cannot prove: that `slot_holders` reads each of #261's partial
indexes as the database defines it — folding `lower(title)` and honouring the
predicate that keeps a districted role out of the title index — and that a
re-key (archive the old role, create the new one on its slot) commits as one
verified transaction. A collision is refused as a `conflict` before any write,
so the index never raises.
"""

import pytest
import pytest_asyncio

from src.core.db import generate_id
from src.core.ingestion.applier import DesiredState, diff_desired
from src.core.ingestion.applier_pg import PostgresLiveStore
from src.core.ingestion.applier_write import apply_diff
from tests.core.ingestion.applier_fakes import manifest_with_roles

pytestmark = [pytest.mark.integration]

MANIFEST = manifest_with_roles()
SOURCE = "usa_wa_roles_test"
ROLES, TITLES, SPANS = "desired_roles", "desired_role_titles", "desired_role_assignments"
OLD, NEW = "committee-role:31640", "committee-role:31641"
ORG_PRODUCER = "01WORG"


@pytest_asyncio.fixture(loop_scope="session")
async def db(db_pool):
    async with db_pool.acquire() as conn:
        tr = conn.transaction()
        await tr.start()
        try:
            yield conn
        finally:
            await tr.rollback()


async def _anchor(db, kind, producer_id, pm_id):
    await db.execute(
        "INSERT INTO producer_crosswalk"
        " (id, source, kind, producer_id, exported_pm_id, pm_id, resolution)"
        " VALUES ($1, $2, $3, $4, $5, $5, 'live')",
        generate_id(),
        SOURCE,
        kind,
        producer_id,
        pm_id,
    )


@pytest_asyncio.fixture(loop_scope="session")
async def world(db):
    """An anchored organization, a district, and the two role types a test may name."""
    org = generate_id()
    await db.execute("INSERT INTO organizations (id) VALUES ($1)", org)
    await _anchor(db, "organization", ORG_PRODUCER, org)
    district = generate_id()
    await db.execute(
        "INSERT INTO jurisdictions (id, slug, name, type_id)"
        " SELECT $1, 'usa-wa-ld-34', 'Washington Legislative District 34', id"
        " FROM jurisdiction_types WHERE slug = 'legislative_district'",
        district,
    )
    return {
        "org": org,
        "district": district,
        "committee": await db.fetchval("SELECT id FROM role_types WHERE slug='committee_member'"),
        "senator": await db.fetchval("SELECT id FROM role_types WHERE slug='state_senator'"),
    }


async def _role(db, world, *, title="Member", jurisdiction=None, role_type=None, qualifier=None):
    role = generate_id()
    await db.execute(
        "INSERT INTO roles (id, organization_id, title, role_type_id, jurisdiction_id, qualifier)"
        " VALUES ($1, $2, $3, $4, $5, $6)",
        role,
        world["org"],
        title,
        role_type or world["committee"],
        jurisdiction,
        qualifier,
    )
    return role


def desired(
    producer_id, *, pm_id=None, role_type="committee_member", district=None, title="Member"
):
    return {
        "pm_id": pm_id,
        "producer_id": producer_id,
        "org_producer_id": ORG_PRODUCER,
        "role_type": role_type,
        "jurisdiction_slug": f"usa-wa-ld-{district}" if district else None,
        "qualifier": None,
        "title": title,
    }


def _state(*, roles=(), spans=()) -> DesiredState:
    tables = {name: [] for name in MANIFEST.tables}
    tables[ROLES], tables[SPANS] = list(roles), list(spans)
    return DesiredState(tables=tables, build_info=None)


async def _diff(db, state):
    return await diff_desired(state, MANIFEST, PostgresLiveStore(db), source=SOURCE)


async def _apply(db, state):
    store = PostgresLiveStore(db)
    diff = await diff_desired(state, MANIFEST, store, source=SOURCE)

    async def rediff(minted):
        return await diff_desired(state, MANIFEST, store, source=SOURCE, minted=minted)

    return diff, await apply_diff(diff, MANIFEST, db, source=SOURCE, rediff=rediff)


def _entry(diff, producer_id, table=ROLES):
    [entry] = [e for e in diff.entries if e.table == table and e.producer_id == producer_id]
    return entry


async def test_a_re_key_archives_the_old_role_and_creates_the_new_one_in_one_transaction(db, world):
    """usa-wa mints a new role_key for the same committee seat: the old role leaves,
    its span with it, and the new role takes the freed title slot."""
    old = await _role(db, world)
    await _anchor(db, "role", OLD, old)
    person = generate_id()
    await db.execute("INSERT INTO people (id) VALUES ($1)", person)
    span = generate_id()
    await db.execute(
        "INSERT INTO role_assignments (id, person_id, role_id) VALUES ($1, $2, $3)",
        span,
        person,
        old,
    )
    await _anchor(db, "assignment", "span-1", span)

    diff, result = await _apply(db, _state(roles=[desired(NEW)]))

    assert (_entry(diff, OLD).kind, _entry(diff, NEW).kind) == ("archive", "create")
    assert await db.fetchval("SELECT archived_at IS NOT NULL FROM roles WHERE id = $1", old)
    minted = result.minted[("role", NEW)]
    row = await db.fetchrow(
        "SELECT title, organization_id, archived_at FROM roles WHERE id = $1", minted
    )
    assert (row["title"], row["organization_id"], row["archived_at"]) == (
        "Member",
        world["org"],
        None,
    )
    assert (
        await db.fetchval(
            "SELECT resolution FROM producer_crosswalk WHERE source=$1 AND kind='role'"
            " AND producer_id=$2",
            SOURCE,
            NEW,
        )
        == "live"
    )


async def test_a_role_whose_live_assignment_this_run_keeps_is_a_conflict_before_any_write(
    db, world
):
    old = await _role(db, world)
    await _anchor(db, "role", OLD, old)
    person = generate_id()
    await db.execute("INSERT INTO people (id) VALUES ($1)", person)
    curated = generate_id()
    await db.execute(
        "INSERT INTO role_assignments (id, person_id, role_id) VALUES ($1, $2, $3)",
        curated,
        person,
        old,
    )

    entry = _entry(await _diff(db, _state()), OLD)

    assert entry.kind == "conflict"
    assert curated in entry.reason
    assert await db.fetchval("SELECT archived_at IS NULL FROM roles WHERE id = $1", old)


async def test_a_create_writes_the_ids_its_slugs_name(db, world):
    diff, result = await _apply(
        db, _state(roles=[desired(NEW, role_type="state_senator", district=34, title="Senator")])
    )

    row = await db.fetchrow(
        "SELECT role_type_id, jurisdiction_id, qualifier FROM roles WHERE id = $1",
        result.minted[("role", NEW)],
    )
    assert (row["role_type_id"], row["jurisdiction_id"], row["qualifier"]) == (
        world["senator"],
        world["district"],
        None,
    )


async def test_a_title_collision_is_a_conflict_and_never_a_unique_violation(db, world):
    """uq_role_org_title indexes lower(title): the create would raise, so it never runs."""
    held = await _role(db, world, title="member")

    entry = _entry(await _diff(db, _state(roles=[desired(NEW, title="Member")])), NEW)

    assert entry.kind == "conflict"
    assert held in entry.reason


async def test_a_districted_role_does_not_hold_the_title_slot(db, world):
    """The title index is partial on `jurisdiction_id IS NULL`, so the seat is invisible
    to a create without a district — PM holds both rows today."""
    await _role(
        db, world, title="Member", jurisdiction=world["district"], role_type=world["senator"]
    )

    entry = _entry(await _diff(db, _state(roles=[desired(NEW, title="Member")])), NEW)

    assert entry.kind == "create"
