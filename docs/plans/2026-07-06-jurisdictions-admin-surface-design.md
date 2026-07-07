# Surface Jurisdictions in the Admin Dashboard (Design)

Issue: #275
Date: 2026-07-06
Status: Approved (brainstorming)

## Goal

Make Jurisdictions a **fully managed** entity in the admin dashboard —
browsable, inspectable, and editable — closing the gap between a fully-realized
backend and a dashboard that surfaces the entity only as a read-only typeahead
feeding the role form.

## Current state

- **Backend: complete.** `jurisdictions` + `jurisdiction_types`, a typed
  bitemporal relationship graph (`jurisdiction_relationships` +
  `jurisdiction_relationship_types`, categories spatial/governance/functional/
  lineage), `organization_jurisdiction_affiliations` (+ types),
  `v_jurisdiction_display_names`, validity/supersession columns (`valid_from`,
  `valid_until`, `superseded_at`, `archived_at`), and polymorphic attachments
  (identifiers, links, addresses, contact_methods — all four CHECK-allow
  `jurisdiction`; `jur_ocd`/`jur_fips`/`jur_iso3166_2` identifier types seeded).
- **Write-ready.** `trg_updated_at_jurisdictions`, `trg_entity_changes_jurisdictions`
  (INSERT/UPDATE → change-feed outbox), the `deleted_entities` tombstone trigger,
  and the subscription allowlist **all already include `jurisdiction`**. No schema
  work required.
- **Public API: full read + write.** `GET /jurisdictions` (type filter), detail
  (id/slug), `/relationships`, `/lineage`, `/resolve`, `POST /observations`.
- **Admin: essentially invisible.** The only surface is
  `src/api/admin/jurisdictions.py` — a read-only `/search/` typeahead feeding the
  role form's jurisdiction picker. No nav entry, no Entities card, no dashboard
  count, no list, no detail. Org detail does not surface its jurisdiction
  affiliations either.

## Approach

Purely an **admin UI + routes build**, mirroring the orgs/people modules and
reusing the entity-parameterized factory routers. No backend/schema changes.

Delivered in **three independent phases** (3 branches / 3 PRs), each TDD-verifiable
in isolation. Value lands immediately: Phase 1 alone makes the entity visible.

### Module & route architecture

Mirror the orgs module split under `src/api/admin/`:

| Module | Phase | Contents |
|---|---|---|
| `jurisdictions.py` *(extend)* | 1–2 | list, detail, new/create, inline edits, archive/unarchive/delete; keep existing `/search/` typeahead |
| `jurisdictions_queries.py` *(new)* | 1 | `query_jurisdictions_rows(...)` — mirrors `orgs_queries.py` |
| `jurisdictions_contacts.py` / `_links.py` / `_identifiers.py` *(new)* | 2 | ~12-line factory wiring each (`make_*_router(entity_type="jurisdiction", …)`) |
| `jurisdictions_addresses.py` *(new)* | 2 | hand-built, mirrors `orgs_addresses.py` (address-normalizer path) |
| `jurisdictions_relationships.py` *(new)* | 3 | typed bitemporal edge CRUD |
| `jurisdictions_affiliations.py` *(new)* | 3 | org-affiliation CRUD (reciprocal panel on org detail) |

Templates under `src/templates/admin/jurisdictions/`: `list.html`, `_region.html`
(HTMX partial), `detail.html`, `form.html`, `partials/*`. Router already
`include`d at `router.py:92` — add the new module includes. Nav gets a
`Jurisdictions` sidebar link; `active_section='jurisdictions'`.

## Phase 1 — Browse & inspect

- **Nav + Entities card + dashboard count.** Sidebar link; card in
  `entities/index.html` (no dup badge — jurisdictions have no dup tables);
  `jurisdictions` count in `dashboard.py`.
- **List** (`GET /admin/jurisdictions/`): search (name/slug ILIKE), type filter
  (dropdown from `jurisdiction_types`), status filter (active/archived, plus a
  `superseded` lens via `superseded_at`), pagination — HTMX region swap like orgs.
- **Read-only detail** (`GET /admin/jurisdictions/{id}/`): header (display name,
  slug, type badge, status, validity range, `superseded_at`, notes) + panels:
  - Identifiers, Links, Addresses, Contacts (read-only display)
  - **Relationships** — graph edges (from/to, type, category, validity), both dirs
  - **Lineage** — recursive supersedes/evolved_from/merged_into chain
  - **Affiliated organizations** — via `organization_jurisdiction_affiliations`
  - **Referencing roles** — districted seats where `roles.jurisdiction_id = this`
    (the reciprocal of the role-form picker — the "tangential mention" made
    bidirectional)

