"""Schema tests for person_names i18n / cultural-awareness columns."""

import os

import asyncpg
import pytest

from src.core.db import apply_schema, generate_id

pytestmark = pytest.mark.integration


@pytest.fixture
async def db():
    dsn = os.environ.get("TEST_DATABASE_URL")
    if not dsn:
        pytest.skip("TEST_DATABASE_URL not set")
    conn = await asyncpg.connect(dsn)
    try:
        await apply_schema(conn)
        tr = conn.transaction()
        await tr.start()
        yield conn
        await tr.rollback()
    finally:
        await conn.close()


async def _person(conn) -> str:
    pid = generate_id()
    await conn.execute("INSERT INTO people (id) VALUES ($1)", pid)
    return pid


# Each tuple: (column, expected information_schema.data_type)
NEW_COLUMNS = [
    ("locale", "text"),
    ("script", "text"),
    ("sort_as", "text"),
    ("primary_identifier", "text"),
    ("visibility", "text"),
    ("reading_of_id", "text"),
    ("given_names", "ARRAY"),
    ("family_names", "ARRAY"),
    ("additional_names", "ARRAY"),
    ("honorific_prefix", "text"),
    ("honorific_suffix", "text"),
]


@pytest.mark.parametrize("column,data_type", NEW_COLUMNS)
async def test_person_names_has_column(db, column, data_type):
    row = await db.fetchrow(
        "SELECT data_type FROM information_schema.columns "
        "WHERE table_name='person_names' AND column_name=$1",
        column,
    )
    assert row is not None, f"person_names.{column} missing"
    assert row["data_type"] == data_type or row["data_type"].lower().startswith(
        data_type.lower()
    )


async def test_visibility_default_public(db):
    pid = await _person(db)
    nid = generate_id()
    await db.execute(
        "INSERT INTO person_names (id, person_id, name) VALUES ($1, $2, $3)",
        nid, pid, "Test Name",
    )
    row = await db.fetchrow("SELECT visibility FROM person_names WHERE id=$1", nid)
    assert row["visibility"] == "public"


async def test_primary_identifier_check_constraint(db):
    pid = await _person(db)
    with pytest.raises(asyncpg.CheckViolationError):
        await db.execute(
            "INSERT INTO person_names (id, person_id, name, primary_identifier)"
            " VALUES ($1, $2, $3, 'invalid_value')",
            generate_id(), pid, "Test",
        )


async def test_visibility_check_constraint(db):
    pid = await _person(db)
    with pytest.raises(asyncpg.CheckViolationError):
        await db.execute(
            "INSERT INTO person_names (id, person_id, name, visibility)"
            " VALUES ($1, $2, $3, 'bogus')",
            generate_id(), pid, "Test",
        )


async def test_reading_of_id_self_reference(db):
    """FK from person_names to itself (used later for phonetic / romanization / MRZ)."""
    pid = await _person(db)
    visual_id = generate_id()
    reading_id = generate_id()
    await db.execute(
        "INSERT INTO person_names (id, person_id, name, name_type, script)"
        " VALUES ($1, $2, $3, $4, $5)",
        visual_id, pid, "毛澤東", "legal", "Hant",
    )
    # Use a name_type already permitted by the Task-1 CHECK; Task 2 will add
    # 'romanization' / 'reading' / 'mrz' as semantic-specific values.
    await db.execute(
        "INSERT INTO person_names "
        "(id, person_id, name, name_type, script, reading_of_id)"
        " VALUES ($1, $2, $3, $4, $5, $6)",
        reading_id, pid, "Máo Zédōng", "alias", "Latn", visual_id,
    )
    row = await db.fetchrow(
        "SELECT reading_of_id FROM person_names WHERE id=$1", reading_id
    )
    assert row["reading_of_id"] == visual_id


async def test_structured_parts_arrays(db):
    pid = await _person(db)
    nid = generate_id()
    await db.execute(
        "INSERT INTO person_names ("
        "id, person_id, name, given_names, family_names, primary_identifier"
        ") VALUES ($1, $2, $3, $4, $5, $6)",
        nid, pid, "María José García López",
        ["María", "José"], ["García", "López"], "family",
    )
    row = await db.fetchrow(
        "SELECT given_names, family_names, primary_identifier "
        "FROM person_names WHERE id=$1",
        nid,
    )
    assert row["given_names"] == ["María", "José"]
    assert row["family_names"] == ["García", "López"]
    assert row["primary_identifier"] == "family"


