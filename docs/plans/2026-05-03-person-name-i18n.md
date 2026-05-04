# Person Name i18n Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Augment `person_names` with locale, script, sort_as, primary_identifier, visibility, reading_of_id, and structured-part columns; expand `name_type`; relax canonical uniqueness to `(person_id, name_type, locale, script)`; add deadname→visibility trigger; make `v_person_display_names` visibility-aware; add `visible_names_filter()` helper + lint test; document operational rules.

**Architecture:** Single-table additive change to `person_names` (no sidecar). Schema changes wrapped in idempotent `DO $$ ... IF NOT EXISTS ... END $$` blocks following the existing pattern. View + trigger created with `CREATE OR REPLACE`. Helper lives in `src/core/db.py`. No UI, no API model changes — Phase 1 only.

**Tech Stack:** PostgreSQL 15+, asyncpg, pytest (integration), ruff lint.

**Issue:** #121
**Worktree:** `.worktrees/feat/121-person-name-i18n` (already created)

**Reference files:**
- Design doc: `docs/plans/2026-05-03-person-name-i18n-design.md`
- Research: `docs/research/2026_05_03-gemini.google.com-designing_person_name_information_architecture.pdf`
- Schema: `src/core/schema.sql:126-157`
- Existing schema test pattern: `tests/core/test_schema.py`
- Idempotent migration pattern: `src/core/schema.sql:282-319` (archived_at blocks)

---

## File Map

**Modify:**
- `src/core/schema.sql` — column adds, CHECK swap, index swap, trigger fn + binding, view rewrite
- `src/core/db.py` — `visible_names_filter()` helper string + symbol export
- `docs/CONVENTIONS.md` — visibility rule, MRZ derivation rules, ICU collation guidance, no-auto-parse rule, structured-parts policy

**Create:**
- `tests/core/test_schema_person_names_i18n.py` — schema tests for each new structural element
- `tests/core/test_visible_names_filter.py` — helper unit test + lint test for direct `FROM person_names` access

---

## Pre-flight

- [ ] **Confirm worktree + clean baseline**

```bash
cd /home/exedev/power-map/.worktrees/feat/person-name-i18n
export $(cat /etc/power-map/.env | xargs) 2>/dev/null
export $(cat .env | xargs) 2>/dev/null
uv run pytest --no-cov -q tests/core/
```

Expected: baseline passes (record exact pass count for regression check at end of each task).

- [ ] **Confirm prod DB has zero rows that would conflict with new constraints**

```bash
psql "$DATABASE_URL" -c "
  SELECT name_type, count(*) FROM person_names GROUP BY 1;
  SELECT count(*) FROM person_names WHERE name_type NOT IN
    ('legal','preferred','alias','former','initials');
"
```

Expected: only existing values present; second query returns 0.

---

## Task 1: Add new optional columns to `person_names`

Adds `locale`, `script`, `sort_as`, `primary_identifier`, `visibility`, `reading_of_id`, `given_names`, `family_names`, `additional_names`, `honorific_prefix`, `honorific_suffix`. All nullable except `visibility` (default `'public'`).

**Files:**
- Modify: `src/core/schema.sql`
- Create: `tests/core/test_schema_person_names_i18n.py`

- [ ] **Step 1: Create the test file with failing column-existence tests**

```python
# tests/core/test_schema_person_names_i18n.py
"""Schema tests for person_names i18n / cultural-awareness columns."""

import os

import asyncpg
import pytest

from src.core.db import apply_schema, generate_id

pytestmark = pytest.mark.integration


@pytest.fixture
async def db():
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        pytest.skip("DATABASE_URL not set")
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
    assert row["data_type"].lower().startswith(data_type.lower()) or row["data_type"] == data_type


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
    pid = await _person(db)
    visual_id = generate_id()
    reading_id = generate_id()
    await db.execute(
        "INSERT INTO person_names (id, person_id, name, name_type, script)"
        " VALUES ($1, $2, $3, $4, $5)",
        visual_id, pid, "毛澤東", "legal", "Hant",
    )
    await db.execute(
        "INSERT INTO person_names (id, person_id, name, name_type, script, reading_of_id)"
        " VALUES ($1, $2, $3, $4, $5, $6)",
        reading_id, pid, "Máo Zédōng", "romanization", "Latn", visual_id,
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
```

