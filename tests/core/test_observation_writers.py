"""Integration tests for src.core.observation per-surface writers."""

import hashlib
import os
from datetime import date

import asyncpg
import pytest
import pytest_asyncio

from src.api.public.schemas import (
    ObservationAcronym,
    ObservationAdditionalIdentifier,
    ObservationAddress,
    ObservationContactMethod,
    ObservationLink,
    ObservationOrgName,
    ObservationPersonName,
    ObservationPersonNameParts,
    ObservationRoleAssignment,
)
from src.core.db import generate_id
from src.core.observation import (
    IdentifierConflict,
    ObservationRejected,
    _heal_person_canonical,
    backfill_assignment_dates,
    write_additional_identifiers,
    write_addresses,
    write_contact_methods,
    write_links,
    write_names,
    write_org_acronyms,
    write_org_active,
    write_org_parent,
    write_pronouns,
    write_role_assignments,
)

pytestmark = [
    pytest.mark.integration,
]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture(loop_scope="session")
async def db(db_pool):
    """Pool-acquired connection wrapped in a rolled-back transaction."""
    async with db_pool.acquire() as conn:
        tr = conn.transaction()
        await tr.start()
        try:
            yield conn
        finally:
            await tr.rollback()


@pytest_asyncio.fixture(loop_scope="session")
async def api_key_id(db):
    """Insert an app_user + api_key; return the api_key_id."""
    uid = generate_id()
    kid = generate_id()
    raw_key = "pm_" + os.urandom(16).hex()
    key_hash = hashlib.sha256(raw_key.encode()).hexdigest()
    await db.execute(
        "INSERT INTO app_users (id, email) VALUES ($1, $2)", uid, "writer_test@test.com"
    )
    await db.execute(
        "INSERT INTO api_keys (id, user_id, label, key_prefix, key_hash) VALUES ($1,$2,$3,$4,$5)",
        kid,
        uid,
        "Writer Test",
        raw_key[:8],
        key_hash,
    )
    return kid


@pytest_asyncio.fixture(loop_scope="session")
async def person_id(db):
    pid = generate_id()
    await db.execute("INSERT INTO people (id) VALUES ($1)", pid)
    return pid


@pytest_asyncio.fixture(loop_scope="session")
async def org_id(db):
    oid = generate_id()
    await db.execute("INSERT INTO organizations (id) VALUES ($1)", oid)
    return oid


# ---------------------------------------------------------------------------
# write_org_active — OrgNotFound is mapped to a graceful rejection (#241)
# ---------------------------------------------------------------------------


async def test_write_org_active_missing_org_rejected(db):
    """A vanished org maps OrgNotFound → ObservationRejected('org_not_found')."""
    with pytest.raises(ObservationRejected) as exc:
        await write_org_active(db, generate_id(), False)
    assert exc.value.detail == "org_not_found"


# ---------------------------------------------------------------------------
# write_names — person
# ---------------------------------------------------------------------------


async def test_write_names_appends_new_person_name(db, person_id, api_key_id):
    name = ObservationPersonName(name="Jane Doe", name_type="legal")
    await write_names(db, person_id, "person", api_key_id, [name])
    rows = await db.fetch(
        "SELECT name, source_key_id FROM person_names WHERE person_id=$1", person_id
    )
    assert len(rows) == 1
    assert rows[0]["name"] == "Jane Doe"
    assert rows[0]["source_key_id"] == api_key_id


async def test_write_names_exact_match_is_noop(db, person_id, api_key_id):
    name = ObservationPersonName(name="Jane Doe", name_type="legal")
    await write_names(db, person_id, "person", api_key_id, [name])
    await write_names(db, person_id, "person", api_key_id, [name])
    rows = await db.fetch("SELECT id FROM person_names WHERE person_id=$1", person_id)
    assert len(rows) == 1


async def test_write_names_parts_written_on_new_row(db, person_id, api_key_id):
    parts = ObservationPersonNameParts(given_names=["Jane"], family_names=["Doe"])
    name = ObservationPersonName(name="Jane Doe", name_type="legal", parts=parts)
    await write_names(db, person_id, "person", api_key_id, [name])
    row = await db.fetchrow(
        "SELECT pnp.given_names, pnp.family_names FROM person_names pn"
        " JOIN person_name_parts pnp ON pnp.person_name_id = pn.id"
        " WHERE pn.person_id=$1",
        person_id,
    )
    assert row is not None
    assert list(row["given_names"]) == ["Jane"]
    assert list(row["family_names"]) == ["Doe"]


async def test_write_names_person_canonical_hint_promotes(db, api_key_id):
    pid = generate_id()
    await db.execute("INSERT INTO people (id) VALUES ($1)", pid)
    name = ObservationPersonName(name="Alice Smith", name_type="legal", is_canonical=True)
    await write_names(db, pid, "person", api_key_id, [name])
    row = await db.fetchrow(
        "SELECT is_canonical FROM person_names WHERE person_id=$1 AND name=$2",
        pid,
        "Alice Smith",
    )
    assert row["is_canonical"] is True


async def test_write_names_person_canonical_hint_no_displace(db, api_key_id):
    pid = generate_id()
    await db.execute("INSERT INTO people (id) VALUES ($1)", pid)
    first = ObservationPersonName(name="Alice Smith", name_type="legal", is_canonical=True)
    await write_names(db, pid, "person", api_key_id, [first])
    second = ObservationPersonName(name="Alice", name_type="preferred", is_canonical=True)
    await write_names(db, pid, "person", api_key_id, [second])
    rows = await db.fetch(
        "SELECT name, is_canonical FROM person_names WHERE person_id=$1 ORDER BY created_at",
        pid,
    )
    # One canonical slot per person (#308, Option A). The earlier per-name_type
    # key let both rows be canonical at once, which is what forced
    # v_person_display_names to disambiguate. A later hint never displaces.
    assert rows[0]["is_canonical"] is True
    assert rows[1]["is_canonical"] is False


async def test_write_names_person_canonical_hint_same_type_no_displace(db, api_key_id):
    pid = generate_id()
    await db.execute("INSERT INTO people (id) VALUES ($1)", pid)
    first = ObservationPersonName(name="Alice Smith", name_type="legal", is_canonical=True)
    await write_names(db, pid, "person", api_key_id, [first])
    second = ObservationPersonName(name="Alice J. Smith", name_type="legal", is_canonical=True)
    await write_names(db, pid, "person", api_key_id, [second])
    rows = await db.fetch(
        "SELECT name, is_canonical FROM person_names WHERE person_id=$1 ORDER BY created_at",
        pid,
    )
    assert rows[0]["name"] == "Alice Smith"
    assert rows[0]["is_canonical"] is True
    assert rows[1]["name"] == "Alice J. Smith"
    assert rows[1]["is_canonical"] is False  # first legal name stays canonical


# --- first-wins auto-promotion (#308b) -------------------------------------
# Symmetry with the org branch: a client that omits is_canonical must still end
# up with a displayable person. Guarded by NOT EXISTS — never displaces.


async def test_write_names_person_no_hint_auto_promotes(db, api_key_id):
    """The #308 regression: sole name, no canonical hint → still canonical."""
    pid = generate_id()
    await db.execute("INSERT INTO people (id) VALUES ($1)", pid)
    name = ObservationPersonName(name="Steve Kirby", name_type="legal")
    await write_names(db, pid, "person", api_key_id, [name])
    row = await db.fetchrow("SELECT is_canonical FROM person_names WHERE person_id=$1", pid)
    assert row["is_canonical"] is True


async def test_write_names_person_no_hint_renders_in_display_view(db, api_key_id):
    """End-to-end: the person no longer renders blank."""
    pid = generate_id()
    await db.execute("INSERT INTO people (id) VALUES ($1)", pid)
    await write_names(
        db,
        pid,
        "person",
        api_key_id,
        [ObservationPersonName(name="Tina Orwall", name_type="legal")],
    )
    rows = await db.fetch("SELECT display_name FROM v_person_display_names WHERE person_id=$1", pid)
    assert len(rows) == 1
    assert rows[0]["display_name"] == "Tina Orwall"