# --- Task 2: name_type expansion ---

NEW_NAME_TYPES = [
    "deadname", "mrz", "reading", "romanization",
    "maiden", "religious", "stage",
]


@pytest.mark.parametrize("name_type", NEW_NAME_TYPES)
async def test_person_names_accepts_new_name_type(db, name_type):
    pid = await _person(db)
    await db.execute(
        "INSERT INTO person_names (id, person_id, name, name_type)"
        " VALUES ($1, $2, $3, $4)",
        generate_id(), pid, "Test", name_type,
    )


async def test_person_names_rejects_unknown_name_type(db):
    pid = await _person(db)
    with pytest.raises(asyncpg.CheckViolationError):
        await db.execute(
            "INSERT INTO person_names (id, person_id, name, name_type)"
            " VALUES ($1, $2, $3, $4)",
            generate_id(), pid, "Test", "totally_invalid",
        )


# --- Task 3: canonical-uniqueness keyed on (locale, script) ---

async def test_canonical_unique_per_locale_script(db):
    """Two canonical legal names with different scripts coexist."""
    pid = await _person(db)
    await db.execute(
        "INSERT INTO person_names "
        "(id, person_id, name, name_type, is_canonical, script)"
        " VALUES ($1, $2, $3, 'legal', TRUE, 'Hant')",
        generate_id(), pid, "毛澤東",
    )
    await db.execute(
        "INSERT INTO person_names "
        "(id, person_id, name, name_type, is_canonical, script)"
        " VALUES ($1, $2, $3, 'legal', TRUE, 'Latn')",
        generate_id(), pid, "Mao Zedong",
    )


async def test_canonical_unique_collision(db):
    """Two canonical legal names with same (locale, script) collide."""
    pid = await _person(db)
    await db.execute(
        "INSERT INTO person_names "
        "(id, person_id, name, name_type, is_canonical, locale, script)"
        " VALUES ($1, $2, $3, 'legal', TRUE, 'en-US', 'Latn')",
        generate_id(), pid, "John Smith",
    )
    with pytest.raises(asyncpg.UniqueViolationError):
        await db.execute(
            "INSERT INTO person_names "
            "(id, person_id, name, name_type, is_canonical, locale, script)"
            " VALUES ($1, $2, $3, 'legal', TRUE, 'en-US', 'Latn')",
            generate_id(), pid, "Johnny Smith",
        )


async def test_canonical_unique_null_locale_collision(db):
    """COALESCE makes two canonical legal rows with NULL locale+script collide."""
    pid = await _person(db)
    await db.execute(
        "INSERT INTO person_names "
        "(id, person_id, name, name_type, is_canonical)"
        " VALUES ($1, $2, $3, 'legal', TRUE)",
        generate_id(), pid, "Cher",
    )
    with pytest.raises(asyncpg.UniqueViolationError):
        await db.execute(
            "INSERT INTO person_names "
            "(id, person_id, name, name_type, is_canonical)"
            " VALUES ($1, $2, $3, 'legal', TRUE)",
            generate_id(), pid, "Cher Bono",
        )


# --- Task 4: deadname → visibility consistency trigger ---

async def test_deadname_coerced_to_legal_only_on_insert(db):
    pid = await _person(db)
    nid = generate_id()
    await db.execute(
        "INSERT INTO person_names (id, person_id, name, name_type, visibility)"
        " VALUES ($1, $2, $3, 'deadname', 'public')",
        nid, pid, "Old Name",
    )
    row = await db.fetchrow("SELECT visibility FROM person_names WHERE id=$1", nid)
    assert row["visibility"] == "legal_only"