- [ ] **Step 2: Run tests — verify they fail**

```bash
uv run pytest tests/core/test_schema_person_names_i18n.py -v --no-cov
```

Expected: FAIL — columns do not exist.

- [ ] **Step 3: Add columns to `src/core/schema.sql`**

Edit the `CREATE TABLE IF NOT EXISTS person_names` block (around line 135) to add the new columns inline (so fresh DBs get them):

```sql
CREATE TABLE IF NOT EXISTS person_names (
    id           TEXT        PRIMARY KEY,
    person_id    TEXT        NOT NULL REFERENCES people(id),
    name         TEXT        NOT NULL,
    name_type    TEXT        NOT NULL DEFAULT 'legal'
                             CHECK (name_type IN ('legal', 'former', 'preferred', 'alias', 'initials')),
    is_canonical BOOLEAN     NOT NULL DEFAULT FALSE,

    -- i18n / cultural-awareness metadata (Phase 1)
    locale              TEXT,                       -- BCP 47, e.g. 'en-US','zh-Hant-TW'
    script              TEXT,                       -- ISO 15924, e.g. 'Latn','Hant','Hans','Kana'
    sort_as             TEXT,                       -- explicit collation key; NULL → use `name`
    primary_identifier  TEXT
                        CHECK (primary_identifier IS NULL
                               OR primary_identifier IN ('family','given','patronymic','mononym')),
    visibility          TEXT NOT NULL DEFAULT 'public'
                        CHECK (visibility IN ('public','internal','legal_only','hidden')),
    reading_of_id       TEXT REFERENCES person_names(id),

    -- Structured parts (populated only when source provides them; never auto-parsed)
    given_names         TEXT[],
    family_names        TEXT[],
    additional_names    TEXT[],
    honorific_prefix    TEXT,
    honorific_suffix    TEXT,

    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
```

Then add an idempotent migration block immediately after the existing `archived_at` evolution blocks (near line 320, before the role/role_assignment alterations) — pattern `DO $$ BEGIN IF NOT EXISTS … END $$`:

```sql
-- =============================================================================
-- Schema evolution: person_names i18n columns (Phase 1)
-- =============================================================================

DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                   WHERE table_name='person_names' AND column_name='locale') THEN
        ALTER TABLE person_names ADD COLUMN locale TEXT;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                   WHERE table_name='person_names' AND column_name='script') THEN
        ALTER TABLE person_names ADD COLUMN script TEXT;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                   WHERE table_name='person_names' AND column_name='sort_as') THEN
        ALTER TABLE person_names ADD COLUMN sort_as TEXT;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                   WHERE table_name='person_names' AND column_name='primary_identifier') THEN
        ALTER TABLE person_names ADD COLUMN primary_identifier TEXT
            CHECK (primary_identifier IS NULL
                   OR primary_identifier IN ('family','given','patronymic','mononym'));
    END IF;
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                   WHERE table_name='person_names' AND column_name='visibility') THEN
        ALTER TABLE person_names ADD COLUMN visibility TEXT NOT NULL DEFAULT 'public'
            CHECK (visibility IN ('public','internal','legal_only','hidden'));
    END IF;
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                   WHERE table_name='person_names' AND column_name='reading_of_id') THEN
        ALTER TABLE person_names ADD COLUMN reading_of_id TEXT REFERENCES person_names(id);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                   WHERE table_name='person_names' AND column_name='given_names') THEN
        ALTER TABLE person_names ADD COLUMN given_names TEXT[];
    END IF;
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                   WHERE table_name='person_names' AND column_name='family_names') THEN
        ALTER TABLE person_names ADD COLUMN family_names TEXT[];
    END IF;
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                   WHERE table_name='person_names' AND column_name='additional_names') THEN
        ALTER TABLE person_names ADD COLUMN additional_names TEXT[];
    END IF;
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                   WHERE table_name='person_names' AND column_name='honorific_prefix') THEN
        ALTER TABLE person_names ADD COLUMN honorific_prefix TEXT;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                   WHERE table_name='person_names' AND column_name='honorific_suffix') THEN
        ALTER TABLE person_names ADD COLUMN honorific_suffix TEXT;
    END IF;
END $$;
```

