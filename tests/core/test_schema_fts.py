"""Integration tests for FTS infrastructure (#201).

Covers TS configs, search_tsv columns, triggers, GIN indexes,
and query-level behaviour for orgs, people, roles, and jurisdictions.
"""

import pytest
import pytest_asyncio

from src.core.db import generate_id

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


# ---------------------------------------------------------------------------
# Step 1 — TS configurations
# ---------------------------------------------------------------------------


async def test_pm_simple_config_exists(db):
    row = await db.fetchrow("SELECT 1 FROM pg_ts_config WHERE cfgname = 'pm_simple'")
    assert row is not None, "pm_simple TS config missing"


async def test_pm_unaccent_simple_config_exists(db):
    row = await db.fetchrow("SELECT 1 FROM pg_ts_config WHERE cfgname = 'pm_unaccent_simple'")
    assert row is not None, "pm_unaccent_simple TS config missing"


async def test_pm_simple_strips_punctuation(db):
    """Jr. and Jr both reduce to the same lexeme."""
    row = await db.fetchrow(
        "SELECT to_tsvector('pm_simple', 'Jr.') @@ to_tsquery('pm_simple', 'jr') AS match"
    )
    assert row["match"] is True


async def test_pm_simple_preserves_and(db):
    """'and' is kept as a lexeme (not a stop word)."""
    row = await db.fetchrow(
        "SELECT to_tsvector('pm_simple', 'Health and Human Services') "
        "@@ plainto_tsquery('pm_simple', 'and') AS match"
    )
    assert row["match"] is True


async def test_pm_unaccent_simple_strips_accents(db):
    """Hernández and hernandez resolve to the same lexeme."""
    row = await db.fetchrow(
        "SELECT to_tsvector('pm_unaccent_simple', 'Hernández') "
        "@@ to_tsquery('pm_unaccent_simple', 'hernandez') AS match"
    )
    assert row["match"] is True


async def test_pm_unaccent_simple_query_side_strips_accents(db):
    """Query with accent matches stored plain form."""
    row = await db.fetchrow(
        "SELECT to_tsvector('pm_unaccent_simple', 'Hernandez') "
        "@@ to_tsquery('pm_unaccent_simple', 'hernández') AS match"
    )
    assert row["match"] is True


# ---------------------------------------------------------------------------
# Step 2 — organizations.search_tsv column + triggers
# ---------------------------------------------------------------------------


async def _insert_org(conn, *, notes=None):
    oid = generate_id()
    await conn.execute("INSERT INTO organizations (id, notes) VALUES ($1, $2)", oid, notes)
    return oid


async def _insert_org_name(conn, org_id, name, *, canonical=False, name_type="legal"):
    nid = generate_id()
    await conn.execute(
        "INSERT INTO organization_names (id, organization_id, name, name_type, is_canonical)"
        " VALUES ($1, $2, $3, $4, $5)",
        nid,
        org_id,
        name,
        name_type,
        canonical,
    )
    return nid


async def _insert_org_acronym(conn, org_id, acronym, *, canonical=False):
    aid = generate_id()
    await conn.execute(
        "INSERT INTO organization_acronyms (id, organization_id, acronym, is_canonical)"
        " VALUES ($1, $2, $3, $4)",
        aid,
        org_id,
        acronym,
        canonical,
    )
    return aid


async def test_organizations_has_search_tsv_column(db):
    row = await db.fetchrow(
        "SELECT data_type FROM information_schema.columns "
        "WHERE table_name = 'organizations' AND column_name = 'search_tsv'"
    )
    assert row is not None, "organizations.search_tsv column missing"


async def test_organizations_search_tsv_gin_index_exists(db):
    row = await db.fetchrow(
        "SELECT 1 FROM pg_indexes "
        "WHERE tablename = 'organizations' AND indexname = 'idx_organizations_search_tsv'"
    )
    assert row is not None, "GIN index on organizations.search_tsv missing"


async def test_org_search_tsv_populated_by_name(db):
    oid = await _insert_org(db)
    await _insert_org_name(db, oid, "Appropriations Committee", canonical=True)
    row = await db.fetchrow(
        "SELECT search_tsv @@ plainto_tsquery('pm_simple', 'appropriations') AS match "
        "FROM organizations WHERE id = $1",
        oid,
    )
    assert row["match"] is True


