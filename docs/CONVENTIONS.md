# power-map — API, DB & Ingestion Conventions

Reference for public API, database, and ingestion patterns. For admin dashboard and UI patterns, see `docs/STYLE.md §32`.

---

## Public API

- Auth: `X-API-Key` header → `require_api_key` dep (in `src.api.public.deps`); 403 on missing, 401 on invalid; updates `api_keys.last_used_at`
- Versioning: path-based (`/api/v1/`). Bump the prefix when introducing breaking changes
- Response models: all routes must declare a Pydantic `response_model` (in `schemas.py`) and an explicit `operation_id`; `dict[str, Any]` return types are not allowed — OpenAPI schema must be fully typed
- List endpoints: return `{"data": [...], "meta": {"limit", "offset", "count", "has_more"}}`; fetch `limit + 1` rows to compute `has_more` without a `COUNT(*)` query; return only `limit` rows in `data`
- Single-resource endpoints: return the resource object directly (no envelope)
- Timestamps: serialize with `_fmt_ts()` from `schemas.py` — ISO 8601 with `Z` suffix
- CORS: not required — the public API is server-to-server only (no browser callers)

---

## DB

- PKs: ULIDs via `generate_id()` from `src.core.db`
- `apply_schema(conn)` is idempotent (`IF NOT EXISTS` / `ON CONFLICT DO NOTHING`); wraps in a transaction
- `updated_at`: maintained by DB triggers — never set manually in application code
- Phone: normalize to E.164 via `PhoneNormalizer` from `src.core.normalizers.phone`
- Email: validate via `EmailNormalizer` from `src.core.normalizers.email`
- Integration tests (marked `integration`) require `TEST_DATABASE_URL` env var; `tests/conftest.py` redirects `DATABASE_URL` → `TEST_DATABASE_URL` and skips when absent — never runs against the production DB
- Integration test fixtures share a session-scoped `db_pool` (`asyncpg.create_pool`) from `tests/conftest.py`; `apply_schema` runs once at session start. Fixtures and tests acquire via `async with db_pool.acquire() as conn:` — never `await asyncpg.connect(...)` per call. Reference recipe: `tests/api/admin/test_people_names.py`.
  - Required markers in every consumer module: `pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="session")]` at module level, and `@pytest_asyncio.fixture(loop_scope="session")` on every async fixture.
  - Both `loop_scope="session"` markers are load-bearing: `db_pool` is bound to the session event loop. Omitting either raises `RuntimeError: ... attached to a different loop` at fixture setup.
  - Sole exception: `tests/core/test_db.py` tests `src.core.db.get_pool()` / `create_pool()` lifecycle itself and intentionally owns its own connection.
  - Teardown for entities referenced by a polymorphic side table (e.g. `addresses` via `entity_addresses`): fetch the side-table's foreign-key ids before dropping the join rows, then delete the entity rows guarded by `NOT EXISTS` so a shared row can't FK-fail. Wrap the read+writes in a single `async with conn.transaction():` so a mid-teardown failure rolls back cleanly. Pair with a module-scoped autouse fixture that snapshots the entity table's rowcount before/after and asserts equality — catches leaks if someone later "simplifies" the teardown. Reference: `tests/api/admin/test_orgs_addresses.py` (#150).

### Display names