- [ ] **Step 4: Run tests — verify they pass**

```bash
uv run pytest tests/core/test_schema_person_names_i18n.py -v --no-cov
```

Expected: PASS for all column / default / CHECK / FK / array tests.

- [ ] **Step 5: Confirm no regressions**

```bash
uv run pytest tests/core/ --no-cov -q
```

Expected: same baseline pass count + the new tests passing.

- [ ] **Step 6: Commit**

```bash
git add src/core/schema.sql tests/core/test_schema_person_names_i18n.py
git commit -m "feat: add i18n columns to person_names (locale, script, parts, visibility)"
```

---

## Task 2: Expand `name_type` CHECK constraint

Adds `deadname`, `mrz`, `reading`, `romanization`, `maiden`, `religious`, `stage` to the allowed values.

**Files:**
- Modify: `src/core/schema.sql`
- Modify: `tests/core/test_schema_person_names_i18n.py`

- [ ] **Step 1: Add failing test**

Append to `tests/core/test_schema_person_names_i18n.py`:

```python
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
```

- [ ] **Step 2: Run — confirm fail**

```bash
uv run pytest tests/core/test_schema_person_names_i18n.py::test_person_names_accepts_new_name_type -v --no-cov
```

Expected: FAIL — CHECK constraint rejects new values.

- [ ] **Step 3: Update the CHECK constraint in `schema.sql`**

In the `CREATE TABLE IF NOT EXISTS person_names` block, replace the inline CHECK on `name_type` with the expanded set:

```sql
    name_type    TEXT        NOT NULL DEFAULT 'legal'
                             CHECK (name_type IN (
                                 'legal','preferred','alias','former','initials',
                                 'maiden','religious','stage',
                                 'deadname',
                                 'reading','romanization','mrz'
                             )),
```

Then add an idempotent migration block in the i18n evolution section:

```sql
DO $$ BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.check_constraints
               WHERE constraint_name='person_names_name_type_check') THEN
        ALTER TABLE person_names DROP CONSTRAINT person_names_name_type_check;
    END IF;
    ALTER TABLE person_names ADD CONSTRAINT person_names_name_type_check CHECK (
        name_type IN (
            'legal','preferred','alias','former','initials',
            'maiden','religious','stage',
            'deadname',
            'reading','romanization','mrz'
        )
    );
END $$;
```

(Note: the auto-generated constraint name from the inline `CHECK` is `person_names_name_type_check`. Verify with `\d+ person_names` if uncertain.)

- [ ] **Step 4: Run tests — verify pass**

```bash
uv run pytest tests/core/test_schema_person_names_i18n.py -v --no-cov
```

Expected: PASS for all parametrized values, FAIL for unknown.

- [ ] **Step 5: Commit**

```bash
git add src/core/schema.sql tests/core/test_schema_person_names_i18n.py
git commit -m "feat: expand person_names.name_type to include deadname, mrz, reading, romanization, maiden, religious, stage"
```

---

## Task 3: Relax canonical-uniqueness index

Index keyed on `(person_id, name_type, COALESCE(locale,''), COALESCE(script,''))` so multiple canonical rows with different `(locale, script)` pairs coexist.

**Files:**
- Modify: `src/core/schema.sql`
- Modify: `tests/core/test_schema_person_names_i18n.py`

- [ ] **Step 1: Add failing tests**

Append:

