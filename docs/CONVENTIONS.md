# power-map — API, DB & Ingestion Conventions

Reference for public API, database, and ingestion patterns. For admin dashboard and UI patterns, see `docs/STYLE.md §32`.

---

## Public API

- Auth: `X-API-Key` header → `require_api_key` dep (in `src.api.public.deps`); 403 on missing, 401 on invalid, 429 on rate limit (#292 — per-key read/write token buckets in `src.api.public.ratelimit`, checked in `_resolve_api_key`; per-worker, env-tunable via `RATE_LIMIT_*`); updates `api_keys.last_used_at` (debounced, `API_KEY_LAST_USED_DEBOUNCE_S`, default 60s)
- Versioning: path-based (`/api/v1/`). Bump the prefix when introducing breaking changes
- Response models: all routes must declare a Pydantic `response_model` (in `schemas.py`) and an explicit `operation_id`; `dict[str, Any]` return types are not allowed — OpenAPI schema must be fully typed
- List endpoints: return `{"data": [...], "meta": {"limit", "offset", "count", "has_more"}}`; fetch `limit + 1` rows to compute `has_more` without a `COUNT(*)` query; return only `limit` rows in `data`
- Stable pagination: every paginated `ORDER BY` **must end with a unique column** (usually the PK, e.g. `, id` / `, o.id`). Ordering by non-unique keys alone (name, rank, `created_at`) lets Postgres return tied rows in a different order per query, so offset windows overlap and gap — consumers paging the full list silently skip and duplicate rows (#297). Append the tiebreaker even when a partial unique index makes ties rare (archived rows, jurisdictional variants), since offset pagination must be total-ordered across *all* returned rows
- Single-resource endpoints: return the resource object directly (no envelope)
- Timestamps: use `datetime` fields in Pydantic response models + `@field_serializer` calling `fmt_ts()` from `schemas.py`; ISO 8601 with `Z` suffix; never pre-serialize as `str` in handlers
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
  - Required markers in every consumer module: `pytestmark = pytest.mark.integration` at module level, and `@pytest_asyncio.fixture(loop_scope="session")` on every async fixture.
  - Fixture `loop_scope="session"` is load-bearing: `db_pool` is bound to the session event loop. The test-function asyncio mark is no longer needed — `asyncio_default_test_loop_scope = "session"` in `pyproject.toml` sets the default globally.
  - Sole exception: `tests/core/test_db.py` tests `src.core.db.get_pool()` / `create_pool()` lifecycle itself and intentionally owns its own connection.
  - Teardown for entities referenced by a polymorphic side table (e.g. `addresses` via `entity_addresses`): fetch the side-table's foreign-key ids before dropping the join rows, then delete the entity rows guarded by `NOT EXISTS` so a shared row can't FK-fail. Wrap the read+writes in a single `async with conn.transaction():` so a mid-teardown failure rolls back cleanly. Pair with a module-scoped autouse fixture that snapshots the entity table's rowcount before/after and asserts equality — catches leaks if someone later "simplifies" the teardown. Reference: `tests/api/admin/test_orgs_addresses.py` (#150).
- **Endpoint-test client — prefer the lifespan-less rollback client (#288).** A per-test `with TestClient(app)` enters the app lifespan, which calls `asyncpg.create_pool` (~170 ms TCP handshake + type introspection) *per test* — ~30% of integration wall-clock. Instead, drive the app with an `AsyncClient` over `ASGITransport` and override `get_db` to yield a single BEGIN/ROLLBACK-wrapped `db_pool` connection: no lifespan → no per-test pool, and app writes + fixture setup share one transaction that rolls back automatically (no manual `DELETE` teardown). Because the app and the connection run on the same session event loop, this only works with async `AsyncClient`, not sync `TestClient`. The shared `db` connection means read-back assertions see the app's uncommitted writes. Routes that open their own `async with db.transaction()` become savepoints under the outer test transaction — the outer rollback still discards them. Reference recipe: `tests/api/admin/test_orgs.py` / `tests/api/admin/test_orgs_detail_inline.py`.
    - **Pure-unit route tests** (no real DB — `get_db` fully mocked) must construct `TestClient(app)` **without** `with`: no lifespan → no pool → no live-DB dependency. Restore overrides with targeted `app.dependency_overrides.pop(dep, None)`, never `.clear()`. Reference: `tests/api/admin/test_router_ordering.py`.
    - **When rollback is *wrong* — the committing client (#288).** A single wrapping transaction can't serve two kinds of test; these use the lifespan-less **committing** fixtures (`committing_db` / `committing_client` in `tests/api/public/conftest.py`) — autocommit (no wrapping transaction), so still no per-test pool, but writes **commit**. You must restore explicit teardown (or rely on unique ULIDs + the session-start truncation) since nothing rolls back.
        - **Timestamps / etags that must *advance*.** Postgres `now()` / `CURRENT_TIMESTAMP` is fixed at transaction start, so `updated_at` (set by triggers) is identical for every write inside one transaction — the rollback client freezes it. A test asserting an entity's etag *changes* after a mutation needs the two writes in *separate* transactions → committing client. Reference: the etag tests in `tests/api/public/test_orgs.py` (they shadow `db`/`client` with the committing variants at file scope).
        - **A *separate* connection must see the row.** `RequestLogMiddleware` writes its `api_request_log` row on a background task via the module-global pool (`db.get_pool()`), never the request-scoped connection — so it can't see rows created in the rollback client's uncommitted transaction. Such tests need committed data + the real global pool. The public conftest's session-autouse fixture creates the global pool (mirroring the lifespan) precisely for this. Reference: `tests/api/public/test_request_log_middleware.py`.
    - **Pool headroom:** each rollback client holds one `db_pool` connection for the whole test (`DB_POOL_MAX_SIZE=2` in tests). Revisit per-worker sizing before any `pytest-xdist` parallelization (#288).
    - **Read-only sweep variant — seed once per module.** When a suite only *reads* (e.g. renders every admin GET route and asserts on the HTML), scope the `db`/`client`/`seed` fixtures to `module` instead of function: one BEGIN/ROLLBACK connection and one seed serve all parametrized cases, so a 148-route sweep pays the seed cost once. Same rollback isolation as the per-test client — just amortized. Reference: `tests/api/admin/test_a11y_render.py` (the #246 rendered-DOM a11y sweep; checker in `tests/api/admin/a11y.py`). Note `follow_redirects=False` there is deliberate: asserting a direct `200` keeps a route that 3xx's from silently passing by following the redirect elsewhere.

### Display names

- Org: use `v_org_display_names` for all queries displaying an org name — formats as "Name (Acronym)" when a canonical acronym exists, otherwise just "Name". Never join `organization_names` or `organization_acronyms` directly for display
- Person: use `v_person_display_names` — returns the canonical `person_names` row filtered to `visibility='public'`. The view exposes `display_name` (visible string) and `sort_key` (`COALESCE(sort_as, name)`, Phase 2b #123). For person ORDER BY, use `sort_key COLLATE "und-x-icu" NULLS LAST` so diacritics order locale-aware (Å near A) and any `sort_as` override is honored. See "Person names — i18n & cultural awareness" below. Never join `person_names` directly for display.
- Acronyms in `organization_acronyms` (separate table); `organization_names` holds legal/dba/former names only. Each table has exactly one canonical row per org via a partial unique index

### Org name effective dates (#239)

`organization_names` carries `effective_start` / `effective_end` (nullable `DATE`, `CHECK (start <= end)` = `chk_org_name_effective_date_order`) — the name's real-world validity timeline. PM is the system of record for "which name was in effect when"; consumers filter the dated name list rather than calling an as-of endpoint.

- **Identity model:** a rename is **one durable Org**, never a fork. An external identifier (e.g. `org_wa_legislature_committee_id`) anchors exactly one Org for its whole life — "one WSL Id = one committee" is a deliberate invariant. Resolves CannObserv/usa-wa#40.
- **Orthogonal axes:** effective dates are independent of `is_canonical` (the display pointer) and `name_type` (the kind of name). NULL `effective_start` = unknown lower bound (−∞); NULL `effective_end` = still in effect (+∞).
- **Ingestion is append-only:** `write_names` stores dates only on a newly inserted row; dates sent for an already-present name are a no-op. Rename transitions (close the old interval, promote the new canonical) are **curated in admin**, never feed-driven.
- **Broadcast:** any name-row INSERT/UPDATE/DELETE fires `trg_touch_org_on_name_change` → bumps `organizations.updated_at` → emits an `entity_changes` `'updated'` row, so change-feed subscribers re-fetch and pick up the new dates.

### Address validity windows (#181)

`entity_addresses` carries `valid_from` / `valid_until` (nullable `DATE`, `CHECK (from <= until)` = `chk_ea_validity`) — the link's real-world validity window. NULL = open-ended on that side. Overlapping windows are legitimate (mailing + physical simultaneously, two offices of the same type) — do **not** add an overlap-exclusion constraint.

- **Identity model:** the window is part of the link's identity. `entity_addresses_entity_addr_uniq` is `UNIQUE NULLS NOT DISTINCT (entity_type, entity_id, address_type, address_id, valid_from, valid_until)` — the same entity at the same address across two windows is history, not a duplicate. Any dedup logic (merge anti-joins, schema-migration dedup DELETEs) must compare windows with `IS NOT DISTINCT FROM`.
- **No `is_current` flag:** a generated column is impossible (`CURRENT_DATE` is not immutable); call sites filter with `valid_until IS NULL OR valid_until >= CURRENT_DATE`, matching the jurisdictions pattern. Admin detail queries sort current-first, then `valid_from DESC NULLS LAST`.
- **Ingestion:** `write_addresses` dedups on the normalized form plus the window, mirroring the unique key (#256). Dateless claims (both bounds NULL) stay window-agnostic and dedup against any existing row; dated claims (`ObservationAddress.valid_from` / `.valid_until`, ISO `YYYY-MM-DD`, `from <= until`) dedup on the exact window via `IS NOT DISTINCT FROM` and record a fresh link for a new window, reusing the existing `addresses` row rather than minting a per-window duplicate. Supply dates only when an upstream source carries them; validity dates are otherwise curated in admin.
- **Historical-window semantics — admin end-dating is authoritative over feeds (#256 decision, from #181 CR finding 4):** a dateless claim keeps matching *any* existing row, including an expired historical row (`valid_until < CURRENT_DATE`). So once an admin end-dates an entity's address, a later dateless re-observation records **nothing** — it does not resurrect a current, open-ended row. Rationale: curation is deliberate and human; silently reopening a closed window on the next ingest run would be whack-a-mole. A dateless re-observation of an expired address therefore leaves no trace (observations aren't logged as per-sighting events) — intentional. A source that genuinely needs to assert a *current* window supplies explicit `valid_from`/`valid_until` (the dated-claim escape hatch, #256 item 1); dated claims dedup with strict `IS NOT DISTINCT FROM` window equality, while dateless claims deliberately ignore the window.
- **Broadcast:** any `entity_addresses` INSERT/UPDATE/DELETE fires `trg_touch_entity_on_address_change` → bumps the parent entity's `updated_at` → emits an `entity_changes` `'updated'` row (all five entity types), so change-feed subscribers re-fetch and pick up the new window.

### Org lifespan bounds on assignments (#307)

An org's lifespan end is **derived, not a column**: `v_org_lifespan(organization_id, ended_on)` takes the earliest non-archived `dissolved` / `merged_with` entity event, resolved to the *latest* date within the event's known precision (year-only 2023 → `2023-12-31`; month-only → last day of month) so closing an assignment at `ended_on` never claims an earlier end than the source supports. `renamed` / `split_from` imply continuity, not an end; an end event without a year (`merged_with` doesn't require one) derives no bound. `organizations.active` is dateless state and `archived_at` is admin bookkeeping — neither is a lifespan; an org marked inactive **should** also get an end event when the date is known.

**Invariant:** an assignment's window falls within its org's lifespan.

- Org ended → no `is_current=TRUE` assignment on its roles (hard).
- `start_date` / `end_date` ≤ `ended_on` when both known (contradiction otherwise).
- `is_current=FALSE, end_date NULL` = **unknown end**, not "ongoing" — allowed on an ended org; never invent an end date for it. Exclude these rows from "current members" displays (`is_current`, not `end_date IS NULL`, is the currency signal).

**Enforcement (deliberately app-layer, no DB trigger):** `src.core.org_lifecycle.check_assignment_lifespan(conn, role_id, *, is_current, start_date, end_date)` raises `AssignmentOutsideOrgLifespan` (codes mirror the audit categories); all three admin write surfaces call it (role-assignments section, role-detail inline rows, person-detail inline rows) and render `lifespan_error_message(exc)` inline. The public observation path is *not* gated — server-to-server writes record what the source asserts and `scripts/audit_org_lifecycle_assignments.py` reconciles (report mode lists violations; `--execute` closes `current_on_ended` rows at `ended_on` with a provenance note; contradictions and unknown-end rows are report-only). A cross-table temporal trigger would misfire on messy, out-of-order ingested history — revisit only after the audit runs clean. Complements the *role*-level `established_on`/`abolished_on` bounds (`_check_assignment_within_bounds` in `roles_shared.py`), which remain a pure-date check against the role row.

**UX:** org detail shows a warning banner when an archived/inactive/ended org still carries open assignments; marking an org inactive flashes the open count. Close/re-home flows ride on #266 / #305 tooling.

### Assignment observations — update semantics & provenance (#311)

The `(person, role, start_date)` match key is **identity, not payload**: a producer correcting a start date used to miss the key and mint a duplicate, and an auto-attach used to silently discard `end_date`/`is_current` deltas. #311 splits update authority across the two resolution modes:

- **PM-native (`pm_assignment_id`) = the authoritative update channel.** The producer proves it means exactly this row, so `update_assignment_fields` *replaces* stored values (supersedes the #289 NULL→dated-only backfill): `start_date` moves (never clears), an **explicit** `end_date: null` clears (reopen) while an omitted field leaves the bound alone (the handler passes `"end_date" in req.model_fields_set` — JSON null ≠ omitted), `is_current` is tri-state (`bool | None`, None = omitted). A resulting dated end with `is_current` omitted implies `FALSE`. Merged-state guards reject before touching the row: `is_current_end_date_conflict`, `start_after_end_date`, `start_date_conflict` (sibling unique-index collision), `source_key_mismatch`.
- **Natural-key auto-attach = safe enrichment + honest signaling.** Exactly one mutation is allowed: closing an open tenure (stored end NULL → supplied dated end, `is_current → FALSE` in the same UPDATE so `chk_current_no_end_date` holds). Every other delta is withheld and echoed back in `ObservationResponse.unapplied` (additive field; `None` on clean attaches) so the producer can stop retrying and escalate to a PM-native update. Never add fuzzy "same person+role, overlapping window" re-matching — non-consecutive terms of the same role are legitimate, and guessing which tenure a mismatched key means corrupts the other term.
- **Provenance:** `role_assignments.source_key_id` (the #162 pattern) is stamped on observation-created rows (both `resolve_assignment` NEW and `write_role_assignments`) and claimed via `COALESCE` on first authoritative update of a pre-#311 NULL row. Updates require `source_key_id IS NULL OR = caller` — admin surfaces are not gated (they operate on any row); only the observation API enforces source authority.
- **Duplicate cleanup:** `scripts/audit_assignment_duplicates.py` finds overlapping same-`(person, role)` dated pairs (`deepened_start` / `subsumed` auto-merge with `--execute`; `overlapping_review` is report-only). Merge = move side data to the survivor, concatenate notes, **archive** the orphan (never delete) — the archive UPDATE hits the outbox so subscribed producers drop stale anchors.

### Jurisdiction graph broadcast (#275)

`jurisdiction_relationships` and `organization_jurisdiction_affiliations` are curated from the admin (Phase 3); both propagate to the change feed so a jurisdiction subscriber sees graph edits (the public API exposes them: `GET /api/v1/jurisdictions/{id}/relationships` and the org read model's `jurisdiction_affiliations`).

- **Relationship edges:** any `jurisdiction_relationships` INSERT/UPDATE/DELETE fires `trg_touch_jurisdiction_on_relationship_change` → `touch_parent_jurisdiction()` bumps **both** endpoints' (`from_id` and `to_id`) `updated_at` → emits an `entity_changes` `'updated'` row per endpoint.
- **Org affiliations:** any `organization_jurisdiction_affiliations` INSERT/UPDATE/DELETE fires **two** touch triggers — `trg_touch_org_on_affiliation_change` (org) and `trg_touch_jurisdiction_on_affiliation_change` (jurisdiction) — so a subscriber on either side re-fetches.

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

#### Canonical name = the display pointer (issue #308)

`person_names.is_canonical` marks **the one name PM displays for a person**. Two constraints carry that meaning:

| Constraint | Guarantees |
|---|---|
| `uq_person_canonical_name` — `UNIQUE (person_id) WHERE is_canonical` | at most one canonical row per person |
| `chk_person_canonical_is_public` — `CHECK (NOT is_canonical OR visibility = 'public')` | the canonical row is always displayable |

This mirrors `uq_org_canonical_name`, which was itself narrowed from `(organization_id, name_type)` to `(organization_id)`. Person and org names now use the same model.

**Why it was re-keyed.** The previous key was `(person_id, name_type, COALESCE(locale,''), COALESCE(script,''))`, so one person could hold several canonical rows at once — a `legal` and a `preferred`, or a Latn and a Jpan `legal`. Consequences, all now gone:

- `v_person_display_names` had to disambiguate with `DISTINCT ON` plus a 13-entry `name_type` priority ladder, and returned duplicate rows per person whenever it didn't.
- A canonical row in one slot could block promotion in another. In particular a curated `legal_only` name (invisible to the view) could occupy a slot, leaving the person rendering blank with no way for the observation path to repair it.
- `is_canonical=TRUE` did not imply "this person displays" — a `deadname` row came back canonical but `legal_only`, because `trg_deadname_visibility` rewrites visibility *after* `is_canonical` is computed.

Nothing consumed the per-family meaning: every read is `ORDER BY is_canonical DESC` (show the main name first) or a wholesale demote on merge, and the admin promote path has always demoted person-wide. At the time of the change production held zero people with more than one canonical row and zero non-public canonical rows.

**Consequences for writers.** A `deadname` can never be canonical — `NEVER_CANONICAL_NAME_TYPES` in `src.core.observation` filters client hints for it, so an observation asserting one is ignored rather than failing the whole request on a `CheckViolationError`. Setting a new canonical in admin must demote the current one in the same transaction (`_names_shared` already does).

**Validate against the visibility that will land, not the one submitted.** `trg_deadname_visibility` rewrites a `deadname` row to `legal_only` *before* the write, so `name_type='deadname'` + `visibility='public'` + `is_canonical` passes any check that only inspects the submitted value and then violates `chk_person_canonical_is_public`. Admin's `_validate_canonical_visibility` therefore checks **`name_type` as well as `visibility`**, and callers pass the *effective* visibility — the stored value when a form omits the field, since `_update_name` leaves the column untouched rather than resetting it. Both name routes also map the constraint to a flash as a backstop.

**Every path that can strand a person without a display pointer must repair it.** Three call the same helper, `heal_person_canonical`: the observation path (including observations carrying no names), name deletion (`_maybe_promote_sole_name`, now a thin delegation), and merge (`merge_person_into`, which demotes the loser's canonical and so must heal the winner). The `#308c` backfill is the fourth repair path but runs its own set-based SQL — it does **not** call the helper; it shares the choice via `name_type_priority_sql()`, so all four still pick the same replacement row. The org name-delete path has its own equivalent heal (`orgs_names.py` — orgs have no visibility or eligibility exclusions, so it is just the ladder plus the guard).

Do not reintroduce a "promote only when exactly one name remains" shortcut — on either the person or the org side. That was the old delete-path rule, and it left a multi-name person (or org) blank whenever their canonical was deleted — the remaining names were perfectly displayable, nothing repaired them, and only a later observation happened to fix it.

The heal is best-effort and never aborts its caller: it runs in a savepoint and swallows `PostgresError`, because failing an observation over a cosmetic display-name repair would discard its links, addresses, role assignments and events. `UniqueViolationError` (a lost race) logs at debug; **everything else logs at WARNING** — `configure_logging` defaults to INFO, so a debug-only line would hide a typo'd statement or a revoked grant while callers still report success.

**Dedup person names on identity, not on the string.** A `legal` row and an `mrz` rendering can carry identical text while being different claims, as can a Latn and a Jpan row; matching on `name` alone silently destroys the second. `write_names` uses the full `(name, name_type, locale, script)` key.

`merge_person_into` uses a deliberately looser variant: text + **visibility** + locale + script must match, and then **either** the `name_type`s are equal **or** both are ordinary display types. Consolidating two records that were each split into `legal` + `variant` would otherwise leave the winner holding the same string as both — redundant rather than two claims. `NO_AUTO_CANONICAL_NAME_TYPES` (`mrz`, `reading`, `romanization`, `deadname`) are never interchangeable: identical text in one of those is a machine-readable rendering, a distinct claim from a display name.

`visibility` is compared on **both** branches, and that is load-bearing twice over. Without it a `hidden` winner row absorbed a `public` loser row carrying the same text; since the loser's canonical is demoted immediately beforehand, that deleted the only promotable name and left the merged person blank — defeating the heal that runs a few lines later. It also silently destroyed `legal_only` claims, breaking the #121 guarantee that the winner inherits the loser's restricted names.

#### Name families are edges, not shared slots (`reading_of_id`)

Re-keying the canonical index costs nothing, because **PM does not model "the same name written differently" by grouping rows that share `(name_type, locale, script)`** — it models it with an explicit FK.

`person_names.reading_of_id` points a `reading` (furigana) or `romanization` (pinyin, romaji) row at the name row it renders, `ON DELETE CASCADE` — a reading cannot outlive its source. So a Japanese legal name and its romaji rendering are:

- **two rows**, of **two different `name_type`s** (`legal` and `romanization`),
- **joined by an FK edge**, not by an implied grouping,
- of which **exactly one is the display pointer**.

That was already true before #308 and is unchanged by it. The old per-family canonical key was not what expressed the relationship; `reading_of_id` was, and still is. What the old key actually permitted was a *second* `legal` row differing only by locale/script also being canonical — which is the same content in two scripts, i.e. precisely the case `reading_of_id` + `name_type='romanization'` is designed for. Model it that way.

Admin support for the edge: `people_reading_target_search.py` powers the "reading of" picker; `_name_row.html` / `_name_form_row.html` surface the parent name.

#### Canonical auto-promotion on observation (#308)

`write_names` guarantees that a person with an eligible name ends up displayable, symmetric with the long-standing org behaviour:

- **Client hint present** (`is_canonical=true` on some name) — that name claims the slot, guarded by `NOT EXISTS (… person_id AND is_canonical)`; never displaces an existing canonical. A hint on a `deadname` is ignored (`NEVER_CANONICAL_NAME_TYPES`) rather than raising `CheckViolationError` and failing the whole observation.
- **No hint** — PM auto-promotes exactly one name per write, picked by `_PERSON_NAME_TYPE_PRIORITY` (`preferred` > `legal` > `alias` > …). `NO_AUTO_CANONICAL_NAME_TYPES` (`deadname`, `mrz`, `romanization`, `reading`) is never auto-promoted.

Eligibility is by **identity, not name string**: the promotion target is an index into the payload, and the append-dedup key is `(name, name_type, locale, script)`. Matching on the bare string let an `mrz` row claim the display slot ahead of a `legal` one purely by list order, and silently discarded the second claim.

Clients are not required to assert `is_canonical` — omitting it is the correct conservative default when the client can't tell whether it is creating a new person or matching an existing one. The `NOT EXISTS` guard makes displacement impossible either way.

**Heal on re-observation.** Auto-promotion fires only on *newly inserted* rows, and `write_names` skips names that already exist — so a person already canonical-less would never recover, since the steady-state client re-sends the same names every sync. `write_names` therefore ends the person branch with `_heal_person_canonical`, promoting the highest-priority eligible existing name whenever the person has no canonical. It also runs for observations carrying **no names at all**, so any observation touching a blank person repairs it, and is skipped when an insert in the same call already claimed the slot.

The heal is a read-only probe plus a guarded `UPDATE`. The probe cannot violate a constraint, so the steady-state case (already-displaying person, unchanged names) stays at one round trip with no savepoint. The `UPDATE` takes a savepoint: a concurrent commit between the two statements can still collide on `uq_person_canonical_name`, and without recovery that error propagates out of `write_names` and aborts the whole observation, which the public route reports as `db_constraint_violation` — discarding links, addresses, role assignments and events over a cosmetic display-name repair.

`scripts/backfill_person_canonical_names.py` repairs people who predate this, selecting with the **same ladder** so both repair paths choose the same row (`test_backfill_matches_heal_choice`).

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

## API request log (#260)

`api_request_log` is an append-only observability record of public API traffic —
one row per `/api/v1/*` request. It backs the admin **Activity › API Requests**
screens and the dashboard API-activity panel.

- **Capture** — `src.api.public.middleware.RequestLogMiddleware`, a **pure-ASGI**
  middleware (not `BaseHTTPMiddleware`, so it can tee request+response bodies
  without breaking downstream `.json()` reads). Registered in `src/api/main.py`
  via `app.add_middleware`. Non-`/api/v1/*` paths early-return uncaptured.
- **Identity** — read from `request.state.api_key_id`, which the auth deps
  (`require_api_key` / `require_key` / `require_scope`) stash on a successful key
  resolve. Unauthenticated/invalid requests (401/403) log a row with a NULL key.
- **Enrichment** — for the `observations` and `changes` route groups only, the
  middleware parses the response body into structured columns (`disposition`,
  `result_entity_id`, `entity_type`, `reason`; `item_count`, `is_empty`). Any
  other group, or a non-JSON body, logs generic metadata only.
- **Fidelity / PII** — raw request/response JSONB bodies are stored **only for
  the `observations` and `changes` groups** (the ones the log surfaces); all
  other v1 traffic records structured metadata only and its bodies are never even
  buffered (avoids memory + JSONB bloat from e.g. large embedding vectors).
  Observation payloads carry PII (names, addresses, contacts); the 90-day
  retention window bounds that footprint, and the admin list surface shows
  metadata only — bodies are confined to the detail view (admin-authed).
- **`result_entity_id` has no FK** — the referenced entity may be hard-deleted or
  merged; the log record must survive. The UI resolves it to an admin link and
  shows "(removed)" on a 404.
- **Best-effort** — a capture failure is swallowed and logged; observability must
  never break the request path. The row write is **fire-and-forget** (#262):
  params are built on the request tail, then the pool-acquire + INSERT run on a
  background `asyncio` task so capture never adds to request latency. Graceful
  shutdown drains in-flight writes before the pool closes (#286,
  `drain_pending_writes`); a hard crash (SIGKILL/OOM) drops them. Under load the
  in-flight set is **bounded** by `API_REQUEST_LOG_MAX_PENDING` (#290): above the
  cap, incoming writes are *shed* (drop-newest, keeping the older writes closest
  to landing) rather than piling onto `pool.acquire()`. So `api_request_log` can
  legitimately under-count during sustained high traffic even with zero errors —
  shed volume is counted and surfaced via a rate-limited `WARNING`.
- **Retention** — pruned on the same 90-day window as the outbox by
  `scripts/prune_outbox.py` (see `docs/COMMANDS.md`).
- **Anomaly surfacing** (#294) — per-key aggregates via
  `src.core.anomaly.key_activity` back both the admin per-key panel and the
  hourly `power-map-anomaly.timer` check (`scripts/check_api_anomalies.py`,
  WARNING + exit 3 at/above `API_ANOMALY_HOURLY_THRESHOLD` req/hr, `<= 0`
  disables; see `docs/COMMANDS.md`). Threshold is deliberately below the
  rate-limit ceiling — rationale in `src/core/anomaly.py`.

## Unique Indexes (PostgreSQL 15+)

- Role uniqueness is **split by whether the role has a jurisdiction** (#261):
  - `uq_role_structural` — `roles(organization_id, role_type_id, jurisdiction_id, qualifier) NULLS NOT DISTINCT WHERE jurisdiction_id IS NOT NULL AND archived_at IS NULL` — the identity of a role with a jurisdiction is chamber + role type + district + position; `NULLS NOT DISTINCT` makes a NULL `qualifier` unique per district (one senator).
  - `uq_role_org_title` — `roles(organization_id, lower(title)) WHERE jurisdiction_id IS NULL AND archived_at IS NULL` — a role without a jurisdiction keeps title-based identity.
  - A role with a jurisdiction must name its role type: `chk_role_jurisdiction_needs_role_type` (`jurisdiction_id IS NULL OR role_type_id IS NOT NULL`).
- `uq_role_assignment_person_role_start` — `role_assignments(person_id, role_id, start_date) NULLS NOT DISTINCT WHERE archived_at IS NULL`
- All created inside `DO … EXCEPTION WHEN unique_violation` blocks so `apply_schema` is safe on DBs with existing duplicates (logs WARNING instead of raising)
- Schema tests for `uq_role_org_title` are guarded by a `require_uq_role_org_title` fixture that skips when the index is absent
- `deduplicate_roles.py` collapses in two passes matching the split: roles without a jurisdiction by `(organization_id, lower(title))`, roles with a jurisdiction by `(organization_id, role_type_id, jurisdiction_id, qualifier)` — it never merges distinct roles that share a title
- **Titles of roles with a jurisdiction are PM-curated (#267):** `title` is not part of such a role's identity (matching keys on the tuple), so the observation may omit it — on create `resolve_role` synthesizes the canonical title from the tuple via `src.core.role_title` (the single formatter, shared with `scripts/generate_wa_roles.py`) and **prefers it over any supplied title**, so upstream observers can't drift PM's curated form. A supplied title is used only as a fallback when it can't be synthesized; a titleless role that can't be synthesized (unknown `role_type` / non-`usa-wa-ld` district) is rejected `role_title_unavailable`.
- **`role_types` catalog is publicly observable (#268):** `GET /api/v1/role-types` returns the classifier vocabulary (`id, slug, display_name, expects_jurisdiction, requires_qualifier`) so producers discover the `role_type` slugs instead of hardcoding them. `expects_jurisdiction` is an **advisory** hint that the office is normally attached with a jurisdiction — `resolve_role` does not enforce it. An unknown `role_type` slug is already rejected (`role_type_not_found`), so the endpoint's role is to prevent a *valid-but-wrong* slug (which would mint a duplicate role).
- **`requires_qualifier` is enforced (#273):** a per-position office (e.g. `state_representative` — House seats are Position 1/2) sets `requires_qualifier=TRUE`; `resolve_role` **rejects** a jurisdictional observation of it with a missing/blank `qualifier` (`qualifier_required`) rather than minting a positionless seat (the #267 spurious-mint). `state_senator` is `FALSE` (one per district — `NULLS NOT DISTINCT` keeps the NULL-qualifier senator unique). Unlike `expects_jurisdiction`, this is a hard guard, not a hint. **Defense in depth:** a DB trigger (`trg_role_requires_qualifier` → `enforce_role_requires_qualifier()`) enforces the same rule at the data layer, so admin/direct-`INSERT` paths that bypass `resolve_role` also can't mint one (raises `check_violation`). Can't be a `CHECK` — it references `role_types`.

**Bootstrap sequence for a dirty DB:** (1) run `scripts/deduplicate_roles.py --execute` to collapse duplicates, (2) re-run `apply_schema` (or restart the service) to create the indexes, (3) verify with `\d roles` / `\d role_assignments` in psql.

**Inline constraint additions need a companion DO block (#307 CR round 2).** `CREATE TABLE IF NOT EXISTS` no-ops on an existing table, so a `CHECK`/`CONSTRAINT` added inline to the CREATE reaches only fresh DBs — prod silently lacked `entity_events_event_year_check` and `chk_at_requires_year` for exactly this reason. Any inline constraint change must ship with an idempotent `DO $$ … $$` migration that ADDs the constraint when absent (guard on `pg_constraint` by `conrelid`/`conname`) and replaces it when the clause is stale, wrapped in `EXCEPTION WHEN check_violation` → `RAISE WARNING` so `apply_schema` survives dirty data. Verify against prod (`pg_get_constraintdef`), not just the test DB — the test DB's table may be young enough to have gotten the inline form. Reference: the `entity_events` reconciliation block in `schema.sql`; regression harness: `tests/core/test_schema_constraint_migrations.py` (drop constraint → `apply_schema` → assert restored).

**ADD-when-absent, not just replace-if-stale (#312).** A replace-if-stale guard (`IF EXISTS (constraint AND check_clause NOT LIKE '…') THEN DROP+re-add`, the #168 idiom) no-ops when the constraint is *entirely absent* — so it never heals a table that predates the constraint. A #312 prod-vs-test sweep caught five more in that state (`field_confidence_entity_type_check`, `import_provenance_entity_type_check`, and the three `import_batches_*_count_check`s): the entity_type pair had only the #168 replace-if-stale guard, the count checks had no reconciliation at all. Their #312 blocks ADD the current full shape when absent (guard on `pg_constraint` presence). Note the contrast: #176's unconditional `DROP CONSTRAINT IF EXISTS + ADD` heals absent *and* stale in one statement (that's why `contact_methods`/`entity_addresses` never drifted), but it hard-aborts `apply_schema` on a violating row — prefer the `EXCEPTION WHEN check_violation → RAISE WARNING` DO-block form on any constraint that could meet dirty data at deploy time.

### Role-type vocabulary — governance (#266)

`role_types` is a **flat, global aggregation key** — its stated purpose is to let "all X across orgs/jurisdictions" aggregate without matching free-text titles. It is *not* a label for every distinct office. New slugs are gated by four rules so the catalog doesn't proliferate into a WA-flavored, domain-ambiguous grab-bag:

1. **Aggregation test — a slug exists only if you would query "all of them" across orgs/jurisdictions.** If the answer is no, it stays a free-text `roles.title` and gets no type. A one-off, non-recurring position doesn't earn a type until it recurs; a coarse *category* whose members you'd query together (`chamber_officer` covers a chamber's institutional officers — Secretary of the Senate, a future Chief Clerk) does earn one even at one row today, because the category — not the single title — is the aggregation. This is the primary guardrail against proliferation.
2. **Domain-prefix convention.** Every non-jurisdictional slug is prefixed by the org-kind it attaches to: `committee_`, `chamber_`, `legislature_`, `party_` (corporate/advocacy `org_*` is reserved for #303). The prefix disambiguates a bare noun that means different things per domain (a `member` on a committee vs a party) and yields coarse rollups for free (`WHERE slug LIKE 'legislature_%'`) — so you rarely need a separate coarse type. The two jurisdictional seat types (`state_representative`, `state_senator`) predate this convention and are **grandfathered unprefixed** — renaming a public-API-visible slug (`GET /api/v1/role-types`) is breaking and buys nothing.
3. **Concept in the type, jurisdiction label in the renderer.** Types stay jurisdiction-neutral; WA-specific titles live in `src/core/role_title.py`. Don't mint `wa_speaker` — the type is the concept, the label is rendered (or, for coarse types, carried by the free-text title).
4. **Coarse where a long tail exists.** When distinct titles share a domain but few would ever be aggregated individually (the committee-staff tail: `Counsel`, `Research Analyst`, `Fiscal Coordinator`, …), use one coarse type (`legislature_staff`) + the specific free-text title, not a slug per title. Coarse types set `expects_jurisdiction=FALSE`; the specific office is not structurally recoverable (accepted tradeoff — e.g. `chamber_leader` aggregates "all chamber leaders" but not "all Speakers cross-state").

**Reserved, not seeded.** Obvious near-term peers of a seeded concept (`chamber_majority_leader`, `chamber_minority_leader`, `chamber_president_pro_tempore`, `chamber_speaker_pro_tempore`) are documented here but **seeded only on first observation** — don't front-load the vocabulary ahead of data (rule 1).

**Current catalog (post-#266):** `state_representative`, `state_senator` (jurisdictional seats, grandfathered); `chamber_leader`, `chamber_officer` (coarse); `committee_chair`, `committee_vice_chair`, `committee_ranking_member`, `committee_assistant_ranking_member`, `committee_member`; `legislature_staff` (coarse); `party_member` (coarse). The pre-#266 coarse `member` was split into `committee_member` + `party_member` and dropped.

### Entity search — last-token prefix FTS (#316)

Every `search_tsv @@ …` predicate (orgs, people, roles, role-assignments — both the public `search` endpoints and the admin list `*_queries.py` filters) goes through **`pm_prefix_tsquery(cfg, q)`**, never bare `plainto_tsquery`. It is the single source of truth for query-side FTS: it reuses `plainto_tsquery` for normalization (unaccent, punctuation split, stopwords) and appends `:*` to the last lexeme so the trailing token matches as a **prefix** — `"Ollie Gar"` matches `"Ollie Garrett"`, and single-token queries prefix too (`"Ja"` → `"Jane …"`). Prefix-only, not infix. It is `IMMUTABLE STRICT` (NULL in → NULL out, so nullable-`q` call sites like orgs-by-jurisdiction keep working) and injection-safe (plainto strips all tsquery operators before the `::text` round-trip). Known limitation: a partial *hyphenated* final token doesn't prefix-match (the `<->`-linked compound is required exact) — see `docs/PUBLIC_API.md`. When adding a new searchable entity, use `pm_prefix_tsquery` for the `@@` predicate; do not re-inline `plainto_tsquery`. Jurisdictions are the deliberate exception (ILIKE on name/slug, no `search_tsv`).

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