async def test_write_names_person_no_hint_does_not_displace(db, api_key_id):
    """An existing canonical is never displaced by auto-promotion."""
    pid = generate_id()
    await db.execute("INSERT INTO people (id) VALUES ($1)", pid)
    await write_names(
        db,
        pid,
        "person",
        api_key_id,
        [ObservationPersonName(name="Alice Smith", name_type="legal", is_canonical=True)],
    )
    await write_names(
        db,
        pid,
        "person",
        api_key_id,
        [ObservationPersonName(name="Alice J. Smith", name_type="legal")],
    )
    rows = await db.fetch(
        "SELECT name, is_canonical FROM person_names WHERE person_id=$1 ORDER BY created_at",
        pid,
    )
    assert rows[0]["is_canonical"] is True
    assert rows[1]["is_canonical"] is False


async def test_write_names_person_multi_name_promotes_only_one(db, api_key_id):
    """Multiple unhinted names in one write → exactly one canonical, not one per slot.

    Naive per-name_type first-wins would promote both, and the person would
    carry two canonical rows (uq_person_canonical_name permits it).
    """
    pid = generate_id()
    await db.execute("INSERT INTO people (id) VALUES ($1)", pid)
    names = [
        ObservationPersonName(name="Alice Smith", name_type="legal"),
        ObservationPersonName(name="Alice", name_type="preferred"),
    ]
    await write_names(db, pid, "person", api_key_id, names)
    rows = await db.fetch("SELECT name, is_canonical FROM person_names WHERE person_id=$1", pid)
    assert sum(1 for r in rows if r["is_canonical"]) == 1


async def test_write_names_person_multi_name_prefers_preferred(db, api_key_id):
    """Eligibility follows the display priority, not list order."""
    pid = generate_id()
    await db.execute("INSERT INTO people (id) VALUES ($1)", pid)
    names = [
        ObservationPersonName(name="Alice Smith", name_type="legal"),
        ObservationPersonName(name="Alice", name_type="preferred"),
    ]
    await write_names(db, pid, "person", api_key_id, names)
    by_name = {
        r["name"]: r["is_canonical"]
        for r in await db.fetch(
            "SELECT name, is_canonical FROM person_names WHERE person_id=$1", pid
        )
    }
    assert by_name["Alice"] is True
    assert by_name["Alice Smith"] is False


async def test_write_names_person_deadname_never_auto_promoted(db, api_key_id):
    """A deadname is forced to legal_only by trigger — never the display slot."""
    pid = generate_id()
    await db.execute("INSERT INTO people (id) VALUES ($1)", pid)
    await write_names(
        db,
        pid,
        "person",
        api_key_id,
        [ObservationPersonName(name="Old Name", name_type="deadname")],
    )
    row = await db.fetchrow(
        "SELECT is_canonical, visibility FROM person_names WHERE person_id=$1", pid
    )
    assert row["visibility"] == "legal_only"
    assert row["is_canonical"] is False


async def test_write_names_person_machine_readable_not_auto_promoted(db, api_key_id):
    """mrz/romanization/reading are not display candidates."""
    pid = generate_id()
    await db.execute("INSERT INTO people (id) VALUES ($1)", pid)
    await write_names(
        db,
        pid,
        "person",
        api_key_id,
        [ObservationPersonName(name="YAMADA<<TARO", name_type="mrz")],
    )
    row = await db.fetchrow("SELECT is_canonical FROM person_names WHERE person_id=$1", pid)
    assert row["is_canonical"] is False


async def test_write_names_person_hint_wins_over_priority(db, api_key_id):
    """An explicit client hint overrides the name_type priority ordering."""
    pid = generate_id()
    await db.execute("INSERT INTO people (id) VALUES ($1)", pid)
    names = [
        ObservationPersonName(name="Alice Smith", name_type="legal", is_canonical=True),
        ObservationPersonName(name="Alice", name_type="preferred"),
    ]
    await write_names(db, pid, "person", api_key_id, names)
    by_name = {
        r["name"]: r["is_canonical"]
        for r in await db.fetch(
            "SELECT name, is_canonical FROM person_names WHERE person_id=$1", pid
        )
    }
    assert by_name["Alice Smith"] is True
    assert by_name["Alice"] is False


async def test_write_names_person_excluded_only_leaves_no_canonical(db, api_key_id):
    """All names excluded → no canonical; a later eligible name can still claim it."""
    pid = generate_id()
    await db.execute("INSERT INTO people (id) VALUES ($1)", pid)
    await write_names(
        db,
        pid,
        "person",
        api_key_id,
        [ObservationPersonName(name="YAMADA<<TARO", name_type="mrz")],
    )
    await write_names(
        db,
        pid,
        "person",
        api_key_id,
        [ObservationPersonName(name="Taro Yamada", name_type="legal")],
    )
    by_name = {
        r["name"]: r["is_canonical"]
        for r in await db.fetch(
            "SELECT name, is_canonical FROM person_names WHERE person_id=$1", pid
        )
    }
    assert by_name["Taro Yamada"] is True
    assert by_name["YAMADA<<TARO"] is False


# --- heal-on-observe (#308, CR round 1 finding 1) ---------------------------
# Auto-promotion on insert only helps names that are new. A person already in
# the canonical-less state is re-observed with the same names every sync, hits
# the exact-match dedup, and would stay blank forever without a heal pass.


async def test_write_names_person_heals_existing_uncanonical_name(db, api_key_id):
    """Re-observing a blank person's existing name promotes it."""
    pid = generate_id()
    await db.execute("INSERT INTO people (id) VALUES ($1)", pid)
    # Simulate a person created before #308b: name present, nothing canonical.
    await db.execute(
        "INSERT INTO person_names (id, person_id, name, name_type, visibility, is_canonical)"
        " VALUES ($1, $2, 'Steve Kirby', 'legal', 'public', FALSE)",
        generate_id(),
        pid,
    )
    await write_names(
        db,
        pid,
        "person",
        api_key_id,
        [ObservationPersonName(name="Steve Kirby", name_type="legal")],
    )
    row = await db.fetchrow("SELECT is_canonical FROM person_names WHERE person_id=$1", pid)
    assert row["is_canonical"] is True


async def test_write_names_person_heal_renders_in_display_view(db, api_key_id):
    pid = generate_id()
    await db.execute("INSERT INTO people (id) VALUES ($1)", pid)
    await db.execute(
        "INSERT INTO person_names (id, person_id, name, name_type, visibility, is_canonical)"
        " VALUES ($1, $2, 'Tina Orwall', 'legal', 'public', FALSE)",
        generate_id(),
        pid,
    )
    await write_names(
        db,
        pid,
        "person",
        api_key_id,
        [ObservationPersonName(name="Tina Orwall", name_type="legal")],
    )
    assert (
        await db.fetchval("SELECT display_name FROM v_person_display_names WHERE person_id=$1", pid)
        == "Tina Orwall"
    )


async def test_write_names_person_heal_respects_priority(db, api_key_id):
    """Heal picks the same row the view would — preferred over legal."""
    pid = generate_id()
    await db.execute("INSERT INTO people (id) VALUES ($1)", pid)
    for name, ntype in [("Alice Smith", "legal"), ("Alice", "preferred")]:
        await db.execute(
            "INSERT INTO person_names (id, person_id, name, name_type, visibility, is_canonical)"
            " VALUES ($1, $2, $3, $4, 'public', FALSE)",
            generate_id(),
            pid,
            name,
            ntype,
        )
    await write_names(
        db,
        pid,
        "person",
        api_key_id,
        [ObservationPersonName(name="Alice Smith", name_type="legal")],
    )
    by_name = {
        r["name"]: r["is_canonical"]
        for r in await db.fetch(
            "SELECT name, is_canonical FROM person_names WHERE person_id=$1", pid
        )
    }
    assert by_name["Alice"] is True
    assert by_name["Alice Smith"] is False


async def test_write_names_person_heal_skips_excluded_name_types(db, api_key_id):
    """A person whose only name is a deadname stays blank — never auto-displayed."""
    pid = generate_id()
    await db.execute("INSERT INTO people (id) VALUES ($1)", pid)
    await db.execute(
        "INSERT INTO person_names (id, person_id, name, name_type, visibility, is_canonical)"
        " VALUES ($1, $2, 'Old Name', 'deadname', 'legal_only', FALSE)",
        generate_id(),
        pid,
    )
    await write_names(
        db,
        pid,
        "person",
        api_key_id,
        [ObservationPersonName(name="Old Name", name_type="deadname")],
    )
    row = await db.fetchrow("SELECT is_canonical FROM person_names WHERE person_id=$1", pid)
    assert row["is_canonical"] is False