```python
async def test_canonical_unique_per_locale_script(db):
    """Two canonical legal names with different scripts should coexist."""
    pid = await _person(db)
    await db.execute(
        "INSERT INTO person_names (id, person_id, name, name_type, is_canonical, script)"
        " VALUES ($1, $2, $3, 'legal', TRUE, 'Hant')",
        generate_id(), pid, "毛澤東",
    )
    # Same name_type, different script — must succeed
    await db.execute(
        "INSERT INTO person_names (id, person_id, name, name_type, is_canonical, script)"
        " VALUES ($1, $2, $3, 'legal', TRUE, 'Latn')",
        generate_id(), pid, "Mao Zedong",
    )


async def test_canonical_unique_collision(db):
    """Two canonical legal names with same (locale, script) collide."""
    pid = await _person(db)
    await db.execute(
        "INSERT INTO person_names (id, person_id, name, name_type, is_canonical, locale, script)"
        " VALUES ($1, $2, $3, 'legal', TRUE, 'en-US', 'Latn')",
        generate_id(), pid, "John Smith",
    )
    with pytest.raises(asyncpg.UniqueViolationError):
        await db.execute(
            "INSERT INTO person_names (id, person_id, name, name_type, is_canonical, locale, script)"
            " VALUES ($1, $2, $3, 'legal', TRUE, 'en-US', 'Latn')",
            generate_id(), pid, "Johnny Smith",
        )


async def test_canonical_unique_null_locale_collision(db):
    """Two canonical legal names with NULL locale + NULL script also collide (COALESCE)."""
    pid = await _person(db)
    await db.execute(
        "INSERT INTO person_names (id, person_id, name, name_type, is_canonical)"
        " VALUES ($1, $2, $3, 'legal', TRUE)",
        generate_id(), pid, "Cher",
    )
    with pytest.raises(asyncpg.UniqueViolationError):
        await db.execute(
            "INSERT INTO person_names (id, person_id, name, name_type, is_canonical)"
            " VALUES ($1, $2, $3, 'legal', TRUE)",
            generate_id(), pid, "Cher Bono",
        )
```

- [ ] **Step 2: Run — confirm fail**

```bash
uv run pytest tests/core/test_schema_person_names_i18n.py::test_canonical_unique_per_locale_script -v --no-cov
```

Expected: FAIL — current index forbids two canonical legal rows.

- [ ] **Step 3: Replace the index in `schema.sql`**

In place of (around line 145):

```sql
CREATE UNIQUE INDEX IF NOT EXISTS uq_person_canonical_name
    ON person_names(person_id, name_type)
    WHERE is_canonical = TRUE;
```

Use:

```sql
DROP INDEX IF EXISTS uq_person_canonical_name;
CREATE UNIQUE INDEX IF NOT EXISTS uq_person_canonical_name
    ON person_names(person_id, name_type, COALESCE(locale, ''), COALESCE(script, ''))
    WHERE is_canonical = TRUE;
```

The `DROP INDEX IF EXISTS` runs every `apply_schema()`, but it's a no-op once the new index exists (PostgreSQL won't drop the index since it has the same name and the `IF NOT EXISTS` recreates it identically). To make this fully idempotent and avoid the brief drop window on re-runs, wrap as:

```sql
DO $$ BEGIN
    IF EXISTS (
        SELECT 1 FROM pg_indexes
        WHERE indexname='uq_person_canonical_name'
          AND indexdef NOT LIKE '%COALESCE%'
    ) THEN
        DROP INDEX uq_person_canonical_name;
    END IF;
END $$;

CREATE UNIQUE INDEX IF NOT EXISTS uq_person_canonical_name
    ON person_names(person_id, name_type, COALESCE(locale, ''), COALESCE(script, ''))
    WHERE is_canonical = TRUE;
```

This drops the old index only if it lacks the `COALESCE` expression, then creates the new one.

- [ ] **Step 4: Run tests — verify pass**

```bash
uv run pytest tests/core/test_schema_person_names_i18n.py -v --no-cov
```

Expected: PASS — including the two collision tests.

- [ ] **Step 5: Commit**

```bash
git add src/core/schema.sql tests/core/test_schema_person_names_i18n.py
git commit -m "refactor: key canonical-name uniqueness on (locale, script) pair"
```

---

## Task 4: Deadname → visibility consistency trigger

Trigger coerces any `name_type='deadname'` row with `visibility='public'` to `visibility='legal_only'` on INSERT/UPDATE.

**Files:**
- Modify: `src/core/schema.sql`
- Modify: `tests/core/test_schema_person_names_i18n.py`

- [ ] **Step 1: Add failing tests**

Append:

```python
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
    """If user explicitly set 'hidden', trigger must NOT downgrade to 'legal_only'."""
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
```

