# Person Name i18n & Cultural-Awareness Design

**Date:** 2026-05-03
**Issue:** #121
**Status:** Approved
**Related research:** `docs/research/2026_05_03-gemini.google.com-designing_person_name_information_architecture.pdf`

## Goal

Reposition the Person name model to support international names, multi-script representations, locale-aware sorting, structured (when available) name parts, and respectful handling of historical names — without forcing destructive normalization or auto-parsing on ingestion.

## Background

Current schema (`src/core/schema.sql:135-147`):

```sql
CREATE TABLE IF NOT EXISTS person_names (
    id           TEXT PRIMARY KEY,
    person_id    TEXT NOT NULL REFERENCES people(id),
    name         TEXT NOT NULL,                 -- single UTF-8 string
    name_type    TEXT NOT NULL DEFAULT 'legal'
                 CHECK (name_type IN ('legal','former','preferred','alias','initials')),
    is_canonical BOOLEAN NOT NULL DEFAULT FALSE,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE UNIQUE INDEX uq_person_canonical_name
    ON person_names(person_id, name_type)
    WHERE is_canonical = TRUE;
```

Strengths: UTF-8 free string preserves user input verbatim; mononyms work implicitly.

Gaps:

- No locale or script tag → can't pick the right rendering by request `Accept-Language` or sort with locale-appropriate collation.
- No primary-identifier hint → "Mao Zedong" and "John Smith" are programmatically indistinguishable.
- No phonetic / reading link → CJK names lose furigana / pinyin pairing.
- No sort key → particles like "van der" / "von" can't be excluded from collation reliably.
- No visibility axis → former names treated identically to current; future "deadname" handling has nowhere to live.
- Single canonical per `(person_id, name_type)` → can't have a canonical Hant *and* a canonical Latn romanization simultaneously.
- No structured name parts → can't render "Last, First" formal mailing form for a Spanish double-surname or a Chinese family-first name.

## Approved Approach

