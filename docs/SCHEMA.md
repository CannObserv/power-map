# power-map — Database Schema & Invariants

Table and column conventions, display-name views, temporal validity windows, and the
unique indexes that encode entity identity. Observation write semantics live in
`docs/OBSERVATIONS.md`; person-name storage and visibility in `docs/NAMES.md`.

---

## DB key rules at a glance

One-line-per-rule index of the DB domain — read it first, then follow the entry
you need. An entry ending `Full rules → …` names the doc and section that hold
them; since #428 that is never this file, and since #518 these lines are
**pointers, not summaries**: each carries the rule's name and the one fact that
decides whether you need to open the target, and nothing more.

- Route handlers acquire connections via `Depends(get_db)` only — never `src.core.db.acquire()`, which escapes `app.dependency_overrides` and breaks test isolation. Sole route exception: `GET /ready` (#343), which must probe the real pool.
- Entity search (#316): every `search_tsv @@ …` predicate goes through `pm_prefix_tsquery(cfg, q)`, never bare `plainto_tsquery`; jurisdictions are the ILIKE exception. Full rules → `docs/SCHEMA_INDEXES.md` §"Entity search — last-token prefix FTS".
- Roles — structural identity: `role_type_id` + `jurisdiction_id` + `qualifier`; a plain role leaves all three NULL. Uniqueness is **split** by whether a jurisdiction is present (`uq_role_structural` vs `uq_role_org_title`), and `title` is required on observations but is *not* identity for a typed role. `requires_qualifier` / `forbids_qualifier` are enforced at both the app and DB layers (#273/#302). Full rules → `docs/SCHEMA_INDEXES.md` §"Unique Indexes".
- Role-type vocabulary is **governed** (#266): a slug earns a row only if you would query "all of them" across orgs — otherwise it stays a free-text `roles.title`. Non-jurisdictional slugs are domain-prefixed by org-kind; types stay jurisdiction-neutral. Full rules → `docs/SCHEMA_INDEXES.md` §"Role-type vocabulary — governance".
- Org lifespan bounds (#307): org end is `v_org_lifespan.ended_on`, derived from entity events — `active` / `archived_at` are **not** lifespans. Assignment window ⊆ org lifespan, enforced app-layer only; `is_current=FALSE` with a NULL `end_date` means *unknown end* and is never invented. Full rules → `docs/OBSERVATIONS.md` §"Org lifespan bounds on assignments".
- Org parent — authoritative reparent (#334): PM-native (`pm_org_id`) *replaces* the stored parent; a natural/external-identifier match only writes when NULL. Reparent needs `source_key_id IS NULL OR = caller`. Full rules (incl. the #499 applier's second door) → `docs/OBSERVATIONS.md` §"Org parent — authoritative reparent & provenance".
- Merge dedup — role_assignment ancillary (#324): four polymorphic tables key on `(entity_type, entity_id)` with **no FK**, so every merge conflict-delete must call `rehome_conflicting_assignment_ancillary` **before** the DELETE or strand them. Full rules → `docs/ANCILLARY.md` §"Merge dedup — role_assignment ancillary re-homing".
- Ancillary `entity_changes` emit (#327): the parent 'updated' signal lives in **DB touch triggers**, not app code, so every write path signals uniformly. Never emit from app code for contacts/links/identifiers/citations — the trigger already fired, and a manual emit double-signals. Ingestion telemetry (`field_confidence`, `import_provenance`) is the trigger-less exception. Full rules → `docs/ANCILLARY.md` §"Ancillary `entity_changes` emit — DB touch triggers".
- Citations (#319): polymorphic no-FK ancillary over 7 citable types, holding human-checkable evidence for a fact — a fifth provenance axis. Identity is `(entity_type, entity_id, field_name, url)` `NULLS NOT DISTINCT` over active rows; observable and retractable like events. Full rules → `docs/OBSERVATIONS.md` §"Citations — write semantics". Design: `docs/plans/2026-07-29-citations-pattern-design.md`.
- Role-assignment relationships — RA→RA edges (#301): directional, temporal, **FK-backed** staffer→principal edges. The temporal invariant (edge ⊆ both endpoints) is app-layer only on admin writes — the observation path records freely (#307's pattern) — while a DB cascade clamps or archives dependents when an endpoint shrinks. Carries its **own** change-feed `entity_type`, and merge must re-home edges before the assignment hard-delete or FK CASCADE drops them silently. Full rules → `docs/OBSERVATIONS.md` §"Role-assignment relationships". Design: `docs/plans/2026-08-01-assignment-relationships-design.md`.
- Person-name visibility (deadnames, hidden, legal-only) → `docs/NAMES.md` §"Person names — i18n & cultural awareness".
- Person **canonical/display** name (#308): `person_names.is_canonical` is the display pointer — one per person, always `visibility='public'`, so `v_person_display_names` is a plain join with no priority ladder. A `deadname` can never be canonical. Every path that could strand a person without a pointer calls `heal_person_canonical`. Name families are the `reading_of_id` FK, never a shared canonical slot. Full rules → `docs/NAMES.md` §"Canonical name = the display pointer".
- Structured name parts: `person_name_parts` is a 1:0..1 sidecar to `person_names`, never auto-written — only an upstream source or a human confirmation populates it. Full rules → `docs/NAMES.md` §"Structured parts (`person_name_parts` sidecar)".
- BCP 47 / ISO 15924 lookups (`bcp47_locales`, `iso15924_scripts`) FK-validate `person_names.locale` / `.script`; seed them after a fresh `apply_schema` (`apply_schema` WARNs when either is empty). Seed command → `docs/COMMANDS.md`.
- Inline constraint drift (#307/#312/#315/#392): `CREATE TABLE IF NOT EXISTS` no-ops, so an inline `CHECK`/`FK`/`ON DELETE` change added after a table shipped never reaches an existing DB — ship an idempotent reconciliation `DO` block with it, and place a *backfilling* block before that table's `set_updated_at()` trigger. Continuous guard: daily `power-map-schema-parity.timer`. Full rules → `docs/SCHEMA_INDEXES.md` §"Unique Indexes (PostgreSQL 15+)".
- Seeded lookups — slug reconciliation (#458): every seeded lookup pairs a ULID PK with a UNIQUE slug, and a bare `INSERT … VALUES` aborts on an operator-created duplicate slug — taking `ExecStartPre` and the service start with it. Stage rows in `_seed_<table>` and call `reconcile_seeded_slugs()` first. Full rules → `docs/SCHEMA_INDEXES.md` §"Seeded lookups — slug reconciliation".
- Integration test fixtures acquire from the session-scoped `db_pool`, never `asyncpg.connect`; endpoint tests use the lifespan-less rollback client (#288). Both recipes, and the `loop_scope="session"` gotcha → `docs/TESTING.md` § Endpoint-test client.
- A11y test tiers — static lint → rendered lxml sweep → real-browser axe sweep, all three sharing one route enumeration in `tests/api/admin/admin_routes.py` (never re-derive). The browser tier is marker-gated and never runs in pre-commit. Full rules → `docs/TESTING.md` § Browser Testing.


## Core rules


- `apply_schema(conn)` is idempotent (`IF NOT EXISTS` / `ON CONFLICT DO NOTHING`); wraps in a transaction
- `updated_at`: maintained by DB triggers — never set manually in application code
- FKs into `api_keys` (#543): `ON DELETE CASCADE` for rows the key owns (scopes, subscriptions), `ON DELETE SET NULL` for rows it only sourced (every `source_key_id`, embeddings' `created_by_key_id`) — never the default NO ACTION, which makes a key undeletable (the admin delete 500s). Sweep: `tests/core/test_schema_api_key_references.py`
- Phone: normalize to E.164 via `PhoneNormalizer` from `src.core.normalizers.phone`
- Email: validate via `EmailNormalizer` from `src.core.normalizers.email`
- Integration tests (marked `integration`) require `TEST_DATABASE_URL` env var; `tests/conftest.py` redirects `DATABASE_URL` → `TEST_DATABASE_URL` and skips when absent — never runs against the production DB
- Integration test fixtures share a session-scoped `db_pool` (`asyncpg.create_pool`) from `tests/conftest.py`; `apply_schema` runs once at session start. Fixtures and tests acquire via `async with db_pool.acquire() as conn:` — never `await asyncpg.connect(...)` per call. Reference recipe: `tests/api/admin/test_people_names.py`.
  - Required markers in every consumer module: `pytestmark = pytest.mark.integration` at module level, and `@pytest_asyncio.fixture(loop_scope="session")` on every async fixture.
  - Fixture `loop_scope="session"` is load-bearing: `db_pool` is bound to the session event loop. The test-function asyncio mark is no longer needed — `asyncio_default_test_loop_scope = "session"` in `pyproject.toml` sets the default globally.
  - Sole exception: `tests/core/test_db.py` tests `src.core.db.get_pool()` / `create_pool()` lifecycle itself and intentionally owns its own connection.
  - Teardown for entities referenced by a polymorphic side table (e.g. `addresses` via `entity_addresses`): fetch the side-table's foreign-key ids before dropping the join rows, then delete the entity rows guarded by `NOT EXISTS` so a shared row can't FK-fail. Wrap the read+writes in a single `async with conn.transaction():` so a mid-teardown failure rolls back cleanly. Pair with a module-scoped autouse fixture that snapshots the entity table's rowcount before/after and asserts equality — catches leaks if someone later "simplifies" the teardown. Reference: `tests/api/admin/test_orgs_addresses.py` (#150).

---

## Display names


- Org: use `v_org_display_names` for all queries displaying an org name — formats as "Name (Acronym)" when a canonical acronym exists, otherwise just "Name". Never join `organization_names` or `organization_acronyms` directly for display
- Person: use `v_person_display_names` — returns the canonical `person_names` row filtered to `visibility='public'`. The view exposes `display_name` (visible string) and `sort_key` (`COALESCE(sort_as, name)`, Phase 2b #123). For person ORDER BY, use `sort_key COLLATE "und-x-icu" NULLS LAST` so diacritics order locale-aware (Å near A) and any `sort_as` override is honored. See "Person names — i18n & cultural awareness" below. Never join `person_names` directly for display.
- Acronyms in `organization_acronyms` (separate table); `organization_names` holds legal/dba/former names only. Each table has exactly one canonical row per org via a partial unique index

---

## Links schema


`link_types` table holds (slug, display_name, is_social). `links` table holds (entity_type, entity_id, url, link_type_id, is_active). Social links: `JOIN link_types WHERE is_social = TRUE`.

**Natural-key uniqueness** (`uq_links_entity_url`, issue #142): an entity must not carry the same URL twice for the same `link_type_id`. `is_active` is intentionally excluded from the index — keeping both an active and an archived copy of the same URL is not a supported state. Ingestion pipeline writes use `ON CONFLICT (entity_type, entity_id, url, link_type_id) DO NOTHING` so re-runs are idempotent. Admin CRUD (create + update) catches `asyncpg.UniqueViolationError` and returns 409 with a `warning` flash; never bubbles as a 500.

---

## Producer crosswalk (#495)


`producer_crosswalk` maps a snapshot producer's ids to the PM rows they name. It is the **row scope** for the dataset applier (#490) and, for roles and assignments, their only per-row producer handle — `source_key_id` cannot serve either duty: `people` and `roles` lack it, and few organizations and only half of usa-wa's assignments carry it.

- Keyed `(source, kind, producer_id)`; `source` is `usa_wa` for every row — one constant, `PRODUCER_SOURCE` in `src/core/ingestion/crosswalk.py`, that the seed writes and the applier scopes by; `kind` is the producer's vocabulary (`person|organization|role|assignment`), which is **not** PM's tombstone vocabulary — an `assignment` walks `role_assignment` tombstones
- `exported_pm_id` is what the producer sent; `pm_id` is where it leads after merge history is walked, NULL **exactly** when `resolution` is unresolvable (`deleted_no_successor`, `missing`, `cycle`) — a CHECK holds the two in step. The producer side splits alike (#525): `exported_producer_id` is the anchor id as exported, `producer_id` the dataset's key (`span_key` for an assignment); the seed matches on the former
- Keeping both makes the seed re-runnable: re-running a stale export after a new merge updates the resolution in place
- Resolution walks `deleted_entities.merged_into`, whose target is *that row's* survivor (`docs/MERGE.md`). Tombstones are pruned at 90 days, so an older merge resolves `missing`: "PM cannot say", never "it never existed"

**The applier's scope query is `resolution IN ('live', 'merged')`.** An `archived` row is in the table but not in scope — writing onto a soft-deleted row is the #481 hazard, and `archived_at` writes no `deleted_entities` row, so the merge walk cannot follow it. Where PM's duplicate audit archived the producer's span and kept a deepened one under a **new** ULID the producer has never seen, the seed reports the live sibling as a supersession candidate; re-pointing is a triage decision (#501), never the seed's.

Seeded by `scripts/seed_producer_crosswalk.py` (`docs/RUNBOOKS.md`). The applier (#499) is the table's second writer: a `create` it applies mints the entity row and a crosswalk row for it in the same transaction — `exported_pm_id = pm_id =` the new ULID, `resolution = 'live'`, export fields null. It edits an existing row only for what it applied: a merge (#514) re-points each anchor naming the retired row at the survivor as `merged` (`repoint_anchors`); an archive (#527) stamps `retracted_at`, which its restore clears, so it restores only its own archives.

## Curation overlay (#497)


`curation_overlay` holds PM-wins overrides on **producer-owned** fields — the slice of each row the dataset applier (#490) would otherwise rewrite from the snapshot. The mapping models join it declaratively, `COALESCE(overlay.value, mapped.value)`, so PM state for that slice is f(snapshot, overlay): idempotent, replayable, and diffable before any write. Everything a producer does not publish stays direct curation on the entity row.

- Keyed `(entity_type, entity_id, field)`, unique per *active* row — unpin sets `archived_at`, keeping the row as history (#498); models read active rows only
- `entity_type` is the producer crosswalk's `kind` vocabulary (`person|organization|role|assignment`): the rows it can override are exactly the crosswalk's scope. No FK on `entity_id` (polymorphic, like `links`)
- `value` is **nullable on purpose** — a curator can assert a producer-owned field should be empty
- `field` is free text at the table; the mapping project's `overlay_field_unmapped` test enforces the vocabulary **per entity type** (`person.name`; `organization.parent_id|legal_name|acronym|dissolved_year`; nothing yet for `role`/`assignment`), so an override naming a field no model maps for its type is **warned by name and applied nowhere** — never silently ignored or misapplied, never halting the build. The admin offers exactly those pairs → `docs/ADMIN_OVERLAY.md`
- `created_by` (pinner), `archived_by` (unpinner; NULL if merge-displaced) → `app_users`, `ON DELETE SET NULL`

Written by the admin (#498) and merges; read only via `scripts/export_pm_tables.py`, which hands the models Parquet so they never open a database connection.

---

## Moved out

- `docs/SCHEMA_INDEXES.md` — the unique indexes that encode identity, role-type
  vocabulary governance (#266), entity-search FTS (#316), `pg_trgm`
- `docs/SCHEMA_VALIDITY.md` — org name effective dates (#239), address validity
  windows (#181), jurisdiction graph broadcast (#275)
- `docs/NAMES.md` — the org auto-promote invariant and last-identity guard