- [ ] **Step 2: Run — confirm fail**

```bash
uv run pytest tests/core/test_schema_person_names_i18n.py::test_deadname_coerced_to_legal_only_on_insert -v --no-cov
```

Expected: FAIL — `visibility` stays `'public'`.

- [ ] **Step 3: Add trigger to `schema.sql`**

Insert near the other trigger definitions (after `set_updated_at` block, around line 475):

```sql
-- =============================================================================
-- Deadname → visibility consistency
-- A 'deadname' row can never be 'public'; coerce to 'legal_only' if so.
-- Explicit 'hidden' is preserved (more restrictive, intentional).
-- =============================================================================

CREATE OR REPLACE FUNCTION enforce_deadname_visibility()
RETURNS TRIGGER
LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.name_type = 'deadname' AND NEW.visibility = 'public' THEN
        NEW.visibility := 'legal_only';
    END IF;
    RETURN NEW;
END;
$$;

CREATE OR REPLACE TRIGGER trg_deadname_visibility
    BEFORE INSERT OR UPDATE ON person_names
    FOR EACH ROW EXECUTE FUNCTION enforce_deadname_visibility();
```

- [ ] **Step 4: Run tests — verify pass**

```bash
uv run pytest tests/core/test_schema_person_names_i18n.py -v --no-cov
```

Expected: PASS for all four trigger tests.

- [ ] **Step 5: Commit**

```bash
git add src/core/schema.sql tests/core/test_schema_person_names_i18n.py
git commit -m "feat: enforce deadname visibility never 'public' via BEFORE trigger"
```

---

## Task 5: Visibility-aware `v_person_display_names`

View filters `is_canonical = TRUE AND visibility = 'public'`.

**Files:**
- Modify: `src/core/schema.sql`
- Modify: `tests/core/test_schema_person_names_i18n.py`

- [ ] **Step 1: Add failing tests**

Append:

```python
async def test_view_excludes_legal_only(db):
    pid = await _person(db)
    await db.execute(
        "INSERT INTO person_names (id, person_id, name, name_type, is_canonical, visibility)"
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
        "INSERT INTO person_names (id, person_id, name, name_type, is_canonical, visibility)"
        " VALUES ($1, $2, $3, 'legal', TRUE, 'hidden')",
        generate_id(), pid, "Hidden",
    )
    row = await db.fetchrow(
        "SELECT display_name FROM v_person_display_names WHERE person_id=$1", pid
    )
    assert row["display_name"] is None


async def test_view_returns_public_canonical(db):
    pid = await _person(db)
    await db.execute(
        "INSERT INTO person_names (id, person_id, name, name_type, is_canonical, visibility)"
        " VALUES ($1, $2, $3, 'legal', TRUE, 'public')",
        generate_id(), pid, "Public Name",
    )
    row = await db.fetchrow(
        "SELECT display_name FROM v_person_display_names WHERE person_id=$1", pid
    )
    assert row["display_name"] == "Public Name"


async def test_view_prefers_public_over_legal_only(db):
    """Person has both: public canonical wins."""
    pid = await _person(db)
    await db.execute(
        "INSERT INTO person_names (id, person_id, name, name_type, is_canonical, visibility, script)"
        " VALUES ($1, $2, $3, 'legal', TRUE, 'public', 'Latn')",
        generate_id(), pid, "Public",
    )
    await db.execute(
        "INSERT INTO person_names (id, person_id, name, name_type, is_canonical, visibility, script)"
        " VALUES ($1, $2, $3, 'former', TRUE, 'legal_only', 'Latn')",
        generate_id(), pid, "OldName",
    )
    row = await db.fetchrow(
        "SELECT display_name FROM v_person_display_names WHERE person_id=$1", pid
    )
    assert row["display_name"] == "Public"
```

- [ ] **Step 2: Run — confirm fail**

```bash
uv run pytest tests/core/test_schema_person_names_i18n.py::test_view_excludes_legal_only -v --no-cov
```

Expected: FAIL — view returns the row regardless of visibility.

- [ ] **Step 3: Update the view in `schema.sql`**

