# Citations / source provenance — design (#319)

**Status:** approved 2026-07-29. Supersedes the ad-hoc `.notes` provenance stopgap (#314) and the human-visible interim (#318).

## Goal

A coherent, durable pattern for "where did this fact come from" across the graph:
a first-class, observable **citation** attached to an entity or one of its fields,
carrying human-checkable evidence (URL + title + excerpt + accessed-date).

## Audit — provenance today (context)

Four provenance axes already exist; **none is a citation**:

| Axis | Mechanism | Answers | Granularity |
|---|---|---|---|
| Actor | `source_key_id` (`role_assignments`, `entity_events`, `person_names`, `organization_names`, embeddings) | which producer wrote it | row |
| Ingestion | `import_provenance` + `import_batches` (`batch_id`, `source_row`, `raw_data`) | which bulk load / CSV row | row |
| Confidence | `field_confidence` (`source_reliability`, `validation_status`, per `(entity, field)`) | how sure / validated how | field |
| **Citation** | **none** — stand-in is free-text `.notes` (#314) | public evidence a human can check | — |

`links` carries source-flavored `link_type`s (`wa_pdc`, `sec_form_d`, `wikipedia`) but those are
**entity web-presence**, not fact citations, and `person_names`/`entity_event` aren't even in its CHECK.

## Decision: dedicated `citations` table (not a `links` link_type)

A `source` link_type was evaluated and rejected on five counts:
1. **Granularity** — `links` keys on `(entity, url, link_type)`; citations target a *field*. Its unique index would collapse two facts citing the same URL.
2. **Attributes** — citations need `accessed_at`, `excerpt`, `title`; `links` has none.
3. **Wrong surface** — a `source` link_type surfaces in the entity web-presence panel (admin + public links response). A Wikipedia cite for a birthdate is not a "profile link."
4. **Rendering** — citations render as inline footnotes next to the fact; links render as a panel.
5. **Coverage** — `person_names`/`entity_event` need citing but aren't in the `links` CHECK.

`citations` stays **separate from `field_confidence`** (curated human evidence vs. automated ingestion
telemetry) though both share the `(entity, field)` spine.

## Data model

```sql
citations (
  id            TEXT PRIMARY KEY,           -- ULID generate_id()
  entity_type   TEXT NOT NULL CHECK (entity_type IN
                  ('organization','person','role','role_assignment',
                   'jurisdiction','person_name','entity_event')),
  entity_id     TEXT NOT NULL,              -- polymorphic, NO FK (ancillary convention)
  field_name    TEXT,                       -- NULL = whole-entity cite; else CITABLE_FIELDS
  url           TEXT,                        -- nullable (offline sources)
  title         TEXT,                        -- human label; required iff url NULL
  excerpt       TEXT,                        -- supporting quote (mutable payload)
  accessed_at   TIMESTAMPTZ,                 -- retrieval date (mutable payload)
  source_key_id TEXT REFERENCES api_keys(id) ON DELETE SET NULL,
  archived_at   TIMESTAMPTZ,                 -- soft-delete, never hard-delete
  created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),  -- maintained by trigger
  CHECK (url IS NOT NULL OR title IS NOT NULL)
);

CREATE INDEX idx_citations_entity ON citations(entity_type, entity_id);

-- Identity — active rows only. NULLS NOT DISTINCT: a NULL url (and a NULL
-- field_name) is one distinct slot, so at most one URL-less citation per
-- (entity, field). Same trick role_assignments uses for a NULL start_date.
CREATE UNIQUE INDEX uq_citation_identity
  ON citations (entity_type, entity_id, field_name, url) NULLS NOT DISTINCT
  WHERE archived_at IS NULL;
```

## Identity & observation lifecycle (mirrors events #321/#322)

- **Identity** = `(entity_type, entity_id, field_name, url)`, `NULLS NOT DISTINCT`.
  `title` / `excerpt` / `accessed_at` are **mutable payload**, never identity.
- **observe** (`op="observe"`, default):
  - no `pm_citation_id` → match identity → **refine in place** if found (diff-before-write
    no-op → `auto-attached`, no `updated_at` bump), else **create** (`new`).
  - with `pm_citation_id` → refine that row; identity fields (`entity`, `field_name`, `url`)
    are immutable → `identity_immutable` on a mismatch.
- **retract** (`op="retract"`): id-addressed (no `pm_citation_id` → `invalid`); archives
  (`archived_at`, never hard-delete) → `retracted`. Supplied `url` + `field_name` must match the
  stored row (`identity_immutable` guard, catches a copy-paste id). Already-archived → diff-gated
  no-op `auto-attached` (no clock bump).
- **Provenance gate:** `source_key_id` stamped on create; refine/retract require
  `source_key_id IS NULL OR = caller` (claimed via `COALESCE`) → else `provenance_conflict`.
  Admin surfaces are ungated.
- **Anti-resurrection:** the create-path content-dedup sees archived rows — re-observing a
  retracted citation returns `auto-attached` and stays retracted; un-retract is a deliberate admin
  unarchive, not a side effect of re-observation. (Same posture as #322.)

## `field_name` governance

- Per-entity-type **`CITABLE_FIELDS`** allowlist in core (`src/core/`). `NULL` always allowed
  (entity-level cite). An observed non-NULL `field_name` not in the set → reject
  `citable_field_unknown` (partial-success slug on the native path). App-layer enforced; documented
  in `docs/CONVENTIONS.md`. Consistent with governed vocabularies (role-types #266, statuses #306).

## Transports (both, events-style #321)

- **Embedded** — optional `citations: [{field_name, url, title, excerpt, accessed_at, op?, pm_citation_id?}]`
  on existing assignment / event / name / org / person observation payloads. All-or-nothing with the
  parent observation.
- **Native** — `POST /api/v1/{entity_type}/{id}/citations/observations`. Partial-success: per-item
  savepoint, per-item disposition (`new｜auto-attached｜updated｜retracted｜rejected`) + reason slug.
- Scopes: `citations:write` (write), `citations:read` (read).
- Reason slugs (terminal unless noted): `identity_immutable`, `citation_not_found`,
  `provenance_conflict`, `citable_field_unknown`, `entity_unresolved` (transient — self-heals once
  the target entity anchors), `missing_required_field`, `invalid`.

## Read surface

- Dedicated **`GET /api/v1/{entity_type}/{id}/citations?field_name=&include_archived=&limit=&offset=`**
  per citable entity type — standard `{data, meta:{limit,offset,count,has_more}}` envelope, fetch
  `limit+1` for `has_more`, `ORDER BY … , id` (unique tail per #297). `accessed_at` / `created_at`
  serialized via `@field_serializer` → `fmt_ts` (ISO-8601 `Z`).
- **Not** embedded in each entity `response_model` by default (avoids bloat across six types).
  `?include=citations` on entity reads is a possible fast-follow, out of scope for v1.

## Admin dashboard (full CRUD)

- Presence icon (`📚 N`) + standalone citation editor on each citable entity detail page:
  add / edit / archive / unarchive. Reuses the #326 ancillary-partial plumbing and the #318
  notes-editor pattern.
- Conventions: `flash_trigger`, `markupsafe.escape()` on all DB-derived values, `is_htmx` partial
  with `RedirectResponse` fallback, `Depends(get_admin_user)` on every route.

## Ancillary machinery (must plug into all of it — #324/#327)

- **entity_changes emit** — DB touch-cascade trigger `trg_touch_entity_on_citation_change`
  (per-row parent-'updated' signal), **not** app-layer emit. Every write path signals uniformly.
- **Merge re-homing** — add `citation` to the `rehome_conflicting_*` machinery
  (`src.core.ancillary_migrate`); re-point/dedup onto the survivor **before** the conflict-DELETE in
  `people_merge.py`, `orgs_roles.py::role_merge`, `orgs_merge.py` role-pair. Citations self-emit via
  their touch trigger (like `links`/`contact_methods` post-#327), so no manual merge emit.
- **Orphan audit** — add a `citation` scope to `scripts/audit_ancillary_orphans.py` + the daily
  `power-map-ancillary-orphans.timer`; anti-join over the polymorphic entity types.
- **Schema-parity** — the trigger + constraints are picked up automatically by the daily #331
  `power-map-schema-parity.timer` (functions/triggers surface).

## Migration of `.notes` provenance

- Supervised, per-known-pattern script (dry-run → `--execute`), starting with the #314 Jinkins
  housedemocrats.wa.gov citations folded into the Designate + Speaker tenures. Writes structured
  `citations` rows; **keeps** the note text. No blind regex sweep.
- Do **not** migrate `wa_pdc` / `sec_form_d` / `wikipedia` link_types — those are legitimate
  entity web-presence, not fact citations.

## Testing (TDD, red → green)

- **Core** — identity dedup incl. `NULLS NOT DISTINCT` (one URL-less per `(entity, field)`),
  refine no-op gate, provenance gate, retract + anti-resurrection, `CITABLE_FIELDS` rejection,
  `CHECK (url OR title)`.
- **Public** — embedded all-or-nothing; native partial-success dispositions + reason slugs;
  read pagination unique-tail + `has_more`; timestamp serialization; scope enforcement.
- **Admin** — rollback-client CRUD, htmx partial + RedirectResponse fallback, escaping, auth redirect.
- **Merge** — citation re-homing before conflict-DELETE; orphan-audit counts across all scopes.

## Out of scope (v1)

- Two distinct offline (URL-less) sources per `(entity, field)` — the `NULLS NOT DISTINCT`
  limitation; a later `source_ref` discriminator could join identity if it ever matters.
- `?include=citations` embedding on entity read responses.
- Merging `citations` with `field_confidence`.
- Retrieval-health / dead-link checking of cited URLs.

## Phasing

All six entity types (`organization`, `person`, `role`, `role_assignment`, `person_name`,
`entity_event`) are citable in v1. Suggested internal order: schema + core → public write
(embedded + native) → public read → admin CRUD → merge re-homing + orphan audit →
supervised `.notes` migration.