async def test_org_search_tsv_populated_by_name_variant(db):
    oid = await _insert_org(db)
    await _insert_org_name(db, oid, "Dept of Ecology", canonical=True)
    await _insert_org_name(db, oid, "Ecology Department", name_type="dba")
    row = await db.fetchrow(
        "SELECT search_tsv @@ plainto_tsquery('pm_simple', 'ecology department') AS match "
        "FROM organizations WHERE id = $1",
        oid,
    )
    assert row["match"] is True


async def test_org_search_tsv_populated_by_acronym(db):
    oid = await _insert_org(db)
    await _insert_org_name(db, oid, "Washington State Patrol", canonical=True)
    await _insert_org_acronym(db, oid, "WSP", canonical=True)
    row = await db.fetchrow(
        "SELECT search_tsv @@ to_tsquery('pm_simple', 'wsp') AS match "
        "FROM organizations WHERE id = $1",
        oid,
    )
    assert row["match"] is True


async def test_org_search_tsv_populated_by_notes(db):
    oid = await _insert_org(db, notes="Handles environmental regulation statewide")
    await _insert_org_name(db, oid, "Dept of Ecology", canonical=True)
    row = await db.fetchrow(
        "SELECT search_tsv @@ plainto_tsquery('pm_simple', 'environmental regulation') AS match "
        "FROM organizations WHERE id = $1",
        oid,
    )
    assert row["match"] is True


async def test_org_search_tsv_updates_on_name_delete(db):
    oid = await _insert_org(db)
    nid = await _insert_org_name(db, oid, "TransientName", canonical=True)
    row = await db.fetchrow(
        "SELECT search_tsv @@ plainto_tsquery('pm_simple', 'transientname') AS match "
        "FROM organizations WHERE id = $1",
        oid,
    )
    assert row["match"] is True

    await db.execute("DELETE FROM organization_names WHERE id = $1", nid)
    row = await db.fetchrow(
        "SELECT search_tsv @@ plainto_tsquery('pm_simple', 'transientname') AS match "
        "FROM organizations WHERE id = $1",
        oid,
    )
    assert row["match"] is False


async def test_org_search_tsv_updates_on_notes_change(db):
    oid = await _insert_org(db, notes="original note")
    await _insert_org_name(db, oid, "Some Org", canonical=True)
    await db.execute("UPDATE organizations SET notes = 'updated note' WHERE id = $1", oid)
    row = await db.fetchrow(
        "SELECT search_tsv @@ plainto_tsquery('pm_simple', 'updated') AS match "
        "FROM organizations WHERE id = $1",
        oid,
    )
    assert row["match"] is True


# ---------------------------------------------------------------------------
# Step 3 — people.search_tsv column + triggers
# ---------------------------------------------------------------------------


async def _insert_person(conn, *, notes=None):
    pid = generate_id()
    await conn.execute("INSERT INTO people (id, notes) VALUES ($1, $2)", pid, notes)
    return pid


async def _insert_person_name(
    conn, person_id, name, *, canonical=False, visibility="public", name_type="legal"
):
    nid = generate_id()
    await conn.execute(
        "INSERT INTO person_names (id, person_id, name, name_type, is_canonical, visibility)"
        " VALUES ($1, $2, $3, $4, $5, $6)",
        nid,
        person_id,
        name,
        name_type,
        canonical,
        visibility,
    )
    return nid


async def test_people_has_search_tsv_column(db):
    row = await db.fetchrow(
        "SELECT data_type FROM information_schema.columns "
        "WHERE table_name = 'people' AND column_name = 'search_tsv'"
    )
    assert row is not None, "people.search_tsv column missing"


async def test_people_search_tsv_gin_index_exists(db):
    row = await db.fetchrow(
        "SELECT 1 FROM pg_indexes "
        "WHERE tablename = 'people' AND indexname = 'idx_people_search_tsv'"
    )
    assert row is not None, "GIN index on people.search_tsv missing"


async def test_person_search_tsv_populated_by_public_name(db):
    pid = await _insert_person(db)
    await _insert_person_name(db, pid, "Jane Smith", canonical=True, visibility="public")
    row = await db.fetchrow(
        "SELECT search_tsv @@ plainto_tsquery('pm_unaccent_simple', 'jane smith') AS match "
        "FROM people WHERE id = $1",
        pid,
    )
    assert row["match"] is True