Replace the existing `CREATE OR REPLACE VIEW v_person_display_names` (around line 151):

```sql
CREATE OR REPLACE VIEW v_person_display_names AS
SELECT p.id AS person_id,
       n.name AS display_name
FROM people p
LEFT JOIN person_names n
    ON n.person_id = p.id
   AND n.is_canonical = TRUE
   AND n.visibility = 'public';
```

- [ ] **Step 4: Run tests — verify pass**

```bash
uv run pytest tests/core/test_schema_person_names_i18n.py -v --no-cov
```

Expected: PASS for all four view tests.

- [ ] **Step 5: Confirm no regressions in admin / public / view tests**

```bash
uv run pytest tests/core/ tests/api/ --no-cov -q
```

Expected: same baseline pass count + new tests passing. Any failures here indicate a downstream caller relied on `v_person_display_names` returning a non-public canonical row — investigate before continuing.

- [ ] **Step 6: Commit**

```bash
git add src/core/schema.sql tests/core/test_schema_person_names_i18n.py
git commit -m "refactor: filter v_person_display_names to visibility='public'"
```

---

## Task 6: `visible_names_filter()` helper

Helper string for queries that need raw `FROM person_names` access — search, dup-detection, etc. Returns the SQL fragment to AND-append.

**Files:**
- Modify: `src/core/db.py`
- Create: `tests/core/test_visible_names_filter.py`

- [ ] **Step 1: Create test file**

```python
# tests/core/test_visible_names_filter.py
"""Unit tests for visible_names_filter helper + lint test for direct person_names access."""

import os
import re
from pathlib import Path

import pytest

from src.core.db import visible_names_filter


def test_visible_names_filter_returns_sql_fragment():
    s = visible_names_filter()
    assert isinstance(s, str)
    assert s.strip() != ""
    assert "visibility" in s
    assert "'public'" in s


def test_visible_names_filter_uses_alias():
    """When called with alias, fragment qualifies the column."""
    s = visible_names_filter(alias="pn")
    assert "pn.visibility" in s


# --- Lint test: forbid raw `FROM person_names` outside the helper ---

ALLOWED_DIRECT_ACCESS = {
    # Files explicitly permitted to query person_names without the helper.
    # Each entry is justified in a comment in the file itself.
    "src/core/db.py",                                      # defines the helper
    "src/api/admin/people_dups.py",                        # dup-detection — uses helper
    "src/api/admin/people.py",                             # admin detail — disclosure-gated
}

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_no_unguarded_person_names_queries():
    """No raw `FROM person_names` outside the allow-list (visibility-rule enforcement)."""
    pattern = re.compile(r"\bFROM\s+person_names\b", re.IGNORECASE)
    offenders = []
    for path in (REPO_ROOT / "src").rglob("*.py"):
        rel = path.relative_to(REPO_ROOT).as_posix()
        if rel in ALLOWED_DIRECT_ACCESS:
            continue
        text = path.read_text()
        if pattern.search(text):
            offenders.append(rel)
    assert not offenders, (
        f"Direct `FROM person_names` access in {offenders}. "
        f"Either go through v_person_display_names, use visible_names_filter(), "
        f"or add the file to ALLOWED_DIRECT_ACCESS with a justification comment."
    )
```

- [ ] **Step 2: Run — confirm fail**

```bash
uv run pytest tests/core/test_visible_names_filter.py -v --no-cov
```

Expected: FAIL — `visible_names_filter` not defined; ImportError.

- [ ] **Step 3: Implement helper in `src/core/db.py`**

Add near the other public utilities:

```python
def visible_names_filter(alias: str | None = None) -> str:
    """SQL fragment to AND-append when querying person_names directly.

    Excludes deadnames and any row marked legal_only / hidden / internal,
    matching the visibility rule documented in docs/CONVENTIONS.md.

    Args:
        alias: Optional table alias used in the query (e.g. 'pn' for
               `FROM person_names pn`). When None, the column is unqualified.
    """
    col = f"{alias}.visibility" if alias else "visibility"
    return f"{col} = 'public'"
```

- [ ] **Step 4: Audit existing direct queries**

```bash
grep -rn "FROM person_names" src/
```