async def test_write_names_person_heal_does_not_displace(db, api_key_id):
    """A person who already displays is left completely alone."""
    pid = generate_id()
    await db.execute("INSERT INTO people (id) VALUES ($1)", pid)
    await write_names(
        db,
        pid,
        "person",
        api_key_id,
        [ObservationPersonName(name="Alice Smith", name_type="legal", is_canonical=True)],
    )
    await db.execute(
        "INSERT INTO person_names (id, person_id, name, name_type, visibility, is_canonical)"
        " VALUES ($1, $2, 'Alice', 'preferred', 'public', FALSE)",
        generate_id(),
        pid,
    )
    await write_names(
        db,
        pid,
        "person",
        api_key_id,
        [ObservationPersonName(name="Alice", name_type="preferred")],
    )
    by_name = {
        r["name"]: r["is_canonical"]
        for r in await db.fetch(
            "SELECT name, is_canonical FROM person_names WHERE person_id=$1", pid
        )
    }
    assert by_name["Alice Smith"] is True
    assert by_name["Alice"] is False


# --- canonical-slot contention (#308, CR round 2 findings 9/10/12) ----------
# The insert fallback and the heal no-op share one deterministic trigger: a
# NON-PUBLIC canonical already occupies the (person_id, name_type, locale,
# script) slot. The auto guard only looks for a *public* canonical, so it passes;
# the unique index does not care about visibility, so it rejects the promotion.
# Reachable single-threaded via admin curation — no concurrency required.


async def test_write_names_person_hinted_guard_suppresses_same_slot(db, api_key_id):
    """Hinted promotion into an occupied same-name_type slot is suppressed.

    Covers the guard, not the fallback: the guard is keyed (person_id, name_type)
    and this row shares `legal`, so it is seen and promotion never reaches the
    index.
    """
    pid = generate_id()
    await db.execute("INSERT INTO people (id) VALUES ($1)", pid)
    await db.execute(
        "INSERT INTO person_names (id, person_id, name, name_type, locale, script,"
        " visibility, is_canonical) VALUES ($1, $2, '山田太郎', 'legal', 'ja', 'Jpan',"
        " 'public', TRUE)",
        generate_id(),
        pid,
    )
    await write_names(
        db,
        pid,
        "person",
        api_key_id,
        [
            ObservationPersonName(
                name="Yamada Taro",
                name_type="legal",
                locale="ja",
                script="Jpan",
                is_canonical=True,
            )
        ],
    )
    by_name = {
        r["name"]: r["is_canonical"]
        for r in await db.fetch(
            "SELECT name, is_canonical FROM person_names WHERE person_id=$1", pid
        )
    }
    assert by_name["Yamada Taro"] is False
    assert by_name["山田太郎"] is True


# The former "blocked slot" scenario — a non-public canonical occupying the
# person's display slot — is unreachable under Option A (#308):
# chk_person_canonical_is_public rejects it at write time. Its coverage lives in
# tests/core/test_schema_person_canonical.py, which asserts the constraint fires.


# --- heal is skipped when the write already promoted (#308, CR round 2 #11) --


class _CountingConn:
    """Delegates to a real connection, counting the heal statement.

    The heal costs a round trip against a remote DB; it must not fire when the
    insert already claimed the canonical slot.
    """

    def __init__(self, conn):
        self._conn = conn
        self.heal_calls = 0

    def __getattr__(self, item):
        return getattr(self._conn, item)

    def _tally(self, query):
        if "UPDATE person_names SET is_canonical" in query:
            self.heal_calls += 1

    async def execute(self, query, *args, **kwargs):
        self._tally(query)
        return await self._conn.execute(query, *args, **kwargs)

    async def fetchrow(self, query, *args, **kwargs):
        self._tally(query)
        return await self._conn.fetchrow(query, *args, **kwargs)

    async def fetchval(self, query, *args, **kwargs):
        self._tally(query)
        return await self._conn.fetchval(query, *args, **kwargs)


async def test_write_names_person_skips_heal_when_insert_promoted(db, api_key_id):
    pid = generate_id()
    await db.execute("INSERT INTO people (id) VALUES ($1)", pid)
    conn = _CountingConn(db)
    await write_names(
        conn,
        pid,
        "person",
        api_key_id,
        [ObservationPersonName(name="Steve Kirby", name_type="legal")],
    )
    assert conn.heal_calls == 0
    assert await db.fetchval("SELECT is_canonical FROM person_names WHERE person_id=$1", pid)


async def test_write_names_person_runs_heal_when_nothing_promoted(db, api_key_id):
    """Re-observation of an already-present name still needs the heal."""
    pid = generate_id()
    await db.execute("INSERT INTO people (id) VALUES ($1)", pid)
    await db.execute(
        "INSERT INTO person_names (id, person_id, name, name_type, visibility,"
        " is_canonical) VALUES ($1, $2, 'Steve Kirby', 'legal', 'public', FALSE)",
        generate_id(),
        pid,
    )
    conn = _CountingConn(db)
    await write_names(
        conn,
        pid,
        "person",
        api_key_id,
        [ObservationPersonName(name="Steve Kirby", name_type="legal")],
    )
    assert conn.heal_calls == 1
    assert await db.fetchval("SELECT is_canonical FROM person_names WHERE person_id=$1", pid)


async def test_write_names_person_heals_on_nameless_observation(db, api_key_id):
    """An observation carrying no names still heals a blank person (#308, #14)."""
    pid = generate_id()
    await db.execute("INSERT INTO people (id) VALUES ($1)", pid)
    await db.execute(
        "INSERT INTO person_names (id, person_id, name, name_type, visibility,"
        " is_canonical) VALUES ($1, $2, 'Steve Kirby', 'legal', 'public', FALSE)",
        generate_id(),
        pid,
    )
    await write_names(db, pid, "person", api_key_id, [])
    assert await db.fetchval("SELECT is_canonical FROM person_names WHERE person_id=$1", pid)


# ---------------------------------------------------------------------------
# write_names — organization
# ---------------------------------------------------------------------------


async def test_write_names_organization(db, org_id, api_key_id):
    name = ObservationOrgName(name="Acme Corp", name_type="legal")
    await write_names(db, org_id, "organization", api_key_id, [name])
    rows = await db.fetch(
        "SELECT name, source_key_id, is_canonical FROM organization_names WHERE organization_id=$1",
        org_id,
    )
    assert len(rows) == 1
    assert rows[0]["name"] == "Acme Corp"
    assert rows[0]["source_key_id"] == api_key_id
    assert rows[0]["is_canonical"] is True


async def test_write_names_org_multi_name_list_promotes_first(db, api_key_id):
    oid = generate_id()
    await db.execute("INSERT INTO organizations (id) VALUES ($1)", oid)
    names = [
        ObservationOrgName(name="WA Joint Committee on Education", name_type="legal"),
        ObservationOrgName(name="Joint Ed Committee", name_type="dba"),
    ]
    await write_names(db, oid, "organization", api_key_id, names)
    rows = await db.fetch(
        "SELECT is_canonical FROM organization_names WHERE organization_id=$1 ORDER BY created_at",
        oid,
    )
    assert len(rows) == 2
    assert rows[0]["is_canonical"] is True
    assert rows[1]["is_canonical"] is False


async def test_write_names_org_second_name_not_canonical(db, api_key_id):
    oid = generate_id()
    await db.execute("INSERT INTO organizations (id) VALUES ($1)", oid)
    first = ObservationOrgName(name="Senate Finance Committee", name_type="legal")
    await write_names(db, oid, "organization", api_key_id, [first])
    second = ObservationOrgName(name="Finance Committee", name_type="dba")
    await write_names(db, oid, "organization", api_key_id, [second])
    rows = await db.fetch(
        "SELECT is_canonical FROM organization_names WHERE organization_id=$1 ORDER BY created_at",
        oid,
    )
    assert len(rows) == 2
    assert rows[0]["is_canonical"] is True
    assert rows[1]["is_canonical"] is False