- Org: use `v_org_display_names` for all queries displaying an org name — formats as "Name (Acronym)" when a canonical acronym exists, otherwise just "Name". Never join `organization_names` or `organization_acronyms` directly for display
- Person: use `v_person_display_names` — returns the canonical `person_names` row filtered to `visibility='public'`. The view exposes `display_name` (visible string) and `sort_key` (`COALESCE(sort_as, name)`, Phase 2b #123). For person ORDER BY, use `sort_key COLLATE "und-x-icu" NULLS LAST` so diacritics order locale-aware (Å near A) and any `sort_as` override is honored. See "Person names — i18n & cultural awareness" below. Never join `person_names` directly for display.
- Acronyms in `organization_acronyms` (separate table); `organization_names` holds legal/dba/former names only. Each table has exactly one canonical row per org via a partial unique index

### Auto-promote invariant

Every **delete** route on `organization_names` must call `_maybe_promote_sole_name(org_id, db)` inside its transaction (from `src.api.admin.orgs_names`) — promotes the sole remaining non-canonical name to canonical, keeping `v_org_display_names.display_name` non-NULL. Edit routes do not need this — the canonical edit guard prevents completing when it would leave zero canonical names.

Equivalent for acronyms: `_maybe_promote_sole_acronym(org_id, db)` (from `src.api.admin.orgs_acronyms`) — call inside the transaction of every **delete** route on `organization_acronyms`.

### Last-identity guard

`name_delete` blocks when the org has exactly one name and no canonical acronym; `acronym_delete` blocks symmetrically.

- HTMX: return HTTP 200 with `flash_trigger("error", ...)` and empty body
- Non-HTMX: raise `HTTPException(409)`

### Links schema

`link_types` table holds (slug, display_name, is_social). `links` table holds (entity_type, entity_id, url, link_type_id, is_active). Social links: `JOIN link_types WHERE is_social = TRUE`.

**Natural-key uniqueness** (`uq_links_entity_url`, issue #142): an entity must not carry the same URL twice for the same `link_type_id`. `is_active` is intentionally excluded from the index — keeping both an active and an archived copy of the same URL is not a supported state. Ingestion pipeline writes use `ON CONFLICT (entity_type, entity_id, url, link_type_id) DO NOTHING` so re-runs are idempotent. Admin CRUD (create + update) catches `asyncpg.UniqueViolationError` and returns 409 with a `warning` flash; never bubbles as a 500.

### Person names — i18n & cultural awareness

Hybrid model (issue #121): `person_names.name` is the canonical UTF-8 display string; per-name-row metadata (`locale`, `script`, `sort_as`, `visibility`, `reading_of_id`) lives on `person_names`; structured parts live in the `person_name_parts` sidecar (1:0..1, keyed on `person_names.id`).

#### Storage rules

- Store user input verbatim. **Never** lowercase, title-case, ASCII-fold, or strip diacritics on input — names like "McNamara", "van der Waals", or "ffrench" rely on specific casing; Vietnamese names rely on diacritics.
- `name` is the authoritative free string. Structured parts in `person_name_parts` are **never auto-decomposed** — populated only when an upstream source supplies pre-parsed structure (e.g., via the observation endpoint's `names[].parts` field) or when a human confirms a suggestion — the "David Lloyd George" ambiguity is unresolvable without cultural context. **Never auto-write parts to the database** without human confirmation or upstream pre-parsed data. Assisted *suggestion* of parts is allowed via `src.core.normalizers.person_name.suggest_parts(...)` — used by triage/backfill scripts (CSV-mediated review) and, optionally, the admin name editor (pre-fill the form for review). The decomposer never persists; only the existing `upsert_or_delete_parts` path does.
- Sort with Postgres ICU collations (e.g. `ORDER BY name COLLATE "und-x-icu"`), or by `sort_as` when present. Do not use `LOWER(name)` for sorting.
- New rows default to `visibility='public'`. The `trg_deadname_visibility` trigger downgrades any `name_type='deadname'` row from `'public'` to `'legal_only'` automatically; an explicit `'hidden'` is preserved.

#### Visibility rule (single, project-wide)

A `person_names` row with `visibility ∈ {'legal_only', 'hidden'}` is excluded from:

- `v_person_display_names`
- All public API responses
- All admin search results, list pages, autocomplete, typeahead
- All duplicate-detection candidate sets and ingestion auto-match queries
- All flash messages and activity logs

It surfaces **only** on the person-detail admin page, behind an explicit "Show legal/historical names" disclosure toggle (default collapsed).

Enforcement layers:

- `v_person_display_names` filters by `visibility='public'` — use the view for all display.
- For raw `FROM person_names` / `JOIN person_names` queries, AND-append `visible_names_filter()` from `src.core.db` (or the literal `visibility = 'public'`).
- `tests/core/test_visible_names_filter.py::test_no_unguarded_person_names_queries` greps for direct access outside `ALLOWED_DIRECT_ACCESS`. New direct-access call sites must either filter visibility inline or be added to the allow-list with a `# visibility-allowlist (issue #121): <reason>` comment.

#### `name_type` values

| Value | Meaning |
|---|---|
| `legal` | Government-recognized legal name |
| `preferred` | What the person asks to be called publicly |
| `alias` | Alternate identifier (pen name, handle) |
| `former` | Previous name (marriage change, divorce, voluntary change) |
| `initials` | Initialism (`JFK`, `MLK`) |
| `maiden` | Birth surname |
| `religious` | Religious / monastic name |
| `stage` | Performer / artist name |
| `deadname` | Pre-transition or pre-disclosure name; auto-`legal_only` |
| `reading` | Phonetic reading of another row (e.g. furigana) — link via `reading_of_id` |
| `romanization` | Latin-script rendering of another row (pinyin, romaji) — link via `reading_of_id` |
| `mrz` | ICAO 9303 Machine-Readable Zone form — link via `reading_of_id` |
| `variant` | Alt-spelling / nickname of an existing name on the same person (e.g. `Jodi`/`Jody`, `Kip`/`Kristopher`) — see below |

#### `variant` vs neighbouring types

| | `variant` | `alias` | `preferred` | `reading`/`romanization`/`mrz` |
|---|---|---|---|---|
| Same identity? | yes | usually no (pen name, handle) | yes | yes |
| Linked via `reading_of_id`? | no | no | no | yes |
| Typical case | `Jody`/`Jodi` (uncertain spelling), `Kip`/`Kristopher` (short form) | "Mark Twain" for Samuel Clemens | What they go by | Phonetic / latin-script / passport form |

A `variant` row sits next to its `legal` row on the same person; both share `person_id`. `is_canonical=FALSE` on the variant; the legal row stays canonical. Use `variant` (not `alias`) when collation against the canonical name matters — e.g. to surface `Jody`-typed search input alongside `Jodi`-keyed records without conflating with truly separate identities.

#### MRZ derivation

When generating an MRZ row from a Latin-script visual `legal` row:

| Transformation | Example |
|---|---|
| Uppercase all letters | `José` → `JOSE` |
| Strip diacritics (NFKD + ASCII-only) | `García` → `GARCIA` |
| Replace hyphens with single space | `García-López` → `GARCIA LOPEZ` |
| Drop apostrophes | `O'Brien` → `OBRIEN` |
| Use `<` as filler / separator | `GARCIA<LOPEZ<<JOSE` |

No automatic generation pipeline exists in Phase 1 — populate manually or via a future ingestion integration.

#### Structured parts (`person_name_parts` sidecar)

Parts live in `person_name_parts`, keyed on `person_name_id` (1:0..1 with `person_names`). Each `person_names` row optionally has its *own* parts row — the Hant `legal` and Latn `romanization` of one person each carry distinct decompositions, not a shared set. ON DELETE CASCADE — when a `person_names` row is deleted its parts row is removed too.

Columns: `given_names TEXT[]`, `family_names TEXT[]`, `additional_names TEXT[]`, `honorific_prefix`, `honorific_suffix`, `primary_identifier`. Arrays are ordered. `primary_identifier` indicates which array drives formal address and primary sort:

- `'family'` — Western, Sinitic, Hungarian (last-name address); sort by `family_names[1]`
- `'given'` — Icelandic, mononymous fallback; sort by `given_names[1]`
- `'patronymic'` — Arabic chain, Russian; address by `given_names[1]`
- `'mononym'` — single-name people (Cher, Prince); the single token is in `name`

A `person_names` row with no corresponding `person_name_parts` row is fully valid — the free `name` string remains authoritative.

The admin UI surfaces this section as **Details** (issue #127); the DB / route names retain `parts` / `person_name_parts`.

#### Canonical-uniqueness key

`uq_person_canonical_name` is keyed on `(person_id, name_type, COALESCE(locale, ''), COALESCE(script, ''))`. A person can hold a canonical Hant `legal` and a canonical Latn `legal` (romanization) simultaneously.

#### BCP 47 / ISO 15924 lookup tables (issue #123, Phase 2-prep)

`person_names.locale` and `person_names.script` are FK-constrained to `bcp47_locales(code)` and `iso15924_scripts(code)` respectively. The lookup tables are seeded by `scripts/seed_locales_scripts.py` from the `langcodes` and `pycountry` libraries, which live in the `seed` dependency group only — request-path code never imports them.

Validation layering:

| Layer | What it does | Source of truth |
|---|---|---|
| Admin form (Pydantic) | Strips whitespace, rejects empty strings | UI ergonomics |
| Database FK | Rejects unregistered codes (`'xx-XX'`, `'Xxxx'`) | Authoritative |
| Seed script (`langcodes` + `pycountry`) | Populates the lookup tables; runs once per env | Registry mirror |

No curated default-set is maintained — the typeahead's empty state shows a placeholder and narrows the full table by user keystrokes. The human-readable column differs by table: locales use `display_name`, scripts use `name`. So:

- locales: `code ILIKE '%q%' OR display_name ILIKE '%q%'`
- scripts: `code ILIKE '%q%' OR name ILIKE '%q%'`

pg_trgm GIN indexes are present on both columns of both tables (Postgres' planner may still pick Seq Scan at current row counts; the index is load-bearing as the data grows). Re-seed at any time to pick up registry updates: `uv run --group seed scripts/seed_locales_scripts.py`.

ON UPDATE CASCADE is set on both FKs, so a registry-driven `code` rename propagates to existing person_names rows. ON DELETE NO ACTION (default) blocks lookup-row deletion when referenced — the registry doesn't shrink, so this is correct.

A symmetric FK on `bcp47_locales.script → iso15924_scripts(code)` (same `ON UPDATE CASCADE`) keeps locale rows consistent with the script registry. Phase 2b code may join `bcp47_locales.script → iso15924_scripts.code` to enrich locales with their script's `name`/`numeric_code` without defensive existence checks.

---

## Unique Indexes (PostgreSQL 15+)

- `uq_role_org_title` — `roles(organization_id, lower(title)) WHERE archived_at IS NULL`
- `uq_role_assignment_person_role_start` — `role_assignments(person_id, role_id, start_date) NULLS NOT DISTINCT WHERE archived_at IS NULL`
- Both created inside `DO … EXCEPTION WHEN unique_violation` blocks so `apply_schema` is safe on DBs with existing duplicates (logs WARNING instead of raising)
- Schema tests for `uq_role_org_title` are guarded by a `require_uq_role_org_title` fixture that skips when the index is absent

**Bootstrap sequence for a dirty DB:** (1) run `scripts/deduplicate_roles.py --execute` to collapse duplicates, (2) re-run `apply_schema` (or restart the service) to create the indexes, (3) verify with `\d roles` / `\d role_assignments` in psql.

### `pg_trgm` extension

Enabled via `CREATE EXTENSION IF NOT EXISTS pg_trgm` in `apply_schema`. Required for org duplicate detection (`similarity()` function). Re-run `apply_schema` (or restart) to install on existing databases. Gracefully degrades to `org_dup_count = 0` if not installed.

---

## Ingestion

- EVTL pattern: Extract (CSV read) → Validate (Pydantic) → Transform (normalize fields) → Load (DB insert)
- `RowResult` envelope: `errors` = fatal (entity skipped), `warnings` = non-fatal (field skipped)
- `field_confidence` is append-only; query latest with `ORDER BY assessed_at DESC LIMIT 1`
- `import_batches.file_hash` is unique; re-running with same files reuses the existing batch
- Address standardization uses the external address-validator service when `ADDRESS_VALIDATOR_API_KEY` is set; falls back to local `usaddress` parsing otherwise
- `addresses.precision` indicates the specificity tier of the geocoded result (`street`, `postal`, `city`, `region`, `country`; NULL = unset or pre-geocoding historical record). Event place linkage (`entity_events.event_place_address_id`) requires city-level or finer (`city`, `postal`, `street`) — or NULL; `country`/`region` precision is rejected. See `EVENT_PLACE_PRECISIONS` in `src/core/types.py`.
- `VALIDATE_ADDRESSES=true` (or `--validate-addresses` CLI flag) enables the `/validate` endpoint
- `role_index` is pre-populated from the DB at pipeline startup (Pass 3) so re-runs are idempotent across batches