async def test_person_search_tsv_excludes_hidden_name(db):
    pid = await _insert_person(db)
    await _insert_person_name(db, pid, "DeadName", canonical=False, visibility="hidden")
    row = await db.fetchrow(
        "SELECT search_tsv @@ plainto_tsquery('pm_unaccent_simple', 'deadname') AS match "
        "FROM people WHERE id = $1",
        pid,
    )
    assert row["match"] is False


async def test_person_search_tsv_updates_when_visibility_changes_to_hidden(db):
    pid = await _insert_person(db)
    nid = await _insert_person_name(db, pid, "NowHidden", canonical=True, visibility="public")
    row = await db.fetchrow(
        "SELECT search_tsv @@ plainto_tsquery('pm_unaccent_simple', 'nowhidden') AS match "
        "FROM people WHERE id = $1",
        pid,
    )
    assert row["match"] is True

    await db.execute("UPDATE person_names SET visibility = 'hidden' WHERE id = $1", nid)
    row = await db.fetchrow(
        "SELECT search_tsv @@ plainto_tsquery('pm_unaccent_simple', 'nowhidden') AS match "
        "FROM people WHERE id = $1",
        pid,
    )
    assert row["match"] is False


async def test_person_search_tsv_accent_normalization(db):
    pid = await _insert_person(db)
    await _insert_person_name(db, pid, "Hernández", canonical=True, visibility="public")
    row = await db.fetchrow(
        "SELECT search_tsv @@ plainto_tsquery('pm_unaccent_simple', 'hernandez') AS match "
        "FROM people WHERE id = $1",
        pid,
    )
    assert row["match"] is True


async def test_person_search_tsv_punctuation_normalization(db):
    pid = await _insert_person(db)
    await _insert_person_name(db, pid, "John Smith Jr.", canonical=True, visibility="public")
    row = await db.fetchrow(
        "SELECT search_tsv @@ plainto_tsquery('pm_unaccent_simple', 'jr') AS match "
        "FROM people WHERE id = $1",
        pid,
    )
    assert row["match"] is True


async def test_person_search_tsv_populated_by_notes(db):
    pid = await _insert_person(db, notes="former state legislator")
    await _insert_person_name(db, pid, "Pat Jones", canonical=True)
    row = await db.fetchrow(
        "SELECT search_tsv @@ plainto_tsquery('pm_unaccent_simple', 'legislator') AS match "
        "FROM people WHERE id = $1",
        pid,
    )
    assert row["match"] is True


# ---------------------------------------------------------------------------
# Step 4 — roles.search_tsv column + trigger
# ---------------------------------------------------------------------------


async def _insert_role(conn, org_id, title, *, notes=None):
    rid = generate_id()
    await conn.execute(
        "INSERT INTO roles (id, organization_id, title, notes) VALUES ($1, $2, $3, $4)",
        rid,
        org_id,
        title,
        notes,
    )
    return rid


async def test_roles_has_search_tsv_column(db):
    row = await db.fetchrow(
        "SELECT data_type FROM information_schema.columns "
        "WHERE table_name = 'roles' AND column_name = 'search_tsv'"
    )
    assert row is not None, "roles.search_tsv column missing"


async def test_roles_search_tsv_gin_index_exists(db):
    row = await db.fetchrow(
        "SELECT 1 FROM pg_indexes WHERE tablename = 'roles' AND indexname = 'idx_roles_search_tsv'"
    )
    assert row is not None, "GIN index on roles.search_tsv missing"


async def test_role_search_tsv_populated_by_title(db):
    oid = await _insert_org(db)
    await _insert_org_name(db, oid, "State Legislature", canonical=True)
    rid = await _insert_role(db, oid, "Appropriations Committee Chair")
    row = await db.fetchrow(
        "SELECT search_tsv @@ plainto_tsquery('pm_simple', 'appropriations chair') AS match "
        "FROM roles WHERE id = $1",
        rid,
    )
    assert row["match"] is True


async def test_role_search_tsv_populated_by_notes(db):
    oid = await _insert_org(db)
    await _insert_org_name(db, oid, "State Legislature", canonical=True)
    rid = await _insert_role(db, oid, "Director", notes="oversees budget planning")
    row = await db.fetchrow(
        "SELECT search_tsv @@ plainto_tsquery('pm_simple', 'budget planning') AS match "
        "FROM roles WHERE id = $1",
        rid,
    )
    assert row["match"] is True