async def test_write_names_org_canonical_hint_promotes_specific(db, api_key_id):
    """is_canonical=True on a non-first name → that name becomes canonical, not the first."""
    oid = generate_id()
    await db.execute("INSERT INTO organizations (id) VALUES ($1)", oid)
    names = [
        ObservationOrgName(name="WA Leg", name_type="dba", is_canonical=False),
        ObservationOrgName(
            name="Washington State Legislature", name_type="legal", is_canonical=True
        ),
    ]
    await write_names(db, oid, "organization", api_key_id, names)
    rows = await db.fetch(
        "SELECT name, is_canonical FROM organization_names WHERE organization_id=$1",
        oid,
    )
    by_name = {r["name"]: r["is_canonical"] for r in rows}
    assert by_name["WA Leg"] is False
    assert by_name["Washington State Legislature"] is True


async def test_write_names_org_canonical_hint_no_displace(db, api_key_id):
    """is_canonical=True does not displace an already-canonical name."""
    oid = generate_id()
    await db.execute("INSERT INTO organizations (id) VALUES ($1)", oid)
    first = ObservationOrgName(name="First Name", name_type="legal")
    await write_names(db, oid, "organization", api_key_id, [first])
    second = ObservationOrgName(name="Second Name", name_type="dba", is_canonical=True)
    await write_names(db, oid, "organization", api_key_id, [second])
    rows = await db.fetch(
        "SELECT name, is_canonical FROM organization_names"
        " WHERE organization_id=$1 ORDER BY created_at",
        oid,
    )
    assert rows[0]["name"] == "First Name"
    assert rows[0]["is_canonical"] is True
    assert rows[1]["name"] == "Second Name"
    assert rows[1]["is_canonical"] is False


async def test_write_names_org_stores_effective_dates(db, api_key_id):
    """Effective dates on an observation name are persisted on the new row (#239)."""
    oid = generate_id()
    await db.execute("INSERT INTO organizations (id) VALUES ($1)", oid)
    name = ObservationOrgName(
        name="Committee on Old Government",
        name_type="former",
        effective_start=date(2019, 1, 1),
        effective_end=date(2023, 1, 9),
    )
    await write_names(db, oid, "organization", api_key_id, [name])
    row = await db.fetchrow(
        "SELECT effective_start, effective_end FROM organization_names"
        " WHERE organization_id=$1 AND name=$2",
        oid,
        "Committee on Old Government",
    )
    assert row["effective_start"] == date(2019, 1, 1)
    assert row["effective_end"] == date(2023, 1, 9)


async def test_write_names_org_effective_dates_noop_on_existing(db, api_key_id):
    """Append-only: re-observing an existing name with dates does not mutate it (#239)."""
    oid = generate_id()
    await db.execute("INSERT INTO organizations (id) VALUES ($1)", oid)
    await write_names(
        db,
        oid,
        "organization",
        api_key_id,
        [ObservationOrgName(name="Stable Name", name_type="legal")],
    )
    # Same name re-sent with dates — must not touch the existing (NULL/NULL) row.
    await write_names(
        db,
        oid,
        "organization",
        api_key_id,
        [
            ObservationOrgName(
                name="Stable Name",
                name_type="legal",
                effective_start=date(2020, 1, 1),
                effective_end=date(2021, 1, 1),
            )
        ],
    )
    rows = await db.fetch(
        "SELECT effective_start, effective_end FROM organization_names"
        " WHERE organization_id=$1 AND name=$2",
        oid,
        "Stable Name",
    )
    assert len(rows) == 1
    assert rows[0]["effective_start"] is None
    assert rows[0]["effective_end"] is None


# ---------------------------------------------------------------------------
# write_links
# ---------------------------------------------------------------------------


async def test_write_links_appends_new(db, person_id):
    link = ObservationLink(url="https://example.com/jane", link_type_slug="website")
    await write_links(db, person_id, "person", [link])
    rows = await db.fetch(
        "SELECT url, link_type_id FROM links WHERE entity_type='person' AND entity_id=$1",
        person_id,
    )
    assert len(rows) == 1
    assert rows[0]["url"] == "https://example.com/jane"
    lt = await db.fetchrow("SELECT id FROM link_types WHERE slug='website'")
    assert rows[0]["link_type_id"] == lt["id"]


async def test_write_links_duplicate_is_noop(db, person_id):
    link = ObservationLink(url="https://example.com/jane", link_type_slug="website")
    await write_links(db, person_id, "person", [link])
    await write_links(db, person_id, "person", [link])
    rows = await db.fetch(
        "SELECT id FROM links WHERE entity_type='person' AND entity_id=$1", person_id
    )
    assert len(rows) == 1


async def test_write_links_by_id(db, person_id):
    lt = await db.fetchrow("SELECT id FROM link_types WHERE slug='twitter'")
    link = ObservationLink(url="https://twitter.com/jane", link_type_id=lt["id"])
    await write_links(db, person_id, "person", [link])
    rows = await db.fetch(
        "SELECT link_type_id FROM links WHERE entity_type='person' AND entity_id=$1",
        person_id,
    )
    assert len(rows) == 1
    assert rows[0]["link_type_id"] == lt["id"]


async def test_write_links_insert_writes_entity_changes(db):
    """Initial INSERT of a link writes an entity_changes row."""
    pid = generate_id()
    await db.execute("INSERT INTO people (id) VALUES ($1)", pid)
    before = await db.fetchval(
        "SELECT COUNT(*) FROM entity_changes WHERE entity_type='person' AND entity_id=$1", pid
    )
    link = ObservationLink(url="https://example.com/new", link_type_slug="website")
    await write_links(db, pid, "person", [link])
    after = await db.fetchval(
        "SELECT COUNT(*) FROM entity_changes WHERE entity_type='person' AND entity_id=$1", pid
    )
    assert after - before == 1


# ---------------------------------------------------------------------------
# write_contact_methods
# ---------------------------------------------------------------------------


async def test_write_contact_methods_phone_normalized(db, person_id):
    cm = ObservationContactMethod(contact_type="phone", value="(206) 555-1234")
    await write_contact_methods(db, person_id, "person", [cm])
    rows = await db.fetch(
        "SELECT value FROM contact_methods WHERE entity_type='person' AND entity_id=$1",
        person_id,
    )
    assert len(rows) == 1
    assert rows[0]["value"] == "+12065551234"


async def test_write_contact_methods_invalid_phone_raises(db, person_id):
    cm = ObservationContactMethod(contact_type="phone", value="not a phone")
    with pytest.raises(ObservationRejected):
        await write_contact_methods(db, person_id, "person", [cm])


async def test_write_contact_methods_duplicate_noop(db, person_id):
    cm1 = ObservationContactMethod(contact_type="phone", value="(206) 555-1234")
    cm2 = ObservationContactMethod(contact_type="phone", value="+1 206 555 1234")
    await write_contact_methods(db, person_id, "person", [cm1, cm2])
    rows = await db.fetch(
        "SELECT id FROM contact_methods WHERE entity_type='person' AND entity_id=$1",
        person_id,
    )
    assert len(rows) == 1


async def test_write_contact_methods_email(db, person_id):
    cm = ObservationContactMethod(contact_type="email", value="Jane@Example.com")
    await write_contact_methods(db, person_id, "person", [cm])
    rows = await db.fetch(
        "SELECT value FROM contact_methods WHERE entity_type='person' AND entity_id=$1",
        person_id,
    )
    assert len(rows) == 1
    # email-validator normalizes domain to lowercase
    assert rows[0]["value"].endswith("@example.com")


async def test_write_contact_methods_insert_writes_entity_changes(db):
    """Initial INSERT of a contact method writes an entity_changes row."""
    pid = generate_id()
    await db.execute("INSERT INTO people (id) VALUES ($1)", pid)
    before = await db.fetchval(
        "SELECT COUNT(*) FROM entity_changes WHERE entity_type='person' AND entity_id=$1", pid
    )
    cm = ObservationContactMethod(contact_type="phone", value="(206) 555-0100")
    await write_contact_methods(db, pid, "person", [cm])
    after = await db.fetchval(
        "SELECT COUNT(*) FROM entity_changes WHERE entity_type='person' AND entity_id=$1", pid
    )
    assert after - before == 1


