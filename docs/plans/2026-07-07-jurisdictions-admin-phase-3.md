---
title: "#275 Phase 3 — Jurisdictions admin: graph & affiliations"
date: 2026-07-07
status: draft
---

# #275 Phase 3 — Jurisdictions admin: graph & affiliations

Design: `docs/plans/2026-07-06-jurisdictions-admin-surface-design.md` · Issue: #275
Branch: `feature/275-jurisdictions-admin-phase-3` (off `main`, which now carries Phases 1+2)

## Problem

Phases 1+2 made jurisdictions browsable and fully CRUD-able, but the two **graph**
panels on the detail page — **Relationships** and **Affiliated organizations** —
are still read-only lists. A curator can't add or retire a typed relationship
edge (spatial / governance / functional / lineage) or manage which organizations
are affiliated with a jurisdiction. And **org detail surfaces nothing about
jurisdictions**, so an affiliation can only ever be *seen* from one side, never
managed from it. Phase 3 closes the loop: editable graph edges + bidirectional
affiliation management.

## Approach

Two new admin modules, mirroring the established typeahead + add-row-guard +
inline-row patterns (orgs children / people assignments):

- **`jurisdictions_relationships.py`** — makes the Relationships panel
  interactive. Add: target-jurisdiction typeahead (reuse the Phase-1
  `/admin/jurisdictions/search/`), a `rel_type` dropdown grouped by category
  (from `jurisdiction_relationship_types`), a **direction** control for
  asymmetric types (symmetric types store once, read both ways), `valid_from` /
  `valid_until`, notes. Inline-edit `valid_from`/`valid_until` on an existing edge
  (this is how a relationship is *temporally ended*). Remove = **hard-delete**
  (the table has no `archived_at` — mistake correction, not soft-delete). DB
  guards surfaced as 422: `chk_no_self_rel` (target == current), `chk_rel_valid_range`.
- **`jurisdictions_affiliations.py`** — makes the Affiliated-orgs panel
  interactive on jurisdiction detail (org typeahead + `affiliation_type`
  dropdown) + remove; unique `(org, jur, type)` → 409. **Reciprocal:** a second
  router in the same module powers a new "Affiliated jurisdictions" panel on
  **org detail** (jurisdiction typeahead + type + remove), so the link is
  manageable from both sides.

**Change-feed propagation.** Affiliations already touch the org's `updated_at`
(`trg_touch_org_on_affiliation_change` → `touch_parent_org`), which rides the org
`entity_changes` outbox — so affiliation edits already emit. But
`jurisdiction_relationships` has **no trigger at all** today, so relationship
edits would be invisible to the change feed. Phase 3 adds a
`touch_parent_jurisdiction()` trigger (mirrors the org pattern) touching
`from_id`/`to_id` so edits fire `trg_entity_changes_jurisdictions`. This is the
**only schema change** in Phase 3.

TDD throughout, mirroring `tests/api/admin/test_orgs*.py` + Phase 2's
`test_jurisdictions_crud.py`.

## Tradeoffs / alternatives

- **Soft-delete edges (add `archived_at`)** — rejected; the table intentionally
  has none, and the design specifies remove = hard-delete (temporal end via
  `valid_until`). Inventing soft-delete diverges from the schema.
- **Skip the reciprocal org panel** — rejected; the design explicitly requires
  bidirectional management, and org detail shows nothing about jurisdictions today.
- **Two separate affiliation modules (jur + org)** — rejected for one module with
  two routers: it's one table with symmetric logic; splitting duplicates the
  fetch/validate helpers.
- **Leave the relationship change-feed gap** (document it like the roles outbox
  gap) — possible, but Phase 2 deliberately kept jurisdictions gap-free; a small
  touch trigger preserves that (see Open questions).

## Steps

1. **Relationship add** — RED: add symmetric edge (stored once); asymmetric with
   direction (current = from vs to); self-edge → 422; `valid_from > valid_until`
   → 422; edge renders in the panel. GREEN: `jurisdictions_relationships.py`
   new-row (typeahead + grouped `rel_type` + direction + validity/notes) +
   create; "+ Add relationship" button on detail (add-row-guard trio); register
   in `router.py`.
2. **Relationship inline-edit validity** — RED: `valid_from`/`valid_until`
   round-trip (read-row → edit-row → post → updated row); invalid range → 422.
   GREEN: read-row / edit-row / post routes + partials.
3. **Relationship delete** — RED: delete removes the edge; 404 if absent. GREEN:
   delete route + `hx-confirm`.
4. **Change-feed trigger** — RED: a relationship insert/delete produces an
   `entity_changes` row for the touched jurisdiction(s). GREEN:
   `touch_parent_jurisdiction()` + trigger on `jurisdiction_relationships` in
   `schema.sql`; `apply-schema.sh`.
5. **Affiliation add + remove (jurisdiction side)** — RED: add (org typeahead +
   type) renders; dup `(org, jur, type)` → 409; remove deletes. GREEN:
   `jurisdictions_affiliations.py` (jur-scoped router) + panel wiring on
   jurisdiction detail.
6. **Reciprocal org-detail panel** — RED: org detail shows "Affiliated
   jurisdictions"; add (jurisdiction typeahead + type) + remove from the org
   side; dup → 409. GREEN: org-scoped router in the same module + panel partial
   on `orgs/detail.html`; register in `router.py`.
7. **Docs + verification** — update `docs/STYLE.md §32` (graph editing live;
   reciprocal org panel; relationship change-feed trigger). Run `ruff` + full
   pytest + JS; dev-server smoke on 8001 (add/edit/delete edge + add/remove
   affiliation from both sides).

## Open questions / risks

- **Relationship change-feed trigger (Step 4)** — add `touch_parent_jurisdiction()`
  (schema change) so edits emit to the outbox, or accept a documented gap?
  **Recommend ADD** — keeps jurisdictions gap-free per Phase 2. It is the only
  schema change in Phase 3 (needs `apply-schema` / restart at ship).
- **Lineage-category edges in the editor** — the add-form `rel_type` dropdown:
  include all 4 categories (a lineage edge added here surfaces in the read-only
  Lineage panel, not Relationships), or exclude `lineage`? **Recommend INCLUDE
  all**; keep the display split exactly as today.
- **Direction UX for asymmetric types** — a from/to toggle with a live phrase
  preview ("{this} contains {target}" ⇄ "{target} contains {this}"). Minor;
  will keep a simple select if no directional editor exists to mirror.
- **PR scope** — one PR for all of Phase 3 (relationships + affiliations +
  reciprocal), matching Phase 1+2 delivery. Confirm.