For each hit not in `ALLOWED_DIRECT_ACCESS`, either:

1. Switch to `v_person_display_names` (preferred — already filters), **OR**
2. Append `AND <visible_names_filter()>` (when raw access is needed for non-display columns), **OR**
3. Add the file to `ALLOWED_DIRECT_ACCESS` with a `# visibility-allowlist: <reason>` comment in that file (e.g. `people_dups.py` builds dup candidates and explicitly excludes hidden via the helper; `people.py` admin detail surfaces all names behind a disclosure toggle).

This audit IS the work of this task. Do not blindly add files to the allow-list.

- [ ] **Step 5: Run tests — verify pass**

```bash
uv run pytest tests/core/test_visible_names_filter.py -v --no-cov
```

Expected: PASS — including the lint test (allow-list is the post-audit set).

- [ ] **Step 6: Run full test suite**

```bash
uv run pytest --no-cov -q
```

Expected: baseline pass count maintained. Investigate any regressions.

- [ ] **Step 7: Commit**

```bash
git add src/core/db.py tests/core/test_visible_names_filter.py src/api/
git commit -m "feat: add visible_names_filter helper + lint test for person_names access"
```

(Include any caller-site changes from the Step 4 audit in this commit.)

---

## Task 7: Document operational rules in `docs/CONVENTIONS.md`

Codifies: visibility rule, MRZ derivation, ICU collation, no-auto-parse, structured-parts policy.

**Files:**
- Modify: `docs/CONVENTIONS.md`

- [ ] **Step 1: Add a "Person Names" section**

Insert (location: alphabetically near other DB-rule sections; if none exists, add a top-level `## Person Names` section):

```markdown
## Person Names

### Storage rules

- Store user input verbatim. **Never** lowercase, title-case, ASCII-fold, or strip diacritics on input.
- `name` (UTF-8 free string) is the canonical display form. Structured parts (`given_names`, `family_names`, etc.) are populated only when an upstream source explicitly provides them. **Never auto-parse** a free string into parts.
- Sort with Postgres ICU collations (e.g. `ORDER BY name COLLATE "und-x-icu"`), or by `sort_as` when present. Do not use `LOWER(name)` for sorting.
- New rows default to `visibility='public'`. The deadname trigger downgrades any `name_type='deadname'` row from `'public'` to `'legal_only'` automatically.

### Visibility rule

A `person_names` row with `visibility ∈ {'legal_only', 'hidden', 'internal'}` is excluded from:

- `v_person_display_names`
- All public API responses
- All admin search results, list pages, autocomplete, typeahead
- All duplicate-detection candidate sets
- All flash messages and activity logs

It surfaces **only** on the person-detail admin page, behind an explicit "Show legal/historical names" disclosure toggle (default collapsed).

Enforcement:

- `v_person_display_names` filters by `visibility='public'`.
- For raw `FROM person_names` queries, AND-append `visible_names_filter()` from `src.core.db`.
- The lint test `tests/core/test_visible_names_filter.py::test_no_unguarded_person_names_queries` greps for direct access outside the explicit allow-list.

### MRZ form

ICAO 9303 Machine-Readable Zone form is stored as a `name_type='mrz'` row linked via `reading_of_id` to its visual original. Derivation rules (apply when generating an MRZ row from a Latin-script visual name):

| Transformation | Example |
|---|---|
| Uppercase all letters | `José` → `JOSE` |
| Strip diacritics (NFKD + ASCII-only) | `García` → `GARCIA` |
| Replace hyphens with single space | `García-López` → `GARCIA LOPEZ` |
| Drop apostrophes | `O'Brien` → `OBRIEN` |
| Use `<` as filler / separator | `GARCIA<LOPEZ<<JOSE` |

No automatic generation pipeline ships with Phase 1 — populate manually or via a future ingestion integration.

### Structured parts

`given_names`, `family_names`, `additional_names` are PostgreSQL `TEXT[]` and ordered. `primary_identifier` indicates which array drives formal address and primary sort:

- `'family'` — Western, Sinitic, Hungarian (last-name address); sort by `family_names[1]`.
- `'given'` — Icelandic, mononymous fallback; sort by `given_names[1]`.
- `'patronymic'` — Arabic chain, Russian; address by `given_names[1]`.
- `'mononym'` — single-name people (Cher, Prince); the single token is in `name`.

A row with NULL parts but a populated `name` is fully valid — the free string remains authoritative.
```

- [ ] **Step 2: Verify rendered Markdown**

```bash
ls docs/CONVENTIONS.md && head -20 docs/CONVENTIONS.md
```

Sanity-check that the section was inserted in a sensible place; reorder headers if needed.

- [ ] **Step 3: Commit**

```bash
git add docs/CONVENTIONS.md
git commit -m "docs: codify person-name visibility rule, MRZ derivation, ICU sort, no-auto-parse"
```

---

## Task 8: Apply schema to dev DB + production rehearsal

**Files:** none modified — operational task.

- [ ] **Step 1: Apply schema to the worktree's test DB**

```bash
export $(cat /etc/power-map/.env | xargs) 2>/dev/null
export $(cat .env | xargs) 2>/dev/null
uv run python -c "
import asyncio, asyncpg, os
from src.core.db import apply_schema
async def main():
    c = await asyncpg.connect(os.environ['TEST_DATABASE_URL'])
    await apply_schema(c)
    print('TEST DB schema applied')
asyncio.run(main())
"
```

Expected: no errors. Re-run to confirm idempotency.

- [ ] **Step 2: Snapshot prod DB schema (read-only)**

```bash
psql "$DATABASE_URL" -c "\d person_names" > /tmp/person_names_before.txt
psql "$DATABASE_URL" -c "SELECT count(*), name_type FROM person_names GROUP BY 2;" > /tmp/person_names_counts_before.txt
cat /tmp/person_names_before.txt /tmp/person_names_counts_before.txt
```

- [ ] **Step 3: Apply schema to production DB**

```bash
uv run python -c "
import asyncio, asyncpg, os
from src.core.db import apply_schema
async def main():
    c = await asyncpg.connect(os.environ['DATABASE_URL'])
    await apply_schema(c)
    print('PROD DB schema applied')
asyncio.run(main())
"
```

- [ ] **Step 4: Verify post-state**

```bash
psql "$DATABASE_URL" -c "\d person_names" > /tmp/person_names_after.txt
diff /tmp/person_names_before.txt /tmp/person_names_after.txt | head -80
psql "$DATABASE_URL" -c "SELECT count(*), name_type FROM person_names GROUP BY 2;" > /tmp/person_names_counts_after.txt
diff /tmp/person_names_counts_before.txt /tmp/person_names_counts_after.txt
```

Expected: column diff shows the 11 new columns + new constraints; row counts identical (0 rows changed).

- [ ] **Step 5: Restart production service**

```bash
sudo systemctl restart power-map
sudo systemctl status power-map --no-pager | head -10
curl -s -o /dev/null -w "Prod API: %{http_code}\n" http://localhost:8000/admin/
```

Expected: service healthy, admin index returns 307 (redirect to login) as before.

- [ ] **Step 6: Final regression sweep**

```bash
uv run pytest --no-cov -q
uv run ruff check src/ tests/
```

Expected: full pass; ruff clean.

---

## Done Criteria

- [ ] All 8 tasks committed on `feat/person-name-i18n`.
- [ ] `tests/core/test_schema_person_names_i18n.py` and `tests/core/test_visible_names_filter.py` pass against `TEST_DATABASE_URL`.
- [ ] Full test suite at baseline pass count.
- [ ] Production DB shows the 11 new columns + new constraints; no row data changed.
- [ ] Production service restarted and healthy.
- [ ] `docs/CONVENTIONS.md` documents the visibility rule, MRZ derivation, and no-auto-parse policy.

## Out of Scope (Phase 2+)

- Admin UI for editing locale/script/visibility/parts.
- Public API additive fields on `PersonName` Pydantic model.
- `Accept-Language` content negotiation in API responses.
- Backfill of `locale='en'` / `script='Latn'` on existing rows.
- Per-source ingestion logic populating structured parts.
- vCard 4.0 export endpoint.
- ICAO MRZ auto-generation pipeline.