async def test_write_contact_methods_null_fill_updates_label(db):
    """display_label NULL-filled from re-observation; entity_changes row written."""
    pid = generate_id()
    await db.execute("INSERT INTO people (id) VALUES ($1)", pid)
    cm1 = ObservationContactMethod(contact_type="phone", value="(206) 555-0101")
    await write_contact_methods(db, pid, "person", [cm1])
    before = await db.fetchval(
        "SELECT COUNT(*) FROM entity_changes WHERE entity_type='person' AND entity_id=$1", pid
    )
    cm2 = ObservationContactMethod(
        contact_type="phone", value="(206) 555-0101", display_label="Main"
    )
    await write_contact_methods(db, pid, "person", [cm2])
    row = await db.fetchrow(
        "SELECT display_label FROM contact_methods WHERE entity_type='person' AND entity_id=$1",
        pid,
    )
    after = await db.fetchval(
        "SELECT COUNT(*) FROM entity_changes WHERE entity_type='person' AND entity_id=$1", pid
    )
    assert row["display_label"] == "Main"
    assert after - before == 1


async def test_write_contact_methods_existing_label_not_overwritten(db):
    """Non-NULL display_label is not overwritten; no entity_changes row written."""
    pid = generate_id()
    await db.execute("INSERT INTO people (id) VALUES ($1)", pid)
    cm1 = ObservationContactMethod(
        contact_type="phone", value="(206) 555-0102", display_label="Office"
    )
    await write_contact_methods(db, pid, "person", [cm1])
    before = await db.fetchval(
        "SELECT COUNT(*) FROM entity_changes WHERE entity_type='person' AND entity_id=$1", pid
    )
    cm2 = ObservationContactMethod(
        contact_type="phone", value="(206) 555-0102", display_label="Mobile"
    )
    await write_contact_methods(db, pid, "person", [cm2])
    row = await db.fetchrow(
        "SELECT display_label FROM contact_methods WHERE entity_type='person' AND entity_id=$1",
        pid,
    )
    after = await db.fetchval(
        "SELECT COUNT(*) FROM entity_changes WHERE entity_type='person' AND entity_id=$1", pid
    )
    assert row["display_label"] == "Office"
    assert after - before == 0


async def test_write_contact_methods_null_fill_idempotent(db):
    """Second re-observation after label already filled → no new entity_changes."""
    pid = generate_id()
    await db.execute("INSERT INTO people (id) VALUES ($1)", pid)
    cm1 = ObservationContactMethod(contact_type="phone", value="(206) 555-0103")
    await write_contact_methods(db, pid, "person", [cm1])
    cm2 = ObservationContactMethod(contact_type="phone", value="(206) 555-0103", display_label="WA")
    await write_contact_methods(db, pid, "person", [cm2])
    before = await db.fetchval(
        "SELECT COUNT(*) FROM entity_changes WHERE entity_type='person' AND entity_id=$1", pid
    )
    await write_contact_methods(db, pid, "person", [cm2])
    after = await db.fetchval(
        "SELECT COUNT(*) FROM entity_changes WHERE entity_type='person' AND entity_id=$1", pid
    )
    assert after - before == 0


async def test_write_contact_methods_empty_string_label_skipped(db):
    """Empty string display_label is not written; no entity_changes row emitted."""
    pid = generate_id()
    await db.execute("INSERT INTO people (id) VALUES ($1)", pid)
    cm1 = ObservationContactMethod(contact_type="phone", value="(206) 555-0104")
    await write_contact_methods(db, pid, "person", [cm1])
    before = await db.fetchval(
        "SELECT COUNT(*) FROM entity_changes WHERE entity_type='person' AND entity_id=$1", pid
    )
    cm2 = ObservationContactMethod(contact_type="phone", value="(206) 555-0104", display_label="")
    await write_contact_methods(db, pid, "person", [cm2])
    row = await db.fetchrow(
        "SELECT display_label FROM contact_methods WHERE entity_type='person' AND entity_id=$1",
        pid,
    )
    after = await db.fetchval(
        "SELECT COUNT(*) FROM entity_changes WHERE entity_type='person' AND entity_id=$1", pid
    )
    assert row["display_label"] is None
    assert after - before == 0


async def test_write_contact_methods_initial_empty_string_stored_as_null(db):
    """Initial INSERT with display_label='' stores NULL so future NULL-fill can land."""
    pid = generate_id()
    await db.execute("INSERT INTO people (id) VALUES ($1)", pid)
    cm1 = ObservationContactMethod(contact_type="phone", value="(206) 555-0105", display_label="")
    await write_contact_methods(db, pid, "person", [cm1])
    row = await db.fetchrow(
        "SELECT display_label FROM contact_methods WHERE entity_type='person' AND entity_id=$1",
        pid,
    )
    assert row["display_label"] is None
    # Confirm a subsequent real label can fill the slot
    cm2 = ObservationContactMethod(
        contact_type="phone", value="(206) 555-0105", display_label="Main"
    )
    await write_contact_methods(db, pid, "person", [cm2])
    row2 = await db.fetchrow(
        "SELECT display_label FROM contact_methods WHERE entity_type='person' AND entity_id=$1",
        pid,
    )
    assert row2["display_label"] == "Main"


# ---------------------------------------------------------------------------
# write_addresses
# ---------------------------------------------------------------------------


async def test_write_addresses_basic(db, org_id, local_address_normalizer):
    """write_addresses inserts an address row and an entity_addresses join."""
    addr = ObservationAddress(raw_input="123 Main St, Seattle, WA 98101", address_type="mailing")
    await write_addresses(db, org_id, "organization", [addr])
    rows = await db.fetch(
        "SELECT ea.address_type, a.raw_input FROM entity_addresses ea"
        " JOIN addresses a ON a.id = ea.address_id"
        " WHERE ea.entity_type='organization' AND ea.entity_id=$1",
        org_id,
    )
    assert len(rows) == 1
    assert rows[0]["address_type"] == "mailing"


async def test_write_addresses_duplicate_noop(db, org_id, local_address_normalizer):
    addr = ObservationAddress(raw_input="123 Main St, Seattle, WA 98101", address_type="mailing")
    await write_addresses(db, org_id, "organization", [addr])
    await write_addresses(db, org_id, "organization", [addr])
    rows = await db.fetch(
        "SELECT id FROM entity_addresses WHERE entity_type='organization' AND entity_id=$1",
        org_id,
    )
    assert len(rows) == 1


async def test_write_addresses_insert_writes_entity_changes(db, local_address_normalizer):
    """Initial INSERT of an address writes an entity_changes row."""
    oid = generate_id()
    await db.execute("INSERT INTO organizations (id) VALUES ($1)", oid)
    before = await db.fetchval(
        "SELECT COUNT(*) FROM entity_changes WHERE entity_type='organization' AND entity_id=$1",
        oid,
    )
    addr = ObservationAddress(raw_input="456 Pine St, Seattle, WA 98101", address_type="physical")
    await write_addresses(db, oid, "organization", [addr])
    after = await db.fetchval(
        "SELECT COUNT(*) FROM entity_changes WHERE entity_type='organization' AND entity_id=$1",
        oid,
    )
    assert after - before == 1


async def test_write_addresses_null_fill_updates_display_name(db, local_address_normalizer):
    """display_name NULL-filled from re-observation; entity_changes row written."""
    oid = generate_id()
    await db.execute("INSERT INTO organizations (id) VALUES ($1)", oid)
    addr1 = ObservationAddress(raw_input="789 Oak Ave, Seattle, WA 98101", address_type="mailing")
    await write_addresses(db, oid, "organization", [addr1])
    before = await db.fetchval(
        "SELECT COUNT(*) FROM entity_changes WHERE entity_type='organization' AND entity_id=$1",
        oid,
    )
    addr2 = ObservationAddress(
        raw_input="789 Oak Ave, Seattle, WA 98101", address_type="mailing", display_name="HQ"
    )
    await write_addresses(db, oid, "organization", [addr2])
    row = await db.fetchrow(
        "SELECT ea.display_name FROM entity_addresses ea"
        " WHERE ea.entity_type='organization' AND ea.entity_id=$1",
        oid,
    )
    after = await db.fetchval(
        "SELECT COUNT(*) FROM entity_changes WHERE entity_type='organization' AND entity_id=$1",
        oid,
    )
    assert row["display_name"] == "HQ"
    assert after - before == 1


