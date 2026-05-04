# Phase 2-prep — `bcp47_locales` + `iso15924_scripts` Lookup Tables

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Issue:** #123 (Phase 2-prep sub-task)
**Worktree:** `.worktrees/feat/123-phase-2-admin-ui`
**Design:** `docs/plans/2026-05-04-phase-2-person-name-ui-design.md` (§ Phase 2-prep)

**Goal:** Add two reference tables (`bcp47_locales`, `iso15924_scripts`) seeded from `langcodes` + `pycountry`, plus FKs from `person_names.locale` and `person_names.script`. Establishes DB-level validation for the columns that Phase 2b will surface in the admin UI. Library deps live in a `seed` dep group only — no runtime imports.

**Architecture:** Schema-first. Tables created idempotently via `CREATE TABLE IF NOT EXISTS`. Seed script (`scripts/seed_locales_scripts.py`) populates rows via `INSERT ... ON CONFLICT DO UPDATE`. FKs added to `person_names` after seed completes (existing rows are all NULL for `locale` / `script`, so the constraint binds without rewrites). The seed step runs once per environment after schema apply.

**Tech Stack:** PostgreSQL 15+, asyncpg, `langcodes` ≥3.5, `pycountry` ≥24, pytest (unit + integration).

---

## File Map

**Modify:**
- `src/core/schema.sql` — add `bcp47_locales`, `iso15924_scripts` tables + FK migration blocks.
- `pyproject.toml` — add `[dependency-groups.seed]`.
- `docs/CONVENTIONS.md` — describe the validation layering.

**Create:**
- `scripts/seed_locales_scripts.py` — generates and upserts rows from `langcodes` + `pycountry`.
- `tests/scripts/test_seed_locales_scripts.py` — unit tests over enumeration helpers.
- `tests/core/test_schema_locale_script_lookups.py` — integration tests for FK enforcement, idempotent re-seed, `is_common` flag.

---

## Pre-flight

- [ ] **Confirm baseline pass count**

```bash
cd /home/exedev/power-map/.worktrees/feat/123-phase-2-admin-ui
export $(cat /etc/power-map/.env | xargs) 2>/dev/null
export $(cat .env | xargs) 2>/dev/null
uv run pytest --no-cov -q tests/core/
```

Record baseline.

- [ ] **Confirm prod has no person_names rows with non-NULL `locale` or `script`**

```bash
psql "$DATABASE_URL" -tAc "SELECT count(*) FROM person_names WHERE locale IS NOT NULL OR script IS NOT NULL"
```