async def test_deadname_coerced_to_legal_only_on_update(db):
    pid = await _person(db)
    nid = generate_id()
    await db.execute(
        "INSERT INTO person_names (id, person_id, name, name_type, visibility)"
        " VALUES ($1, $2, $3, 'former', 'public')",
        nid, pid, "Old Name",
    )
    await db.execute(
        "UPDATE person_names SET name_type='deadname' WHERE id=$1", nid
    )
    row = await db.fetchrow("SELECT visibility FROM person_names WHERE id=$1", nid)
    assert row["visibility"] == "legal_only"


async def test_deadname_hidden_visibility_preserved(db):
    """Explicit 'hidden' is more restrictive than 'legal_only' — must not downgrade."""
    pid = await _person(db)
    nid = generate_id()
    await db.execute(
        "INSERT INTO person_names (id, person_id, name, name_type, visibility)"
        " VALUES ($1, $2, $3, 'deadname', 'hidden')",
        nid, pid, "Old Name",
    )
    row = await db.fetchrow("SELECT visibility FROM person_names WHERE id=$1", nid)
    assert row["visibility"] == "hidden"


async def test_non_deadname_public_unchanged(db):
    pid = await _person(db)
    nid = generate_id()
    await db.execute(
        "INSERT INTO person_names (id, person_id, name, name_type, visibility)"
        " VALUES ($1, $2, $3, 'former', 'public')",
        nid, pid, "Old Name",
    )
    row = await db.fetchrow("SELECT visibility FROM person_names WHERE id=$1", nid)
    assert row["visibility"] == "public"


# --- Task 5: visibility-aware v_person_display_names ---

async def test_view_excludes_legal_only(db):
    pid = await _person(db)
    await db.execute(
        "INSERT INTO person_names "
        "(id, person_id, name, name_type, is_canonical, visibility)"
        " VALUES ($1, $2, $3, 'legal', TRUE, 'legal_only')",
        generate_id(), pid, "Legal-Only Name",
    )
    row = await db.fetchrow(
        "SELECT display_name FROM v_person_display_names WHERE person_id=$1", pid
    )
    assert row["display_name"] is None


async def test_view_excludes_hidden(db):
    pid = await _person(db)
    await db.execute(
        "INSERT INTO person_names "
        "(id, person_id, name, name_type, is_canonical, visibility)"
        " VALUES ($1, $2, $3, 'legal', TRUE, 'hidden')",
        generate_id(), pid, "Hidden",
    )
    row = await db.fetchrow(
        "SELECT display_name FROM v_person_display_names WHERE person_id=$1", pid
    )
    assert row["display_name"] is None


async def test_view_excludes_internal(db):
    pid = await _person(db)
    await db.execute(
        "INSERT INTO person_names "
        "(id, person_id, name, name_type, is_canonical, visibility)"
        " VALUES ($1, $2, $3, 'legal', TRUE, 'internal')",
        generate_id(), pid, "Internal Only",
    )
    row = await db.fetchrow(
        "SELECT display_name FROM v_person_display_names WHERE person_id=$1", pid
    )
    assert row["display_name"] is None


async def test_view_returns_public_canonical(db):
    pid = await _person(db)
    await db.execute(
        "INSERT INTO person_names "
        "(id, person_id, name, name_type, is_canonical, visibility)"
        " VALUES ($1, $2, $3, 'legal', TRUE, 'public')",
        generate_id(), pid, "Public Name",
    )
    row = await db.fetchrow(
        "SELECT display_name FROM v_person_display_names WHERE person_id=$1", pid
    )
    assert row["display_name"] == "Public Name"


async def test_view_prefers_public_over_legal_only(db):
    """Person with both public and legal_only canonical: public wins."""
    pid = await _person(db)
    await db.execute(
        "INSERT INTO person_names "
        "(id, person_id, name, name_type, is_canonical, visibility, script)"
        " VALUES ($1, $2, $3, 'legal', TRUE, 'public', 'Latn')",
        generate_id(), pid, "Public",
    )
    await db.execute(
        "INSERT INTO person_names "
        "(id, person_id, name, name_type, is_canonical, visibility, script)"
        " VALUES ($1, $2, $3, 'former', TRUE, 'legal_only', 'Latn')",
        generate_id(), pid, "OldName",
    )
    row = await db.fetchrow(
        "SELECT display_name FROM v_person_display_names WHERE person_id=$1", pid
    )
    assert row["display_name"] == "Public"