async def test_write_addresses_existing_display_name_not_overwritten(db, local_address_normalizer):
    """Non-NULL display_name is not overwritten; no entity_changes row written."""
    oid = generate_id()
    await db.execute("INSERT INTO organizations (id) VALUES ($1)", oid)
    addr1 = ObservationAddress(
        raw_input="321 Elm St, Seattle, WA 98101", address_type="mailing", display_name="Branch"
    )
    await write_addresses(db, oid, "organization", [addr1])
    before = await db.fetchval(
        "SELECT COUNT(*) FROM entity_changes WHERE entity_type='organization' AND entity_id=$1",
        oid,
    )
    addr2 = ObservationAddress(
        raw_input="321 Elm St, Seattle, WA 98101", address_type="mailing", display_name="HQ"
    )
    await write_addresses(db, oid, "organization", [addr2])
    row = await db.fetchrow(
        "SELECT ea.display_name FROM entity_addresses ea"
        " WHERE ea.entity_type='organization' AND ea.entity_id=$1",
        oid,
    )
    after = await db.fetchval(
        "SELECT COUNT(*) FROM entity_changes WHERE entity_type='organization' AND entity_id=$1",
        oid,
    )
    assert row["display_name"] == "Branch"
    assert after - before == 0


async def test_write_addresses_null_fill_idempotent(db, local_address_normalizer):
    """Second re-observation after display_name already filled → no new entity_changes."""
    oid = generate_id()
    await db.execute("INSERT INTO organizations (id) VALUES ($1)", oid)
    addr1 = ObservationAddress(raw_input="654 Maple Dr, Seattle, WA 98101", address_type="other")
    await write_addresses(db, oid, "organization", [addr1])
    addr2 = ObservationAddress(
        raw_input="654 Maple Dr, Seattle, WA 98101", address_type="other", display_name="Annex"
    )
    await write_addresses(db, oid, "organization", [addr2])
    before = await db.fetchval(
        "SELECT COUNT(*) FROM entity_changes WHERE entity_type='organization' AND entity_id=$1",
        oid,
    )
    await write_addresses(db, oid, "organization", [addr2])
    after = await db.fetchval(
        "SELECT COUNT(*) FROM entity_changes WHERE entity_type='organization' AND entity_id=$1",
        oid,
    )
    assert after - before == 0


async def test_write_addresses_empty_string_display_name_skipped(db, local_address_normalizer):
    """Empty string display_name is not written; no entity_changes row emitted."""
    oid = generate_id()
    await db.execute("INSERT INTO organizations (id) VALUES ($1)", oid)
    addr1 = ObservationAddress(raw_input="987 Cedar Rd, Seattle, WA 98101", address_type="mailing")
    await write_addresses(db, oid, "organization", [addr1])
    before = await db.fetchval(
        "SELECT COUNT(*) FROM entity_changes WHERE entity_type='organization' AND entity_id=$1",
        oid,
    )
    addr2 = ObservationAddress(
        raw_input="987 Cedar Rd, Seattle, WA 98101", address_type="mailing", display_name=""
    )
    await write_addresses(db, oid, "organization", [addr2])
    row = await db.fetchrow(
        "SELECT ea.display_name FROM entity_addresses ea"
        " WHERE ea.entity_type='organization' AND ea.entity_id=$1",
        oid,
    )
    after = await db.fetchval(
        "SELECT COUNT(*) FROM entity_changes WHERE entity_type='organization' AND entity_id=$1",
        oid,
    )
    assert row["display_name"] is None
    assert after - before == 0


async def test_write_addresses_initial_empty_string_stored_as_null(db, local_address_normalizer):
    """Initial INSERT with display_name='' stores NULL so future NULL-fill can land."""
    oid = generate_id()
    await db.execute("INSERT INTO organizations (id) VALUES ($1)", oid)
    addr1 = ObservationAddress(
        raw_input="111 Test Ave, Seattle, WA 98101",
        address_type="mailing",
        display_name="",
    )
    await write_addresses(db, oid, "organization", [addr1])
    row = await db.fetchrow(
        "SELECT ea.display_name FROM entity_addresses ea"
        " WHERE ea.entity_type='organization' AND ea.entity_id=$1",
        oid,
    )
    assert row["display_name"] is None
    # Confirm a subsequent real label can fill the slot
    addr2 = ObservationAddress(
        raw_input="111 Test Ave, Seattle, WA 98101",
        address_type="mailing",
        display_name="HQ",
    )
    await write_addresses(db, oid, "organization", [addr2])
    row2 = await db.fetchrow(
        "SELECT ea.display_name FROM entity_addresses ea"
        " WHERE ea.entity_type='organization' AND ea.entity_id=$1",
        oid,
    )
    assert row2["display_name"] == "HQ"


# write_addresses — validity windows (#256)


async def test_write_addresses_dated_claim_stores_window(db, org_id, local_address_normalizer):
    """A dated claim persists valid_from / valid_until on the entity_addresses link."""
    addr = ObservationAddress(
        raw_input="123 Main St, Seattle, WA 98101",
        address_type="mailing",
        valid_from="2020-01-01",
        valid_until="2021-12-31",
    )
    await write_addresses(db, org_id, "organization", [addr])
    row = await db.fetchrow(
        "SELECT valid_from, valid_until FROM entity_addresses"
        " WHERE entity_type='organization' AND entity_id=$1",
        org_id,
    )
    assert row["valid_from"] == date(2020, 1, 1)
    assert row["valid_until"] == date(2021, 12, 31)


async def test_write_addresses_same_window_dedups(db, org_id, local_address_normalizer):
    """Re-observing the same form + same window is a no-op."""
    addr = ObservationAddress(
        raw_input="123 Main St, Seattle, WA 98101",
        address_type="mailing",
        valid_from="2020-01-01",
        valid_until="2021-12-31",
    )
    await write_addresses(db, org_id, "organization", [addr])
    await write_addresses(db, org_id, "organization", [addr])
    rows = await db.fetch(
        "SELECT id FROM entity_addresses WHERE entity_type='organization' AND entity_id=$1",
        org_id,
    )
    assert len(rows) == 1


async def test_write_addresses_different_window_new_link_reuses_address(
    db, org_id, local_address_normalizer
):
    """A dated claim for a new window creates a second link but reuses the addresses row."""
    first = ObservationAddress(
        raw_input="123 Main St, Seattle, WA 98101",
        address_type="mailing",
        valid_from="2018-01-01",
        valid_until="2019-12-31",
    )
    second = ObservationAddress(
        raw_input="123 Main St, Seattle, WA 98101",
        address_type="mailing",
        valid_from="2020-01-01",
    )
    await write_addresses(db, org_id, "organization", [first])
    await write_addresses(db, org_id, "organization", [second])
    rows = await db.fetch(
        "SELECT address_id, valid_from, valid_until FROM entity_addresses"
        " WHERE entity_type='organization' AND entity_id=$1 ORDER BY valid_from",
        org_id,
    )
    assert len(rows) == 2
    # Same physical address reused across both windows — no duplicate addresses row.
    assert len({r["address_id"] for r in rows}) == 1
    assert rows[1]["valid_from"] == date(2020, 1, 1)
    assert rows[1]["valid_until"] is None


async def test_write_addresses_dateless_claim_matches_dated_row(
    db, org_id, local_address_normalizer
):
    """Dateless re-observation matches any existing row (incl. dated) — records nothing new.

    Admin end-dating stays authoritative: a feed cannot resurrect a current window (#256).
    """
    dated = ObservationAddress(
        raw_input="123 Main St, Seattle, WA 98101",
        address_type="mailing",
        valid_from="2018-01-01",
        valid_until="2019-12-31",
    )
    dateless = ObservationAddress(
        raw_input="123 Main St, Seattle, WA 98101", address_type="mailing"
    )
    await write_addresses(db, org_id, "organization", [dated])
    await write_addresses(db, org_id, "organization", [dateless])
    rows = await db.fetch(
        "SELECT valid_from, valid_until FROM entity_addresses"
        " WHERE entity_type='organization' AND entity_id=$1",
        org_id,
    )
    assert len(rows) == 1
    assert rows[0]["valid_from"] == date(2018, 1, 1)