Expected: `0`. If non-zero (shouldn't be — Phase 1 left them NULL), the FK migration will need a backfill step. Stop and reassess.

---

## Task 1: Add lookup table schemas (no FKs yet)

Tables alone, with their own indexes and constraints. FKs come in Task 3 after the tables are populated.

**Files:**
- Modify: `src/core/schema.sql`
- Create: `tests/core/test_schema_locale_script_lookups.py`

- [ ] **Step 1: Failing tests for table existence + columns**

```python
# tests/core/test_schema_locale_script_lookups.py
"""Phase 2-prep: bcp47_locales + iso15924_scripts lookup tables."""

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


async def test_bcp47_locales_table_exists(db):
    row = await db.fetchrow(
        "SELECT 1 FROM information_schema.tables WHERE table_name='bcp47_locales'"
    )
    assert row is not None


@pytest.mark.parametrize(
    "column,data_type",
    [
        ("code", "text"),
        ("language", "text"),
        ("script", "text"),
        ("region", "text"),
        ("display_name", "text"),
        ("is_common", "boolean"),
    ],
)
async def test_bcp47_locales_has_column(db, column, data_type):
    row = await db.fetchrow(
        "SELECT data_type FROM information_schema.columns "
        "WHERE table_name='bcp47_locales' AND column_name=$1",
        column,
    )
    assert row is not None, f"bcp47_locales.{column} missing"
    assert row["data_type"] == data_type


async def test_iso15924_scripts_table_exists(db):
    row = await db.fetchrow(
        "SELECT 1 FROM information_schema.tables WHERE table_name='iso15924_scripts'"
    )
    assert row is not None


@pytest.mark.parametrize(
    "column,data_type",
    [
        ("code", "text"),
        ("numeric_code", "smallint"),
        ("name", "text"),
        ("is_common", "boolean"),
    ],
)
async def test_iso15924_scripts_has_column(db, column, data_type):
    row = await db.fetchrow(
        "SELECT data_type FROM information_schema.columns "
        "WHERE table_name='iso15924_scripts' AND column_name=$1",
        column,
    )
    assert row is not None
    assert row["data_type"] == data_type


async def test_iso15924_scripts_numeric_code_unique(db):
    await db.execute(
        "INSERT INTO iso15924_scripts (code, numeric_code, name) "
        "VALUES ('Aaaa', 999, 'Test')"
    )
    with pytest.raises(asyncpg.UniqueViolationError):
        await db.execute(
            "INSERT INTO iso15924_scripts (code, numeric_code, name) "
            "VALUES ('Bbbb', 999, 'Other')"
        )


async def test_is_common_default_false(db):
    await db.execute(
        "INSERT INTO bcp47_locales (code, language, display_name) "
        "VALUES ('xx', 'xx', 'Test')"
    )
    row = await db.fetchrow(
        "SELECT is_common FROM bcp47_locales WHERE code='xx'"
    )
    assert row["is_common"] is False
```

- [ ] **Step 2: Run — confirm fail.**

- [ ] **Step 3: Add the tables to `src/core/schema.sql`**

Locate the "Lookup / Reference Tables" section near the top of the file (where `link_types` and `entity_identifier_types` live). Append after `entity_identifier_types`:

```sql
-- =============================================================================
-- BCP 47 / ISO 15924 lookup tables (issue #123, Phase 2-prep)
-- Seeded by scripts/seed_locales_scripts.py from langcodes + pycountry.
-- Validation source for person_names.locale and person_names.script.
-- =============================================================================

CREATE TABLE IF NOT EXISTS bcp47_locales (
    code         TEXT        PRIMARY KEY,
    language     TEXT        NOT NULL,
    script       TEXT,
    region       TEXT,
    display_name TEXT        NOT NULL,
    is_common    BOOLEAN     NOT NULL DEFAULT FALSE,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_bcp47_locales_is_common
    ON bcp47_locales(is_common) WHERE is_common = TRUE;

CREATE TABLE IF NOT EXISTS iso15924_scripts (
    code         TEXT        PRIMARY KEY,
    numeric_code SMALLINT    NOT NULL UNIQUE,
    name         TEXT        NOT NULL,
    is_common    BOOLEAN     NOT NULL DEFAULT FALSE,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_iso15924_scripts_is_common
    ON iso15924_scripts(is_common) WHERE is_common = TRUE;
```

In the `updated_at` trigger section near the bottom, add:

```sql
CREATE OR REPLACE TRIGGER trg_updated_at_bcp47_locales
    BEFORE UPDATE ON bcp47_locales
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

CREATE OR REPLACE TRIGGER trg_updated_at_iso15924_scripts
    BEFORE UPDATE ON iso15924_scripts
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();
```

- [ ] **Step 4: Run — verify pass.**

- [ ] **Step 5: Commit**

```bash
git add src/core/schema.sql tests/core/test_schema_locale_script_lookups.py
git commit -m "#123 feat: add bcp47_locales + iso15924_scripts lookup tables (Phase 2-prep)"
```

---

## Task 2: Seed script + dep group

**Files:**
- Modify: `pyproject.toml`
- Create: `scripts/seed_locales_scripts.py`
- Create: `tests/scripts/test_seed_locales_scripts.py`

- [ ] **Step 1: Add `seed` dep group to `pyproject.toml`**

```toml
[dependency-groups]
dev = [
    "anyio>=4.12.1",
    "pre-commit>=4.5.1",
    "pytest>=8.0",
    "pytest-asyncio>=1.3.0",
    "pytest-cov>=6.0",
    "ruff>=0.9",
]
seed = [
    "langcodes>=3.5",
    "pycountry>=24.0",
]
```

- [ ] **Step 2: Failing unit tests for the enumeration helpers**

```python
# tests/scripts/test_seed_locales_scripts.py
"""Unit tests for the locale/script seed helpers (no DB)."""

import pytest

# These imports fail until the seed script exists.
from scripts.seed_locales_scripts import (
    enumerate_bcp47_locales,
    enumerate_iso15924_scripts,
    IS_COMMON_LOCALES,
    IS_COMMON_SCRIPTS,
)


def test_enumerate_bcp47_locales_yields_dict_records():
    rows = list(enumerate_bcp47_locales())
    assert len(rows) > 1000, "expected ~7000 CLDR locales, got far fewer"
    sample = rows[0]
    assert {"code", "language", "script", "region", "display_name", "is_common"} <= set(sample)


def test_enumerate_bcp47_locales_sets_is_common_flag():
    rows = list(enumerate_bcp47_locales())
    by_code = {r["code"]: r for r in rows}
    for code in IS_COMMON_LOCALES:
        assert code in by_code, f"{code} listed in IS_COMMON_LOCALES but absent from CLDR enumeration"
        assert by_code[code]["is_common"] is True


def test_enumerate_iso15924_scripts_full_set():
    rows = list(enumerate_iso15924_scripts())
    assert len(rows) >= 180, "expected ~200 ISO 15924 codes"
    sample = rows[0]
    assert {"code", "numeric_code", "name", "is_common"} <= set(sample)


def test_enumerate_iso15924_scripts_marks_common():
    rows = list(enumerate_iso15924_scripts())
    by_code = {r["code"]: r for r in rows}
    for code in IS_COMMON_SCRIPTS:
        assert code in by_code, f"{code} not in pycountry.scripts"
        assert by_code[code]["is_common"] is True


def test_iso15924_numeric_codes_unique():
    rows = list(enumerate_iso15924_scripts())
    codes = [r["numeric_code"] for r in rows]
    assert len(codes) == len(set(codes)), "duplicate numeric_code in seed enumeration"


def test_bcp47_locale_codes_unique():
    rows = list(enumerate_bcp47_locales())
    codes = [r["code"] for r in rows]
    assert len(codes) == len(set(codes)), "duplicate code in seed enumeration"
```

- [ ] **Step 3: Run — confirm ImportError fail.**

```bash
uv run --group seed pytest tests/scripts/test_seed_locales_scripts.py --no-cov -q
```

- [ ] **Step 4: Implement `scripts/seed_locales_scripts.py`**

```python
"""Seed bcp47_locales + iso15924_scripts from langcodes + pycountry.

Idempotent — re-runs upsert into existing rows. Run once per environment
after schema apply:

    uv run --group seed scripts/seed_locales_scripts.py

Library deps live in the `seed` dep group; this script is the only place
they are imported.
"""

import asyncio
import os
from typing import Iterable

import asyncpg
import langcodes
import pycountry


# Curated initial typeahead default-set. Editable later via SQL or admin UI.
IS_COMMON_LOCALES = frozenset({
    "en-US", "en-GB", "en-CA", "en-AU",
    "es-ES", "es-MX", "es-AR",
    "pt-BR", "pt-PT",
    "fr-FR", "fr-CA",
    "de-DE", "de-AT", "de-CH",
    "it-IT", "nl-NL", "nl-BE",
    "sv-SE", "no-NO", "da-DK", "fi-FI", "is-IS",
    "ru-RU", "uk-UA", "pl-PL",
    "zh-Hans-CN", "zh-Hant-TW", "zh-Hant-HK",
    "ja-JP", "ko-KR",
    "ar-SA", "ar-EG", "he-IL",
    "tr-TR", "el-GR", "cs-CZ", "ro-RO", "hu-HU",
    "vi-VN", "th-TH", "id-ID", "ms-MY",
    "hi-IN", "bn-IN", "ta-IN", "ur-PK",
})

IS_COMMON_SCRIPTS = frozenset({
    "Latn", "Hans", "Hant", "Hira", "Kana", "Jpan", "Hang", "Hani",
    "Cyrl", "Arab", "Hebr", "Grek",
    "Deva", "Beng", "Taml", "Telu", "Knda", "Mlym", "Guru", "Gujr",
    "Thai", "Laoo", "Mymr", "Khmr",
    "Ethi", "Geor", "Armn",
})


def enumerate_bcp47_locales() -> Iterable[dict]:
    """Yield one record per CLDR locale.

    Each record: {code, language, script, region, display_name, is_common}.
    Codes are normalised by langcodes (e.g. 'en-us' → 'en-US').
    """
    seen: set[str] = set()
    for code in langcodes.LANGUAGE_DATA.list_distinct_locales():
        try:
            tag = langcodes.Language.get(code).maximize().simplify_script()
        except Exception:
            continue
        normalised = str(tag)
        if normalised in seen:
            continue
        seen.add(normalised)
        yield {
            "code": normalised,
            "language": tag.language or "",
            "script": tag.script,
            "region": tag.territory,
            "display_name": tag.display_name(),
            "is_common": normalised in IS_COMMON_LOCALES,
        }


def enumerate_iso15924_scripts() -> Iterable[dict]:
    """Yield one record per ISO 15924 script via pycountry."""
    for s in pycountry.scripts:
        yield {
            "code": s.alpha_4,
            "numeric_code": int(s.numeric),
            "name": s.name,
            "is_common": s.alpha_4 in IS_COMMON_SCRIPTS,
        }


async def upsert_locales(conn: asyncpg.Connection, rows: Iterable[dict]) -> int:
    count = 0
    for r in rows:
        await conn.execute(
            """
            INSERT INTO bcp47_locales (code, language, script, region, display_name, is_common)
            VALUES ($1, $2, $3, $4, $5, $6)
            ON CONFLICT (code) DO UPDATE SET
                language     = EXCLUDED.language,
                script       = EXCLUDED.script,
                region       = EXCLUDED.region,
                display_name = EXCLUDED.display_name,
                is_common    = EXCLUDED.is_common
            """,
            r["code"], r["language"], r["script"], r["region"],
            r["display_name"], r["is_common"],
        )
        count += 1
    return count


async def upsert_scripts(conn: asyncpg.Connection, rows: Iterable[dict]) -> int:
    count = 0
    for r in rows:
        await conn.execute(
            """
            INSERT INTO iso15924_scripts (code, numeric_code, name, is_common)
            VALUES ($1, $2, $3, $4)
            ON CONFLICT (code) DO UPDATE SET
                numeric_code = EXCLUDED.numeric_code,
                name         = EXCLUDED.name,
                is_common    = EXCLUDED.is_common
            """,
            r["code"], r["numeric_code"], r["name"], r["is_common"],
        )
        count += 1
    return count


async def main() -> None:
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        raise SystemExit("DATABASE_URL not set")
    conn = await asyncpg.connect(dsn)
    try:
        n_loc = await upsert_locales(conn, enumerate_bcp47_locales())
        n_scr = await upsert_scripts(conn, enumerate_iso15924_scripts())
        print(f"seeded: {n_loc} locales, {n_scr} scripts")
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
```

- [ ] **Step 5: Run unit tests — verify pass**

```bash
uv run --group seed pytest tests/scripts/test_seed_locales_scripts.py --no-cov -q
```

- [ ] **Step 6: End-to-end seed against `TEST_DATABASE_URL`**

```bash
DATABASE_URL="$TEST_DATABASE_URL" uv run --group seed scripts/seed_locales_scripts.py
```

Expect "seeded: NNNN locales, NNN scripts". Verify counts:

```bash
psql "$TEST_DATABASE_URL" -c "SELECT count(*) FROM bcp47_locales"
psql "$TEST_DATABASE_URL" -c "SELECT count(*) FROM iso15924_scripts"
psql "$TEST_DATABASE_URL" -c "SELECT count(*) FROM bcp47_locales WHERE is_common"
psql "$TEST_DATABASE_URL" -c "SELECT count(*) FROM iso15924_scripts WHERE is_common"
```

- [ ] **Step 7: Re-run script — confirm idempotent (second run is a no-op for unchanged rows)**

```bash
DATABASE_URL="$TEST_DATABASE_URL" uv run --group seed scripts/seed_locales_scripts.py
```

Same counts, no errors.

- [ ] **Step 8: Confirm runtime deps still don't include `langcodes` / `pycountry`**

```bash
uv run python -c "import langcodes" 2>&1 | grep -q "ModuleNotFoundError" \
  && echo OK \
  || echo FAIL
```

- [ ] **Step 9: Commit**

```bash
git add pyproject.toml uv.lock scripts/seed_locales_scripts.py \
        tests/scripts/test_seed_locales_scripts.py
git commit -m "#123 feat: seed_locales_scripts.py — populates lookup tables from langcodes + pycountry"
```

---

## Task 3: Add FK constraints from `person_names`

Now that the lookup tables are populated, bind `person_names.locale` and `person_names.script` to them.

**Files:**
- Modify: `src/core/schema.sql`
- Modify: `tests/core/test_schema_locale_script_lookups.py`

- [ ] **Step 1: Failing tests for FK enforcement**

Append:

```python
async def _person(conn) -> str:
    pid = generate_id()
    await conn.execute("INSERT INTO people (id) VALUES ($1)", pid)
    return pid


async def test_person_names_locale_fk_rejects_unregistered(db):
    pid = await _person(db)
    # Seed the test DB's lookup tables with at least one row to ensure
    # the FK constraint is enforced.
    await db.execute(
        "INSERT INTO bcp47_locales (code, language, display_name) "
        "VALUES ('en-US', 'en', 'English (US)') ON CONFLICT DO NOTHING"
    )
    with pytest.raises(asyncpg.ForeignKeyViolationError):
        await db.execute(
            "INSERT INTO person_names (id, person_id, name, locale) "
            "VALUES ($1, $2, $3, 'xx-XX')",
            generate_id(), pid, "Test",
        )


async def test_person_names_locale_fk_accepts_registered(db):
    pid = await _person(db)
    await db.execute(
        "INSERT INTO bcp47_locales (code, language, display_name) "
        "VALUES ('en-US', 'en', 'English (US)') ON CONFLICT DO NOTHING"
    )
    await db.execute(
        "INSERT INTO person_names (id, person_id, name, locale) "
        "VALUES ($1, $2, $3, 'en-US')",
        generate_id(), pid, "Test",
    )


async def test_person_names_script_fk_rejects_unregistered(db):
    pid = await _person(db)
    await db.execute(
        "INSERT INTO iso15924_scripts (code, numeric_code, name) "
        "VALUES ('Latn', 215, 'Latin') ON CONFLICT DO NOTHING"
    )
    with pytest.raises(asyncpg.ForeignKeyViolationError):
        await db.execute(
            "INSERT INTO person_names (id, person_id, name, script) "
            "VALUES ($1, $2, $3, 'Xxxx')",
            generate_id(), pid, "Test",
        )


async def test_person_names_script_fk_accepts_registered(db):
    pid = await _person(db)
    await db.execute(
        "INSERT INTO iso15924_scripts (code, numeric_code, name) "
        "VALUES ('Latn', 215, 'Latin') ON CONFLICT DO NOTHING"
    )
    await db.execute(
        "INSERT INTO person_names (id, person_id, name, script) "
        "VALUES ($1, $2, $3, 'Latn')",
        generate_id(), pid, "Test",
    )


async def test_person_names_locale_null_still_allowed(db):
    pid = await _person(db)
    await db.execute(
        "INSERT INTO person_names (id, person_id, name) VALUES ($1, $2, $3)",
        generate_id(), pid, "Test",
    )
```

- [ ] **Step 2: Run — confirm fail.**

- [ ] **Step 3: Add idempotent FK migration to `schema.sql`**

In the `Schema evolution: person_names i18n columns` section, append:

```sql
-- Phase 2-prep (#123): bind person_names.locale → bcp47_locales(code).
DO $$ BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint c
        JOIN pg_class t ON t.oid = c.conrelid
        WHERE t.relname = 'person_names'
          AND c.conname = 'person_names_locale_fkey'
    ) THEN
        ALTER TABLE person_names
            ADD CONSTRAINT person_names_locale_fkey
            FOREIGN KEY (locale) REFERENCES bcp47_locales(code) ON UPDATE CASCADE;
    END IF;
END $$;

-- Phase 2-prep (#123): bind person_names.script → iso15924_scripts(code).
DO $$ BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint c
        JOIN pg_class t ON t.oid = c.conrelid
        WHERE t.relname = 'person_names'
          AND c.conname = 'person_names_script_fkey'
    ) THEN
        ALTER TABLE person_names
            ADD CONSTRAINT person_names_script_fkey
            FOREIGN KEY (script) REFERENCES iso15924_scripts(code) ON UPDATE CASCADE;
    END IF;
END $$;
```

- [ ] **Step 4: Run integration tests — verify pass**

```bash
uv run pytest tests/core/test_schema_locale_script_lookups.py --no-cov -m integration -q
```

- [ ] **Step 5: Confirm no regressions in the existing i18n test file**

```bash
uv run pytest tests/core/test_schema_person_names_i18n.py --no-cov -m integration -q
```

Expected: all green; tests that set `script='Latn'` etc. now require the lookup row to be present. If any test fails because it tries to insert an unregistered code, fix the test — it was relying on the column being un-validated.

- [ ] **Step 6: Commit**

```bash
git add src/core/schema.sql tests/core/test_schema_locale_script_lookups.py
git commit -m "#123 feat: FK person_names.locale → bcp47_locales, person_names.script → iso15924_scripts"
```

---

## Task 4: Document validation layering in `docs/CONVENTIONS.md`

**Files:**
- Modify: `docs/CONVENTIONS.md`

- [ ] **Step 1: Add subsection under "Person names — i18n & cultural awareness"**

Insert after the "Canonical-uniqueness key" subsection:

```markdown
#### BCP 47 / ISO 15924 lookup tables

`person_names.locale` and `person_names.script` are FK-constrained to `bcp47_locales(code)` and `iso15924_scripts(code)` respectively. The lookup tables are seeded by `scripts/seed_locales_scripts.py` from the `langcodes` and `pycountry` libraries, which live in the `seed` dependency group only — request-path code never imports them.

Validation layering:

| Layer | What it does | Source of truth |
|---|---|---|
| Admin form (Pydantic) | Strips whitespace, rejects empty strings | UI ergonomics |
| Database FK | Rejects unregistered codes (`'xx-XX'`, `'Xxxx'`) | Authoritative |
| Seed script (`langcodes` + `pycountry`) | Populates the lookup tables; runs once per env | Registry mirror |

`is_common = TRUE` rows drive the typeahead's default candidate set; the long tail is queryable but not pre-loaded into the UI dropdown. Re-seed at any time to pick up registry updates: `uv run --group seed scripts/seed_locales_scripts.py`.

ON UPDATE CASCADE is set on both FKs, so a registry-driven `code` rename propagates to existing person_names rows. ON DELETE NO ACTION (default) blocks lookup-row deletion when referenced — the registry doesn't shrink, so this is correct.
```

- [ ] **Step 2: Commit**

```bash
git add docs/CONVENTIONS.md
git commit -m "#123 docs: BCP 47 / ISO 15924 validation layering in CONVENTIONS.md"
```

---

## Task 5: Apply schema + seed to TEST_DATABASE_URL and PROD

- [ ] **Apply schema to TEST_DATABASE_URL**

```bash
export $(cat /etc/power-map/.env | xargs) 2>/dev/null
export $(cat .env | xargs) 2>/dev/null
DATABASE_URL="$TEST_DATABASE_URL" uv run python -c "
import asyncio, asyncpg, os
from src.core.db import apply_schema
async def main():
    c = await asyncpg.connect(os.environ['DATABASE_URL'])
    try:
        await apply_schema(c)
        print('schema applied')
    finally:
        await c.close()
asyncio.run(main())
"
```

- [ ] **Seed TEST_DATABASE_URL**

```bash
DATABASE_URL="$TEST_DATABASE_URL" uv run --group seed scripts/seed_locales_scripts.py
```

- [ ] **Run all integration tests**

```bash
uv run pytest tests/core/ --no-cov -m integration -q
```

Expected: same baseline + new tests passing.

- [ ] **Snapshot prod, apply schema, seed, verify**

```bash
psql "$DATABASE_URL" -c "SELECT count(*) FROM person_names"  # before
uv run python -c "
import asyncio, asyncpg, os
from src.core.db import apply_schema
async def main():
    c = await asyncpg.connect(os.environ['DATABASE_URL'])
    try:
        await apply_schema(c)
        print('prod schema applied')
    finally:
        await c.close()
asyncio.run(main())
"
uv run --group seed scripts/seed_locales_scripts.py
psql "$DATABASE_URL" -c "SELECT count(*) FROM bcp47_locales"
psql "$DATABASE_URL" -c "SELECT count(*) FROM iso15924_scripts"
psql "$DATABASE_URL" -c "SELECT count(*) FROM person_names"  # unchanged
```

- [ ] **Restart prod service**

```bash
sudo systemctl restart power-map
sudo systemctl status power-map --no-pager | head -5
curl -s -o /dev/null -w "Prod /admin/: %{http_code}\n" http://localhost:8000/admin/
```

- [ ] **Final regression sweep**

```bash
uv run pytest --no-cov -q
uv run ruff check src/ tests/ scripts/
```

---

## Done Criteria

- [ ] All 5 tasks committed on `feat/123-phase-2-admin-ui`.
- [ ] `bcp47_locales` populated with ~7000 CLDR locales; `iso15924_scripts` populated with ~200 ISO 15924 codes.
- [ ] `is_common = TRUE` for the curated initial set.
- [ ] FKs enforce registry membership; well-formed-but-unknown codes raise `ForeignKeyViolationError`.
- [ ] Re-running the seed script is a no-op for unchanged rows.
- [ ] `langcodes` and `pycountry` are NOT in the runtime dep set (verified via `uv run python -c 'import langcodes'` raising ModuleNotFoundError).
- [ ] Production schema applied, lookup tables populated, person_names row count unchanged, service healthy.

## Out of Scope

- Admin UI for the typeahead endpoints (Phase 2b).
- Editing `is_common` from the admin UI (later, if needed; SQL-only for now).
- Re-seed scheduling / CI hook (manual for now).
- Multi-script locale handling (e.g. `zh-Hant-TW` row references `Hant` via the locale tag's script subtag, not via the FK; we don't enforce cross-table consistency at DB level).