## Phase 2 — CRUD parity

- **Create** (`GET/POST /admin/jurisdictions/new/`): slug, name, type, valid_from,
  valid_until, notes. Validation: name/slug/type required; slug UNIQUE (catch
  `UniqueViolationError` → 422 field error); validity range mirrors the DB CHECK.
  `INSERT` → triggers auto-handle `updated_at` + outbox. Redirect to detail.
- **Inline curatorial edits** (orgs read-partial / edit-form / POST pattern):
  name, notes, valid_from/valid_until. **slug + type also editable**, with a
  visible caveat on slug ("changing the slug changes the public `/resolve` key").
- **Archive / unarchive / delete**: mirror orgs. Delete requires archived →
  `DELETE` + `INSERT deleted_entities` (tombstone trigger emits outbox).
  FK-guarded: a jurisdiction referenced by `roles.jurisdiction_id`,
  `jurisdiction_relationships`, or `organization_jurisdiction_affiliations` 409s
  on delete (catch `ForeignKeyViolationError`).
- **Attachments**: contacts / links / identifiers → factory routers + small
  template partials. Addresses → hand-built, mirrors `orgs_addresses.py`.

## Phase 3 — Graph & affiliations

- **Relationship edges** (`jurisdictions_relationships.py`): add/remove typed
  edges on detail. Add form: target jurisdiction (reuse `/search/` typeahead),
  rel_type (dropdown from `jurisdiction_relationship_types`, grouped by category),
  **direction** for asymmetric types (symmetric types skip direction — stored
  once, read both ways), valid_from/valid_until, notes. DB guards surfaced:
  `chk_no_self_rel` (self-edge → 422), validity range.
  - **"Remove" = hard-delete** the edge (mistake correction); temporal *end* is
    expressed by inline-editing `valid_until` (the table has no `archived_at`,
    so no soft-delete is invented).
- **Org affiliations** (`jurisdictions_affiliations.py`): list + add (org
  typeahead + affiliation_type dropdown) + remove, on jurisdiction detail. Unique
  `(org, jur, type)` → dup 409. **Reciprocal:** also add this panel to **org
  detail** (which surfaces nothing about jurisdictions today), so the association
  is manageable from both sides.

## Key decisions & rationale

- **Full management incl. admin create.** Chosen over observation-only. Caveat:
  a hand-typed slug can diverge from upstream OCD/FIPS identity — surfaced as a UI
  warning, not blocked.
- **`entity_lookup.ENTITY_TYPES` deliberately not extended** (stays
  `("person","organization")`). It drives event-linked-entity + generic entity
  search; jurisdiction needs neither (events exclude jurisdiction; the
  jurisdiction typeahead is separate). Leaving it untouched avoids ripple.
- **Events out of scope.** `entity_events` CHECK is person/org only — jurisdiction
  attachments are identifiers, links, addresses, contacts.
- **No dup-detection surface.** Jurisdictions have no dup tables (unlike
  orgs/people) — no merge/dismiss UI, no dup badge.
- **No schema changes.** Backend is already write-ready.

## Out of scope

- Schema/DB changes (none needed).
- Public API changes (read + observation write already exist).
- Events on jurisdictions (CHECK-excluded).
- Jurisdiction dup detection / merge.
- Extending `ENTITY_TYPES` / making jurisdiction a linkable event entity.

## Testing strategy

TDD throughout. Integration tests mirroring `tests/api/admin/` (`TEST_DATABASE_URL`,
`db_pool` fixture, `loop_scope="session"`):

- **P1** — list (search/type filter/status/pagination), detail (renders all
  panels; 404), nav + dashboard count.
- **P2** — create (valid / dup-slug / bad-range / missing-required), inline edits,
  archive/unarchive/delete (+ FK-guard 409), attachment CRUD per router.
- **P3** — relationship add / edit-validity / delete (+ self-edge guard, symmetric
  handling, direction), affiliation add/remove (+ dup guard, reciprocal on org
  detail).

Docs updated alongside code: `docs/STYLE.md §32` (admin conventions) and the
AGENTS.md admin note. Every route `Depends(get_admin_user)`; `is_htmx` partials
with `RedirectResponse` fallback; `flash_trigger` + `markupsafe.escape` on
DB-derived values.