async def test_write_addresses_dated_claim_not_deduped_by_dateless_row(
    db, org_id, local_address_normalizer
):
    """A dated claim does not match a pre-existing dateless row — strict window equality."""
    dateless = ObservationAddress(
        raw_input="123 Main St, Seattle, WA 98101", address_type="mailing"
    )
    dated = ObservationAddress(
        raw_input="123 Main St, Seattle, WA 98101",
        address_type="mailing",
        valid_from="2020-01-01",
    )
    await write_addresses(db, org_id, "organization", [dateless])
    await write_addresses(db, org_id, "organization", [dated])
    rows = await db.fetch(
        "SELECT address_id, valid_from FROM entity_addresses"
        " WHERE entity_type='organization' AND entity_id=$1 ORDER BY valid_from NULLS FIRST",
        org_id,
    )
    assert len(rows) == 2
    assert len({r["address_id"] for r in rows}) == 1  # addresses row reused
    assert rows[0]["valid_from"] is None
    assert rows[1]["valid_from"] == date(2020, 1, 1)


# ---------------------------------------------------------------------------
# write_org_acronyms
# ---------------------------------------------------------------------------


async def test_write_org_acronyms_appends(db, org_id):
    await write_org_acronyms(db, org_id, [ObservationAcronym(acronym="ACME")])
    rows = await db.fetch(
        "SELECT acronym, is_canonical FROM organization_acronyms WHERE organization_id=$1",
        org_id,
    )
    assert len(rows) == 1
    assert rows[0]["acronym"] == "ACME"
    assert rows[0]["is_canonical"] is True


async def test_write_org_acronyms_duplicate_noop(db, org_id):
    await write_org_acronyms(db, org_id, [ObservationAcronym(acronym="ACME")])
    await write_org_acronyms(db, org_id, [ObservationAcronym(acronym="ACME")])
    rows = await db.fetch("SELECT id FROM organization_acronyms WHERE organization_id=$1", org_id)
    assert len(rows) == 1


async def test_write_org_acronyms_second_not_canonical(db):
    oid = generate_id()
    await db.execute("INSERT INTO organizations (id) VALUES ($1)", oid)
    await write_org_acronyms(db, oid, [ObservationAcronym(acronym="WLEG")])
    await write_org_acronyms(db, oid, [ObservationAcronym(acronym="WA-LEG")])
    rows = await db.fetch(
        "SELECT acronym, is_canonical FROM organization_acronyms"
        " WHERE organization_id=$1 ORDER BY created_at",
        oid,
    )
    assert len(rows) == 2
    assert rows[0]["is_canonical"] is True
    assert rows[1]["is_canonical"] is False


async def test_write_org_acronyms_canonical_hint_promotes_specific(db):
    """is_canonical=True on non-first acronym → that one becomes canonical."""
    oid = generate_id()
    await db.execute("INSERT INTO organizations (id) VALUES ($1)", oid)
    acronyms = [
        ObservationAcronym(acronym="WL", is_canonical=False),
        ObservationAcronym(acronym="WLEG", is_canonical=True),
    ]
    await write_org_acronyms(db, oid, acronyms)
    rows = await db.fetch(
        "SELECT acronym, is_canonical FROM organization_acronyms WHERE organization_id=$1",
        oid,
    )
    by_acronym = {r["acronym"]: r["is_canonical"] for r in rows}
    assert by_acronym["WL"] is False
    assert by_acronym["WLEG"] is True


async def test_write_org_acronyms_canonical_hint_no_displace(db):
    """is_canonical=True does not displace an already-canonical acronym."""
    oid = generate_id()
    await db.execute("INSERT INTO organizations (id) VALUES ($1)", oid)
    await write_org_acronyms(db, oid, [ObservationAcronym(acronym="FIRST")])
    await write_org_acronyms(db, oid, [ObservationAcronym(acronym="SECOND", is_canonical=True)])
    rows = await db.fetch(
        "SELECT acronym, is_canonical FROM organization_acronyms"
        " WHERE organization_id=$1 ORDER BY created_at",
        oid,
    )
    assert rows[0]["acronym"] == "FIRST"
    assert rows[0]["is_canonical"] is True
    assert rows[1]["acronym"] == "SECOND"
    assert rows[1]["is_canonical"] is False


# ---------------------------------------------------------------------------
# write_role_assignments
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture(loop_scope="session")
async def role_id(db, org_id):
    rid = generate_id()
    await db.execute(
        "INSERT INTO roles (id, organization_id, title) VALUES ($1, $2, $3)",
        rid,
        org_id,
        "Test Role",
    )
    return rid


async def test_write_role_assignments_creates(db, person_id, role_id):
    ra = ObservationRoleAssignment(role_id=role_id, start_date="2024-01-01")
    await write_role_assignments(db, person_id, [ra])
    rows = await db.fetch(
        "SELECT role_id, start_date FROM role_assignments WHERE person_id=$1", person_id
    )
    assert len(rows) == 1
    assert rows[0]["role_id"] == role_id


async def test_write_role_assignments_open_noop(db, person_id, role_id):
    """Open assignment (no end_date) already exists → no-op."""
    ra_id = generate_id()
    await db.execute(
        "INSERT INTO role_assignments (id, person_id, role_id, start_date) VALUES ($1, $2, $3, $4)",
        ra_id,
        person_id,
        role_id,
        date(2023, 1, 1),
    )
    ra = ObservationRoleAssignment(role_id=role_id, start_date="2024-01-01")
    await write_role_assignments(db, person_id, [ra])
    rows = await db.fetch(
        "SELECT id FROM role_assignments WHERE person_id=$1 AND role_id=$2",
        person_id,
        role_id,
    )
    assert len(rows) == 1


# ---------------------------------------------------------------------------
# backfill_assignment_dates (#289)
# ---------------------------------------------------------------------------


async def test_backfill_assignment_dates_rejects_archived(db, person_id, role_id):
    """Defense-in-depth: an archived target is rejected, not silently mutated."""
    ra_id = generate_id()
    await db.execute(
        "INSERT INTO role_assignments (id, person_id, role_id, archived_at)"
        " VALUES ($1, $2, $3, NOW())",
        ra_id,
        person_id,
        role_id,
    )
    with pytest.raises(ObservationRejected, match="assignment_not_found"):
        await backfill_assignment_dates(db, ra_id, date(2013, 1, 14), None)
    row = await db.fetchrow("SELECT start_date FROM role_assignments WHERE id=$1", ra_id)
    assert row["start_date"] is None  # untouched


# ---------------------------------------------------------------------------
# write_org_parent
# ---------------------------------------------------------------------------


async def test_write_org_parent_sets_when_null(db, org_id):
    parent_id = generate_id()
    await db.execute("INSERT INTO organizations (id) VALUES ($1)", parent_id)
    await write_org_parent(db, org_id, parent_id)
    row = await db.fetchrow("SELECT parent_id FROM organizations WHERE id=$1", org_id)
    assert row["parent_id"] == parent_id


async def test_write_org_parent_noop_when_set(db, org_id):
    p1 = generate_id()
    p2 = generate_id()
    await db.execute("INSERT INTO organizations (id) VALUES ($1)", p1)
    await db.execute("INSERT INTO organizations (id) VALUES ($1)", p2)
    await write_org_parent(db, org_id, p1)
    await write_org_parent(db, org_id, p2)
    row = await db.fetchrow("SELECT parent_id FROM organizations WHERE id=$1", org_id)
    assert row["parent_id"] == p1


# ---------------------------------------------------------------------------
# write_pronouns
# ---------------------------------------------------------------------------


async def test_write_pronouns_sets_when_null(db, person_id):
    await write_pronouns(db, person_id, "she/her")
    row = await db.fetchrow("SELECT personal_pronouns FROM people WHERE id=$1", person_id)
    assert row["personal_pronouns"] == "she/her"


async def test_write_pronouns_noop_when_set(db, person_id):
    await write_pronouns(db, person_id, "she/her")
    await write_pronouns(db, person_id, "they/them")
    row = await db.fetchrow("SELECT personal_pronouns FROM people WHERE id=$1", person_id)
    assert row["personal_pronouns"] == "she/her"


