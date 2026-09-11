# Curation overlay: the admin write path, pins, and the fifth slot (#498)

Part of #490 (design § PM-side pipeline step 3, safeguards 3–4). Follows #497,
which built the `curation_overlay` table, the declarative join in four marts,
and the `overlay_field_unmapped` vocabulary test, and #499/#514, whose applier
the overlay steers.

## Goal

A curator's correction to a producer-owned field survives every later snapshot
without a per-row gate in the applier: PM state for the slice is
f(snapshot, overlay). #497 built the read side. This builds the write side —
the admin edits that write overlay rows, and the surfaces that make every
override visible and removable.

## What exists, and what doesn't (2026-09-11)

- **Exists:** the table (`schema.sql`, unique on `(entity_type, entity_id,
  field)`, nullable `value`); the join in `desired_person_names`,
  `desired_organization_names`, `desired_organization_acronyms`,
  `desired_organization_parents` (presence wins: a null override drops the
  producer's claim); the export (`scripts/export_pm_tables.py`); the vocabulary
  test (`person.name`; `organization.parent_id|legal_name|acronym`).
- **Missing:** everything admin-side — nothing in `src/api` touches the table;
  archive semantics (the table has no `archived_at`); and a slot for the fifth
  producer-owned column, `desired_entity_events.event_year` (dissolved).
- **Constraint:** the API cannot import the manifest.
  `src/core/ingestion/mapping/__init__.py` imports dbt at module load, and the
  service's venv carries no `mapping` group.

## Decisions (user, 2026-09-11)

| Question | Decision |
|---|---|
| The dissolved-event year is producer-owned with no overlay slot | **Include it** — `organization.dissolved_year`, joined in the events mart, wired in the event editor |
| How the admin knows what is pinnable | **Registry + manifest key + sync test** — each owned table names its overlay field in `manifest.yml`; the admin carries a five-slot registry; one structural test holds the manifest, the dbt vocabulary and the registry to each other |
| Pin rule | A write that **changes** a slot's value pins the post-edit value; NULL when nothing of the slot remains |
| UI | Per-slot note, Pin and Unpin, a Pinned badge, and a `/admin/pins` page |

Rejected: the manifest driving the admin directly (needs a dbt-free split of
the mapping package *and* row selectors in `target:` — a query language for five
slots); database triggers (cannot tell a curator's intent from a merge or the
applier's own write, and would move the producer's ownership list into
`schema.sql`, the coupling #490 exists to remove).

## The five slots

| Overlay field | PM storage (the slot's value) | Admin edit sites |
|---|---|---|
| `person.name` | text of the canonical legal `person_names` row | names factory (`_names_shared.py`) |
| `organization.legal_name` | text of the canonical legal `organization_names` row | names factory |
| `organization.acronym` | text of the canonical `organization_acronyms` row | `orgs_acronyms.py` |
| `organization.parent_id` | `organizations.parent_id` | `orgs.py` — inline parent, add child, remove child |
| `organization.dissolved_year` | year of the unarchived `dissolved` `entity_events` row | `_events_shared.py` |

## Data model and semantics

- **Schema.** `curation_overlay` gains `archived_at`; the unique index becomes
  partial on `archived_at IS NULL` — one *active* pin per entity and field — via
  an idempotent reconciliation block that swaps the existing full index.
- **Unpin archives.** The row stays as history (who, when, note); re-pinning
  inserts a fresh active row. No unarchive, no hard delete.
- **The models see active pins only.** The export gains `archived_at`;
  `stg_pm__curation_overlay` filters to active rows.
- **Scope.** A slot is pinnable iff the entity has a `producer_crosswalk` row
  with resolution `live` or `merged` and the slot is in the registry for its
  type. Everything else stays direct curation, unchanged.
- **The pin rule.** Every admin write touching a slot's rows — edit, delete,
  type change, canonical toggle, parent set or clear, event archive — reads the
  slot's value before and after, in its own transaction. Changed → pin the
  post-edit value; nothing of the slot left → pin NULL; unchanged (a locale edit)
  → nothing. Pinning the value *as it stands*, not the text typed, guarantees
  the applier finds it present: a noop, never a re-insert.
- **Timing.** A pin reaches the desired state on the next nightly build. Before
  the #501 flip an unpin shows as an `update` in the dry-run diff; after it, the
  producer's value is written back.
- **Core** — `src/core/curation_overlay.py`, producer-free: `pinnable_slots`,
  `pin(…, value, *, user_id, note=None)`, `unpin(…, *, user_id)`, `active_pins`.
  #501's triage CLI calls the same `pin()`.

## Admin UI

- **In-scope slot, unpinned:** a muted *"Maintained by usa-wa"*; its edit form
  adds *"Saving a change pins your value — PM keeps it over future snapshots
  until you unpin it."* A small **Pin** keeps the current value without an edit.
- **Pinned:** a **Pinned** badge (value, who, when, note) and **Unpin** —
  archive, success flash *"Unpinned — usa-wa's value returns on the next
  apply."* An edit that creates or moves a pin says so in its own flash (#353).
- **`/admin/pins`:** active pins with entity (display name, linked), field,
  value, note, who, when; filter by entity type plus the #306 status axis
  (`active` default, `archived`, `all`) via `STATUS_PREDICATES` /
  `VALID_STATUSES`; inline Unpin; linked from the nav.
- **House rules:** HTMX partial plus `with_flash` fallback on every mutation
  (`test_mutation_fallback_sweep.py`); `markupsafe.escape()` on every value in a
  flash; new routes in the route-enum module for the browser a11y sweep.
- **"usa-wa" is named by the admin layer** (a label keyed by crosswalk
  `source`), never in `src/core` (`test_src_core_wa_free.py`).

## Models and manifest

- `manifest.yml`: each owned `column`/`child` table carries `overlay:
  <field>`; the loader requires it on those shapes and refuses it elsewhere
  (merge and entity tables have nothing to pin).
- `desired_entity_events` joins `organization.dissolved_year`, cast to
  integer, presence-wins: a pinned year emits the dissolved row (even where the
  producer is silent), a null pin drops it. The vocabulary test gains the pair,
  and warns on a non-integer year.
- **One structural test:** manifest overlay fields == admin registry keys ==
  the vocabulary test's accepted pairs.

## Migration and deploy

`ADD COLUMN IF NOT EXISTS archived_at`; a `DO` block for the index swap; the
export spec and the staging model change together. The service restart applies
the schema before the next 09:30 chain; the export treats a missing column as
the deploy state it already treats a missing table as. v0.49.0.

## Testing (TDD throughout)

- **Core:** pin, unpin, re-pin after unpin, one active pin per field, scope.
- **Admin, per edit site:** the value changes → pinned; a cosmetic edit → no
  pin; out of scope → no pin; delete or archive → NULL pin. Pin and Unpin
  routes; badge and note rendering; the pins page and its filters; the
  mutation-fallback and a11y sweeps.
- **dbt:** the dissolved-year join; a null pin drops the row; a pin adds one.
- **The seam test** — both acceptance criteria, end to end: pin through the
  admin → export → dbt build over fixture snapshots → dry-run diff is a **noop**
  for that slot while the producer reasserts its value; unpin → rebuild → the
  diff is the **update back** to the producer's value.

## Out of scope

Role and assignment slots (#500 adds them to the registry with their models);
#501's bulk triage CLI (calls `pin()`); showing *"usa-wa says X"* beside a pin;
unarchive or hard delete of pins; pins in the public API.
