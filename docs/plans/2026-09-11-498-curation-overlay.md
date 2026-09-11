---
title: "#498 curation overlay — build plan"
date: 2026-09-11
status: executed 2026-09-11 (steps 1–10)
design: 2026-09-11-curation-overlay-design.md
---

# #498 curation overlay — build plan

Executes the approved design in `2026-09-11-curation-overlay-design.md`, which
holds the *why* and the decisions; this holds the order of work and what "done"
looks like at each step.

## Problem

The overlay can be read (#497) but not written: nothing in `src/api` touches
`curation_overlay`, so a curator's correction to a producer-owned field is a
plain edit that the #501 flip will revert — safeguard 3's failure, silently.
The fifth producer-owned column (the dissolved year) has no slot at all, and
an override has no archive semantics, so removing one would erase its history.

## Approach

Five slots, each a producer-owned field: the manifest names its overlay field,
the admin carries a registry saying how to read each slot's live value, and one
structural test holds the manifest, the registry and the dbt vocabulary to each
other. `src/core/curation_overlay.py` owns pin, unpin and scope, producer-free.
Every admin write touching a slot reads its value before and after, in the
same transaction, and pins the post-edit value when it changed. Unpin archives.
The UI shows a note, a Pinned badge, Pin and Unpin per slot, and a `/admin/pins`
list. A seam test proves both acceptance criteria end to end.

## Tradeoffs / alternatives

- **Manifest drives the admin directly** — rejected at design: needs dbt split
  out of the mapping package and row selectors in `target:` for five slots.
- **Database triggers write pins** — rejected at design: cannot tell curator
  intent from a merge or the applier's own write; puts the ownership list in
  `schema.sql`.
- **Pin on every edit** — rejected at design: a locale edit would pin a value
  nobody changed and suppress the producer's future corrections to it.

## Steps

1. **Archive semantics, schema to staging.** `archived_at` on
   `curation_overlay`; a `DO` block swaps the full unique index for a partial one
   on `archived_at IS NULL`; the export spec gains the column and tolerates its
   absence like a missing table; `stg_pm__curation_overlay` reads active rows.
   The table's one existing writer follows: #514's `rehome_curation_overlay`
   (every person, org and role merge path, sweep-enforced) **archives** a
   clashing loser pin instead of deleting it, and only *active* pins clash —
   today a survivor holding only an archived pin would delete the loser's live
   one. *Done when:* integration tests show two active pins refused, a re-pin
   after archive accepted, and a merge moving the loser's active pin, archiving
   it under a survivor's active pin, and carrying archived history across; a dbt
   test shows an archived pin applied nowhere; `apply-schema.sh --test` clean
   twice (idempotent).
2. **The manifest names each slot.** `overlay:` on the five owned
   `column`/`child` tables; `TableSpec.overlay`; the loader requires it on those
   shapes and refuses it elsewhere. *Done when:* `test_manifest.py` covers both
   refusals and the five declarations.
3. **The fifth slot in the models.** `desired_entity_events` joins
   `organization.dissolved_year`, cast to integer, presence-wins; the vocabulary
   test gains the pair and warns on a non-integer year. *Done when:* dbt tests
   show a pinned year emitting the row where the producer is silent, a null pin
   dropping it, and a producer year standing when unpinned.
4. **Core overlay.** `src/core/curation_overlay.py`: `in_scope` (live or merged
   crosswalk row), `pin`, `unpin` (archive), `active_pins`, and
   `pin_changed(conn, entity_type, entity_id, before, after, *, user_id)`.
   *Done when:* integration tests cover pin/upsert, unpin, re-pin, scope, and
   `test_src_core_wa_free.py` stays green.
5. **The admin registry and the sync test.** `src/api/admin/overlay_slots.py`:
   five slots, each reading its live value (canonical legal name text; canonical
   acronym; `parent_id`; the unarchived dissolved year), and a `tracked()`
   context that reads before, yields, reads after and calls `pin_changed`.
   *Done when:* unit tests read each slot, and a structural test (mapping group)
   asserts registry keys == manifest overlay pairs == vocabulary-test pairs.
6. **Wire the edit sites.** The names factory (people and orgs), acronyms,
   parent (inline, add child, remove child), events — each inside its existing
   transaction; a flash that says when an edit pinned. *Done when:* per-site
   endpoint tests show value change → pinned, cosmetic edit → no pin, out of
   scope → no pin, delete or archive → NULL pin; the mutation-fallback sweep is
   green.
7. **Slot UI.** The note and Pinned badge in the five slot panels; `POST` Pin and
   Unpin routes (HTMX partial + `with_flash` fallback, escaped values); route-enum
   entries. *Done when:* render tests cover in-scope unpinned, pinned and
   out-of-scope panels; a11y render tier green.
8. **The pins page.** `/admin/pins` — active by default, `archived`, `all`
   (`STATUS_PREDICATES` / `VALID_STATUSES`), entity-type filter, inline Unpin,
   nav link. *Done when:* list, filter and unpin tests pass; route enum and a11y
   render tier green.
9. **The seam test — acceptance.** On the rollback connection: a person
   anchored to a fixture producer id, an admin name edit (pins), export through
   `export_pm_tables.run(fetch)`, a dbt build over the fixture store, a dry run.
   *Done when:* the slot is a noop while the producer reasserts its value; after
   unpin → re-export → rebuild, the diff is the update back.
10. **Docs, version.** A pins section in the admin docs (budget-aware: a new
    `ADMIN_OVERLAY.md` behind a pointer if `ADMIN.md` lacks headroom); the
    runbook's pipeline note; one `archived_at` line in `SCHEMA.md` (#518);
    v0.49.0; this plan `executed`. *Done when:* pre-ship green; integration,
    browser a11y tier (alone) green.

## Open questions / risks

- **Pins across a PM merge — resolved (user, 2026-09-11): fold in.** #514
  already re-homes pins on every merge path (`rehome_curation_overlay`, held by
  `test_merge_identity_sweep.py`); #498 makes its clash archive-aware (step 1).
- **Which legal row is "the slot".** Canonical legal row; if no legal row is
  canonical (the display name is `preferred`), the earliest-created legal row;
  none → NULL. Any choice is safe for the applier — the pinned value is always
  present on a legal row — but it decides what the badge shows.
- **The public API can still edit these fields** for other producers; it pins
  nothing (out of scope, as designed). usa-wa's write scopes are revoked (#494).
- **Browser a11y tier** truncates and seeds its database: run it alone.