async def test_role_search_tsv_updates_on_title_change(db):
    oid = await _insert_org(db)
    await _insert_org_name(db, oid, "State Legislature", canonical=True)
    rid = await _insert_role(db, oid, "OldTitle")
    await db.execute("UPDATE roles SET title = 'NewTitle' WHERE id = $1", rid)
    old_match = await db.fetchrow(
        "SELECT search_tsv @@ plainto_tsquery('pm_simple', 'oldtitle') AS match "
        "FROM roles WHERE id = $1",
        rid,
    )
    new_match = await db.fetchrow(
        "SELECT search_tsv @@ plainto_tsquery('pm_simple', 'newtitle') AS match "
        "FROM roles WHERE id = $1",
        rid,
    )
    assert old_match["match"] is False
    assert new_match["match"] is True


# ---------------------------------------------------------------------------
# Step 5 — jurisdictions.search_tsv column + trigger
# ---------------------------------------------------------------------------


async def _insert_jurisdiction(conn, name, slug, *, notes=None):
    jtype_id = await conn.fetchval("SELECT id FROM jurisdiction_types LIMIT 1")
    if not jtype_id:
        jtype_id = generate_id()
        await conn.execute(
            "INSERT INTO jurisdiction_types (id, slug, display_name) VALUES ($1, $2, $3)",
            jtype_id,
            "state",
            "State",
        )
    jid = generate_id()
    await conn.execute(
        "INSERT INTO jurisdictions (id, slug, name, type_id, notes) VALUES ($1, $2, $3, $4, $5)",
        jid,
        slug,
        name,
        jtype_id,
        notes,
    )
    return jid


async def test_jurisdictions_has_search_tsv_column(db):
    row = await db.fetchrow(
        "SELECT data_type FROM information_schema.columns "
        "WHERE table_name = 'jurisdictions' AND column_name = 'search_tsv'"
    )
    assert row is not None, "jurisdictions.search_tsv column missing"


async def test_jurisdictions_search_tsv_gin_index_exists(db):
    row = await db.fetchrow(
        "SELECT 1 FROM pg_indexes "
        "WHERE tablename = 'jurisdictions' AND indexname = 'idx_jurisdictions_search_tsv'"
    )
    assert row is not None, "GIN index on jurisdictions.search_tsv missing"


async def test_jurisdiction_search_tsv_populated_by_name(db):
    jid = await _insert_jurisdiction(db, "Washington State", "usa-wa-fts-test")
    row = await db.fetchrow(
        "SELECT search_tsv @@ plainto_tsquery('pm_simple', 'washington') AS match "
        "FROM jurisdictions WHERE id = $1",
        jid,
    )
    assert row["match"] is True


async def test_jurisdiction_search_tsv_populated_by_slug(db):
    jid = await _insert_jurisdiction(db, "Test Jurisdiction", "usa-or-fts-test")
    row = await db.fetchrow(
        "SELECT search_tsv @@ to_tsquery('pm_simple', 'usa') AS match "
        "FROM jurisdictions WHERE id = $1",
        jid,
    )
    assert row["match"] is True


async def test_jurisdiction_search_tsv_populated_by_notes(db):
    jid = await _insert_jurisdiction(
        db, "Test State", "usa-xx-fts-test", notes="Pacific Northwest region"
    )
    row = await db.fetchrow(
        "SELECT search_tsv @@ plainto_tsquery('pm_simple', 'pacific northwest') AS match "
        "FROM jurisdictions WHERE id = $1",
        jid,
    )
    assert row["match"] is True


# ---------------------------------------------------------------------------
# Step 6 — trigram GIN indexes for admin typeaheads
# ---------------------------------------------------------------------------


async def test_org_names_trigram_index_exists(db):
    row = await db.fetchrow(
        "SELECT 1 FROM pg_indexes "
        "WHERE tablename = 'organization_names' AND indexname = 'idx_org_names_name_trgm'"
    )
    assert row is not None, "Trigram GIN index on organization_names.name missing"


async def test_person_names_trigram_index_exists(db):
    row = await db.fetchrow(
        "SELECT 1 FROM pg_indexes "
        "WHERE tablename = 'person_names' AND indexname = 'idx_person_names_name_trgm'"
    )
    assert row is not None, "Trigram GIN index on person_names.name missing"


async def test_roles_title_trigram_index_exists(db):
    row = await db.fetchrow(
        "SELECT 1 FROM pg_indexes WHERE tablename = 'roles' AND indexname = 'idx_roles_title_trgm'"
    )
    assert row is not None, "Trigram GIN index on roles.title missing"
