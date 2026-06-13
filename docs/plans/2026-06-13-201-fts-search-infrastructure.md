---
title: FTS search infrastructure — orgs, people, roles, jurisdictions (#201)
date: 2026-06-13
status: draft
supersedes: docs/plans/2026-06-13-fts-search-infrastructure-design.md
---

# FTS Search Infrastructure (#201)

## Problem

Search on `orgs/search` and `people/search` uses `ILIKE '%q%'` against name columns with
no index support. This is a sequential scan, case-insensitive but otherwise unnormalized:
`Jr.` does not match `Jr`, `Hernández` does not match `hernandez`, and there is no
relevance ranking. Admin list-view filters have the same defect. As the corpus grows and
consumers rely on these endpoints for entity lookup, both correctness and performance
degrade.

## Approach

Add a `tsvector search_tsv` column to each of the four searchable entity tables
(`organizations`, `people`, `roles`, `jurisdictions`), kept current by DB triggers, indexed
with GIN, and queried via `plainto_tsquery`. Two named TS configs are introduced:
`pm_simple` (lowercase + punctuation-strip, no stemming) for orgs/roles/jurisdictions and
`pm_unaccent_simple` (same + accent-strip) for people. Admin typeaheads retain ILIKE but
gain trigram GIN indexes on the underlying name columns so substring matching stays fast.
Full design rationale: `docs/plans/2026-06-13-fts-search-infrastructure-design.md`.

## Tradeoffs / alternatives

- **`english` TS config** — rejected: stop-word removal drops "and"/"of" from org names;
  Porter stemmer mangles proper nouns ("Appropriations" → "appro").
- **Python-side normalization + ILIKE** — rejected: only unidirectional; no accent handling
  without adding `unicodedata` munging; no ranking; still a seq scan.
- **External search service (Typesense / Meilisearch)** — deferred: adds infrastructure
  and sync burden not justified at current corpus size; revisit if FTS proves insufficient.
- **Trigram GIN on name columns for both typeaheads and API** — rejected for API: trigram
  is substring-only and provides no word-boundary or accent normalization; kept for
  typeaheads where substring is the required behaviour.

## Steps

1. **TS configs + `unaccent` extension** — add `CREATE EXTENSION IF NOT EXISTS unaccent`
   and the two TS config blocks to `schema.sql`; write integration tests that assert the
   configs exist and that `to_tsvector('pm_unaccent_simple', 'Hernández')` produces lexeme
   `hernandez`.

2. **`organizations.search_tsv` column + triggers + GIN index** — add column, Pattern-B
   trigger function `fn_refresh_org_search_tsv` firing AFTER INSERT/UPDATE/DELETE on
   `organization_names` and `organization_acronyms` and AFTER UPDATE OF notes on
   `organizations`, backfill UPDATE, GIN index. Tests: insert/update/delete a name and
   assert tsvector updates; search by name variant, acronym, and notes.

3. **`people.search_tsv` column + triggers + GIN index** — same Pattern-B shape; aggregation
   filters `visibility = 'public'`. Tests: hidden-name exclusion (visibility change removes
   token from tsvector), accent normalization (`hernandez` finds `Hernández`), notes search.

4. **`roles.search_tsv` column + trigger + GIN index** — Pattern-A BEFORE trigger on
   `title, notes`. Tests: title and notes match; title change updates tsvector.

5. **`jurisdictions.search_tsv` column + trigger + GIN index** — Pattern-A BEFORE trigger
   on `name, slug, notes`. Tests: name, slug, and notes match independently.

6. **Trigram GIN indexes for admin typeaheads** — add `gin_trgm_ops` indexes on
   `lower(organization_names.name)`, `lower(person_names.name) WHERE visibility='public'`,
   and `lower(roles.title)` to `schema.sql`. No query changes; these back existing ILIKE
   calls.

7. **Public API query rewrites** — replace ILIKE in `src/api/public/orgs.py` and
   `src/api/public/people.py` with `@@ plainto_tsquery(...)` + `ts_rank` ordering. Update
   existing integration tests; add tests confirming `Jr.`/`Jr` match and accent match reach
   the HTTP layer.

8. **Admin list-view query rewrites** — replace ILIKE `?q=` filters in
   `src/api/admin/orgs.py`, `src/api/admin/people.py`, `src/api/admin/roles.py`,
   `src/api/admin/role_assignments.py` with FTS equivalents. Update existing tests.

9. **`docs/PUBLIC_API.md` update** — document the changed search semantics (word-boundary,
   accent-insensitive, ranked) and the unchanged typeahead behaviour.

## Open questions / risks

- `apply_schema` is idempotent by convention (`IF NOT EXISTS` guards). TS configs and
  dictionaries need the same guard; confirm `DO $$ IF NOT EXISTS (SELECT 1 FROM
  pg_ts_config WHERE cfgname = 'pm_simple') ... $$` is the right idiom before writing
  schema blocks.
- Backfill runtime on production: corpus is small (~thousands of orgs/people); a blocking
  `UPDATE` in `apply_schema` is acceptable, but flag if that assumption is wrong.
- `fn_refresh_org_search_tsv` for the AFTER UPDATE OF notes trigger references
  `NEW.organization_id` — but `NEW` on the `organizations` table has `id`, not
  `organization_id`. The function needs a separate code path that uses `NEW.id` when the
  firing table is `organizations`. Consider two distinct trigger functions or a `TG_TABLE_NAME`
  branch.