**Hybrid model** (per the research's "Architectural Synthesis"): keep `name` as the canonical UTF-8 display string; layer per-name-row metadata onto `person_names`; move structured parts to a `person_name_parts` sidecar (1:0..1, keyed on `person_names.id`). Never auto-parse; parts populated only when an upstream source provides them.

The sidecar is intentional: a person who has both a Hant `legal` row and a Latn `romanization` row holds *distinct* decompositions (`['毛']` vs. `['Mao']`). A 1:1 person-keyed parts table would force a single decomposition and lose that information.

All new columns on `person_names` are nullable additive changes. No existing rows are rewritten except the constant default on `visibility`.

### Schema changes — `person_names` (additive)

```sql
ALTER TABLE person_names
  -- Locale & script
  ADD COLUMN locale       TEXT,        -- BCP 47 (e.g. 'en-US','zh-Hant-TW','is-IS')
  ADD COLUMN script       TEXT,        -- ISO 15924 (e.g. 'Latn','Hans','Hant','Kana','Cyrl')

  -- Sorting key (NULL → use `name`)
  ADD COLUMN sort_as      TEXT,

  -- Visibility
  ADD COLUMN visibility   TEXT NOT NULL DEFAULT 'public'
       CHECK (visibility IN ('public','legal_only','hidden')),

  -- Derived-form linkage (phonetic, romanization, MRZ all use this).
  -- ON DELETE CASCADE so deleting a visual name removes its readings/MRZ.
  ADD COLUMN reading_of_id TEXT REFERENCES person_names(id) ON DELETE CASCADE;
```

### Schema changes — `person_name_parts` (new sidecar table)

```sql
CREATE TABLE person_name_parts (
    person_name_id      TEXT PRIMARY KEY
                             REFERENCES person_names(id) ON DELETE CASCADE,
    given_names         TEXT[],
    family_names        TEXT[],
    additional_names    TEXT[],
    honorific_prefix    TEXT,
    honorific_suffix    TEXT,
    primary_identifier  TEXT
        CHECK (primary_identifier IS NULL
               OR primary_identifier IN ('family','given','patronymic','mononym')),
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
```

Single PK on `person_name_id` enforces 1:0..1. Cascade ensures parts vanish when their parent name is deleted.

### `name_type` expansion

Replace the CHECK constraint to add `deadname`, `mrz`, `reading`, `romanization`, `maiden`, `religious`, `stage`. Keep all existing values (`legal`, `former`, `preferred`, `alias`, `initials`).

```sql
ALTER TABLE person_names DROP CONSTRAINT person_names_name_type_check;
ALTER TABLE person_names ADD CONSTRAINT person_names_name_type_check CHECK (
  name_type IN (
    'legal','preferred','alias','former','initials',
    'maiden','religious','stage',
    'deadname',
    'reading','romanization','mrz'
  )
);
```

Existing rows untouched. **No `former` rows are reclassified to `deadname`** — current DB has none. New `deadname` entries are explicit, set during ingestion or admin edit only.

### Canonical uniqueness — relaxed

```sql
DROP INDEX uq_person_canonical_name;
CREATE UNIQUE INDEX uq_person_canonical_name
  ON person_names(person_id, name_type, COALESCE(locale,''), COALESCE(script,''))
  WHERE is_canonical = TRUE;
```

A person can now have a canonical Hant `legal` and a canonical Latn `legal` simultaneously.

### Deadname → visibility consistency

Trigger ensures `name_type='deadname'` always implies `visibility ∈ {'legal_only','hidden'}`:

```sql
CREATE OR REPLACE FUNCTION enforce_deadname_visibility() RETURNS TRIGGER AS $$
BEGIN
  IF NEW.name_type = 'deadname' AND NEW.visibility = 'public' THEN
    NEW.visibility := 'legal_only';
  END IF;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_deadname_visibility
  BEFORE INSERT OR UPDATE ON person_names
  FOR EACH ROW EXECUTE FUNCTION enforce_deadname_visibility();
```

A deadname can never be `public`. Coercion (not error) so admin UI doesn't have to know the rule.

### Display view — visibility-aware

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

When a person has multiple canonical rows (different `(locale, script)` pairs), the view returns one arbitrarily. Future work (out of scope): `Accept-Language` negotiation in the API layer to pick the best canonical.

## Visibility Rule (codified)

Single rule, applied everywhere:

> A `person_names` row with `visibility ∈ {'legal_only','hidden'}` is excluded from `v_person_display_names`, all public API responses, all admin search results, all admin list pages, all autocomplete/typeahead, and all duplicate-detection candidate sets. It surfaces **only** on the person-detail page in admin, behind an explicit "Show legal/historical names" disclosure toggle (default collapsed), and is never shown in flash messages or activity logs.

Enforcement layers:

1. **View layer:** `v_person_display_names` filters by `visibility='public'`. All admin display queries already use the view.
2. **Query helper:** new `visible_names_filter()` snippet in `src.core.db` (or `src.core.queries`) for queries that need raw `person_names` access (search, dup-detection); returns the AND clause to append.
3. **Lint test:** pytest test that greps `src/` for `FROM person_names` outside the helper / view; whitelist the admin-detail handler explicitly.

This document records the rule; `docs/CONVENTIONS.md` will get the runtime version.

## ICAO 9303 / MRZ

Handled as a `name_type='mrz'` row linked via `reading_of_id` to its visual original — same mechanism as phonetic readings and romanizations. No dedicated table. Derivation (uppercase, ASCII fold, diacritic strip, hyphen→space, apostrophe drop, `<` filler) is documented in `docs/CONVENTIONS.md` so it's reproducible at write time. No automatic generation; populated when an integration needs it.

Example for José García-López:

```
visual:  name='José García-López', name_type='legal',  script='Latn', locale='es-MX'
mrz:     name='GARCIA<LOPEZ<<JOSE', name_type='mrz',   script='Latn',
         reading_of_id=<visual.id>
```

## vCard Export

Out of scope. The hybrid model maps cleanly to vCard 4.0 when needed:

- `name` → `FN`
- structured part columns → `N`
- `sort_as` → `SORT-AS`
- `locale` → `LANGUAGE`
- `reading_of_id` chains → phonetic params

Pure transformation at export time; no schema accommodation required now.

## Operational Rules (codify in `docs/CONVENTIONS.md`)

- Never lowercase, title-case, or strip diacritics on input. Store user input exactly.
- Sort with Postgres ICU collations (`COLLATE "und-x-icu"` or per-locale) — not `LOWER()`. `sort_as` overrides `name` when present.
- Mononyms: a single `person_names` row with `primary_identifier='mononym'`. No first/last fields, no placeholder hyphen.
- Ingestion: if a source string isn't confidently structured, store `name` only and leave parts NULL. Never auto-parse to fill parts.
- New fields are all optional except `visibility` (defaults to `'public'`).

## Non-Goals

- Auto-parsing names via regex / LLM — explicitly excluded; brittle on real-world data.
- Full vCard 4.0 wire format — concepts adopted, format deferred.
- ICAO MRZ auto-generation pipeline — schema supports it; no derivation logic this round.
- `Accept-Language` content negotiation in API responses — schema supports multi-canonical; negotiation is a follow-up.
- Backfilling `locale` / `script` on existing rows — left NULL; populate opportunistically when records are touched.
- Admin UI changes for new fields — schema-first; UI surfaces follow in a separate plan.

## Migration Safety

Production data lives on this VM. Constraints:

- All new columns nullable except `visibility` (constant default — table is small, full rewrite acceptable).
- `name_type` CHECK migration: drop + add. Existing values are all in the new set, so no row updates needed.
- Index swap (`uq_person_canonical_name`): drop + recreate. User confirmed no maintenance window required.
- Trigger creation is non-blocking.
- View recreation: `CREATE OR REPLACE` is non-blocking.
- Migration is idempotent: wrapped in `DO $$ ... IF NOT EXISTS ... END $$` blocks following the existing pattern in `schema.sql`.
- Rehearse against `TEST_DATABASE_URL` before applying to prod.

## Files Touched (anticipated)

- `src/core/schema.sql` — column adds, CHECK swap, index swap, trigger, view rewrite, new `person_name_parts` table (+ idempotent migration blocks, one DO per column matching the `archived_at` pattern).
- `tests/core/test_schema_person_names_i18n.py` — new tests for each schema change (TDD), including parts-table coverage.
- `src/core/db.py` — `visible_names_filter()` helper.
- `src/core/ingestion/pipeline.py` — add `visibility='public'` filter to person auto-match.
- `src/api/admin/people.py`, `people_names.py`, `people_merge.py` — comment-only allow-list documentation.
- `tests/core/test_visible_names_filter.py` — unit + lint test for direct `person_names` access.
- `AGENTS.md` — pointer to the visibility rule.
- `docs/CONVENTIONS.md` — visibility rule, MRZ derivation, ICU collation, no-auto-parse, parts-table semantics.

## Out of Scope (explicit)

- Public API schema additions (`PersonName` Pydantic model new fields) — separate plan.
- Admin UI for editing locale / script / parts / visibility — separate plan.
- Ingestion changes to populate structured parts from sources — separate plan per source.
- Duplicate-detection algorithm changes to leverage script/locale — separate plan.
- Backfill of `locale='en'` / `script='Latn'` for existing rows — explicitly left NULL.

## Phasing

**Phase 1 (this plan):** Schema + view + trigger + visibility helper + tests + docs. No UI, no API changes.

**Phase 2 (future plan):** Admin UI for locale/script/visibility editing; deadname disclosure toggle on person detail.

**Phase 3 (future plan):** Public API additive fields; `Accept-Language` negotiation.

**Phase 4 (future plan):** Ingestion pipelines populating structured parts from per-source schemas.