# ---------------------------------------------------------------------------
# write_additional_identifiers
# ---------------------------------------------------------------------------


async def test_write_additional_identifiers_new_type(db, person_id):
    item = ObservationAdditionalIdentifier(
        identifier_type_slug="person_ssn", identifier_value="123-45-6789"
    )
    await write_additional_identifiers(db, person_id, [item])
    eit = await db.fetchrow("SELECT id FROM entity_identifier_types WHERE slug='person_ssn'")
    row = await db.fetchrow(
        "SELECT value FROM identifiers WHERE entity_id=$1 AND entity_identifier_type_id=$2",
        person_id,
        eit["id"],
    )
    assert row is not None
    assert row["value"] == "123-45-6789"


async def test_write_additional_identifiers_same_value_noop(db, person_id):
    item = ObservationAdditionalIdentifier(
        identifier_type_slug="person_ssn", identifier_value="123-45-6789"
    )
    await write_additional_identifiers(db, person_id, [item])
    await write_additional_identifiers(db, person_id, [item])
    eit = await db.fetchrow("SELECT id FROM entity_identifier_types WHERE slug='person_ssn'")
    rows = await db.fetch(
        "SELECT id FROM identifiers WHERE entity_id=$1 AND entity_identifier_type_id=$2",
        person_id,
        eit["id"],
    )
    assert len(rows) == 1


async def test_write_additional_identifiers_conflict_raises(db, person_id):
    mk = lambda v: [  # noqa: E731
        ObservationAdditionalIdentifier(identifier_type_slug="person_ssn", identifier_value=v)
    ]
    await write_additional_identifiers(db, person_id, mk("123"))
    with pytest.raises(IdentifierConflict) as exc:
        await write_additional_identifiers(db, person_id, mk("999"))
    assert exc.value.identifier_type_slug == "person_ssn"


# --- CR round 3: identity-based eligibility + display-aware promotion --------


async def test_write_names_person_hinted_deadname_does_not_seal_slot(db, api_key_id):
    """#21: a hinted deadname must not suppress the heal for a good public name.

    trg_deadname_visibility rewrites the row to legal_only, so `is_canonical`
    alone does not mean the person displays.
    """
    pid = generate_id()
    await db.execute("INSERT INTO people (id) VALUES ($1)", pid)
    await write_names(
        db,
        pid,
        "person",
        api_key_id,
        [
            ObservationPersonName(name="Old Name", name_type="deadname", is_canonical=True),
            ObservationPersonName(name="New Name", name_type="legal"),
        ],
    )
    assert (
        await db.fetchval("SELECT display_name FROM v_person_display_names WHERE person_id=$1", pid)
        == "New Name"
    )


async def test_write_names_person_hinted_deadname_alone_stays_blank(db, api_key_id):
    """No eligible public name exists — blank is correct, and nothing is sealed."""
    pid = generate_id()
    await db.execute("INSERT INTO people (id) VALUES ($1)", pid)
    await write_names(
        db,
        pid,
        "person",
        api_key_id,
        [ObservationPersonName(name="Old Name", name_type="deadname", is_canonical=True)],
    )
    assert (
        await db.fetchval("SELECT display_name FROM v_person_display_names WHERE person_id=$1", pid)
        is None
    )


async def test_write_names_person_same_string_excluded_type_not_promoted(db, api_key_id):
    """#22: eligibility is by identity, not name string.

    An mrz row sharing its string with a legal row must never claim the display
    slot just by being first in the list.
    """
    pid = generate_id()
    await db.execute("INSERT INTO people (id) VALUES ($1)", pid)
    await write_names(
        db,
        pid,
        "person",
        api_key_id,
        [
            ObservationPersonName(name="ALICE SMITH", name_type="mrz"),
            ObservationPersonName(name="ALICE SMITH", name_type="legal"),
        ],
    )
    rows = {
        (r["name_type"], r["is_canonical"])
        for r in await db.fetch(
            "SELECT name_type, is_canonical FROM person_names WHERE person_id=$1", pid
        )
    }
    assert ("mrz", True) not in rows, "machine-readable name claimed the display slot"
    assert (
        await db.fetchval("SELECT display_name FROM v_person_display_names WHERE person_id=$1", pid)
        == "ALICE SMITH"
    )
    assert ("legal", True) in rows


async def test_write_names_person_same_string_different_type_both_kept(db, api_key_id):
    """The name-only dedup must not silently drop a distinct name_type claim."""
    pid = generate_id()
    await db.execute("INSERT INTO people (id) VALUES ($1)", pid)
    await write_names(
        db,
        pid,
        "person",
        api_key_id,
        [
            ObservationPersonName(name="ALICE SMITH", name_type="mrz"),
            ObservationPersonName(name="ALICE SMITH", name_type="legal"),
        ],
    )
    types = {
        r["name_type"]
        for r in await db.fetch("SELECT name_type FROM person_names WHERE person_id=$1", pid)
    }
    assert types == {"mrz", "legal"}


async def test_write_names_person_same_string_priority_still_applies(db, api_key_id):
    """#22: with equal strings, `preferred` must still outrank `legal`."""
    pid = generate_id()
    await db.execute("INSERT INTO people (id) VALUES ($1)", pid)
    await write_names(
        db,
        pid,
        "person",
        api_key_id,
        [
            ObservationPersonName(name="Jane Roe", name_type="legal"),
            ObservationPersonName(name="Jane Roe", name_type="preferred"),
        ],
    )
    canon = await db.fetchval(
        "SELECT name_type FROM person_names WHERE person_id=$1 AND is_canonical", pid
    )
    assert canon == "preferred"


async def test_write_names_person_exact_same_row_still_deduped(db, api_key_id):
    """Same name AND type AND locale/script is still a no-op (unchanged policy)."""
    pid = generate_id()
    await db.execute("INSERT INTO people (id) VALUES ($1)", pid)
    n = ObservationPersonName(name="Jane Doe", name_type="legal")
    await write_names(db, pid, "person", api_key_id, [n])
    await write_names(db, pid, "person", api_key_id, [n])
    assert await db.fetchval("SELECT count(*) FROM person_names WHERE person_id=$1", pid) == 1


# --- CR round 3: heal recovers from a concurrent slot claim (#15/#23) --------


class _ViolatingConn:
    """Delegates everything, but makes the heal UPDATE raise UniqueViolationError.

    Stands in for a concurrent transaction committing a conflicting canonical
    between the heal's snapshot and its UPDATE — real interleaving needs two
    committed connections, which the rollback fixture cannot provide.
    """

    def __init__(self, conn):
        self._conn = conn

    def __getattr__(self, item):
        return getattr(self._conn, item)

    def transaction(self, *a, **k):
        return self._conn.transaction(*a, **k)

    async def execute(self, query, *args, **kwargs):
        if "UPDATE person_names SET is_canonical" in query:
            raise asyncpg.exceptions.UniqueViolationError(
                'duplicate key value violates unique constraint "uq_person_canonical_name"'
            )
        return await self._conn.execute(query, *args, **kwargs)

    async def fetchrow(self, query, *args, **kwargs):
        return await self._conn.fetchrow(query, *args, **kwargs)

    async def fetchval(self, query, *args, **kwargs):
        return await self._conn.fetchval(query, *args, **kwargs)


async def test_heal_survives_concurrent_slot_claim(db, api_key_id):
    """A lost race must not abort the enclosing observation transaction.

    Without recovery the UniqueViolationError propagates out of write_names and
    the route rejects the entire observation as db_constraint_violation,
    discarding links, addresses, role assignments and events (#23).
    """
    pid = generate_id()
    await db.execute("INSERT INTO people (id) VALUES ($1)", pid)
    await db.execute(
        "INSERT INTO person_names (id, person_id, name, name_type, visibility,"
        " is_canonical) VALUES ($1, $2, 'Steve Kirby', 'legal', 'public', FALSE)",
        generate_id(),
        pid,
    )
    await _heal_person_canonical(_ViolatingConn(db), pid)
    # The enclosing transaction must still be usable — that is the whole point.
    assert await db.fetchval("SELECT 1") == 1
