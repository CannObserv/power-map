---
title: "#321 — producer event refine-in-place + succeeded_by slug"
date: 2026-07-25
status: approved
issue: 321
related: 307, 311, 313, 305, 266, 85, 109, 112, 107
---

# #321 — producer event-write refinement + `succeeded_by`

## Problem

usa-wa's model-A backfill produced ~150 historical WA committee orgs, all
`active=true`, none lifespan-bounded. Hand-entering `dissolved`/succession
events per admin (the #313 path) doesn't scale. The producer holds the
objective signal (each org's operational biennium window) and re-emits every
refresh cycle. Two capabilities are missing to let it self-serve the
#313-class backfill programmatically:

1. **Refine-in-place** — a `founded` year sharpening 2013→2011 changes the
   content-dedup key, so append-only writes mint a *duplicate*. The producer
   needs to update an event it already anchored, without a dup.
2. **`succeeded_by`** — the dominant WA re-key is a rename + re-scope yielding
   a *new* committee Id (a continuation, not a dissolve/branch). No existing
   linked-entity slug captures it.

## Corrections that reshaped the ask (issue thread)

- Events are **already** producer-writable, embedded in the org/person
  observation payload (`write_entity_events`). Not admin-only.
- A dedicated `/events/observations` sub-resource does **not** decouple events
  from the org LWW clock — both transports fire
  `trg_touch_entity_on_event_change` → `organizations.updated_at` → org outbox
  row (`fn_record_entity_change`). LWW decoupling is a *trigger* decision,
  orthogonal to transport.
- Strict-identical re-emit is **already** a true no-op via content-dedup
  (`(event_type, full partial-date, linked_entity)`, NULLs-equal). Issue #3's
  "hard requirement" already holds for the append-only case.

Net scope: **succeeded_by + refine-in-place + per-event disposition**, not a
new write API.

## Locked decisions

- **Anchor: B — `pm_event_id` PM-native update** (mirror #311, not a new
  `(source, source_id)` primitive). usa-wa producers are stateful and already
  address PM by `pm_*_id` (#311 dual-mode in `descriptors/assignment.py`); A's
  stateless-re-emit benefit is unused → YAGNI. `(source, source_id)` is added
  only if/when a stateless producer exists.
- **Trigger: no change.** Keep the org-touch; load-bearing for usa-wa's
  `sync_entity_events` reconcile (it re-fetches `/events` on org change). A
  genuine timeline change *should* propagate an org change; spurious churn is
  gone once content-dedup (creates) + diff-before-write (updates) are in place.
  Event-granular outbox rows, if ever wanted, go in *additively* later — never
  as a replacement.
- **Transport:** per-event disposition is the must-have; a thin
  `POST /organizations/{id}/events/observations` is worth building, justified
  by **failure isolation** (not LWW). If deferred, enrich the embedded response
  instead — we lose only isolation.
- **Diff-before-write (non-negotiable):** an unchanged `pm_event_id` update
  must skip the UPDATE, else `updated_at` bumps and re-arms the ping-pong that
  content-dedup avoids for free. The comparator stays **narrow** — only the
  mutable field set — per the #109 lesson: a wide comparator risks a false
  no-op that *erases* a pending change.

## Identity vs. mutable — the #311 analog

| assignment (#311) | event (#321) |
|---|---|
| identity: `(person, role)` | identity: `(event_type, linked_entity)` |
| mutable via id-address: `start_date`, `end_date`, `is_current` | mutable via id-address: `date` parts, `notes`, `place`, `visibility` |

`linked_entity` is the event's *other endpoint* — changing it changes **what**
the relationship is (exactly as changing an assignment's `person` would), so it
is **identity, immutable via `pm_event_id`**. `date` is the refinable *when*,
exactly like `start_date`. A `succeeded_by` re-pointed at a different successor
is a *different* event → `rejected` (`identity_immutable`), never a silent
reclassify.

## Partial-success semantics

The writer stops being all-or-nothing. Today `write_entity_events` raises
`ObservationRejected` → the entire org observation transaction rolls back
(`orgs.py:70-99`). Instead: a **list** of events, **per-event savepoint**,
partial-success — commit the good ones, collect outcomes.

Beyond matching the lineage-backfill shape, this buys **ordering-tolerance for
free**: a `succeeded_by` on 14294 → 28244 emitted *before* 28244 is anchored
comes back `rejected` (`linked_entity_unresolved`) while 14294's
`founded`/`dissolved` land, and the link self-heals next cycle once the
successor anchors. The producer never has to strictly sequence "anchor all
successors, then emit links."

### `rejected` carries a machine-readable reason slug

Required (rider, issue thread) — the #85 rejection-visibility summary and #112
non-convergence tracking bucket by reason (the assignment `identifier_conflict`
precedent). Without it, a permanently-rejected event churns invisibly every
cycle, indistinguishable from a transient self-healing one. Slug set:

| slug | class | meaning |
|---|---|---|
| `linked_entity_unresolved` | transient | linked entity not yet anchored in PM; self-heals next cycle |
| `identity_immutable` | terminal | `pm_event_id` update tried to change `event_type` or `linked_entity` |
| `event_not_found` | terminal | `pm_event_id` does not resolve |
| `provenance_conflict` | terminal | `source_key_id` gate failed (foreign non-NULL source) |
| `applies_to_mismatch` | terminal | event type does not apply to this entity kind |
| `missing_required_field` | terminal | `requires_year` / `requires_linked_entity` unmet |
| `unknown_event_type` | terminal | slug/id not in `entity_event_types` |
| `invalid` | terminal | catch-all / validation |

Transient vs. terminal is the axis #112 cares about; the table above is the
authoritative mapping.

## Proposed shape

### 1. `succeeded_by` slug
Seed row in `entity_event_types`:
`succeeded_by | Succeeded By | organization | requires_year=FALSE | requires_linked_entity=TRUE`
(via the existing `ON CONFLICT DO UPDATE` seed block). Direction (documented,
not derivable from the row): event lives on the **predecessor**;
`linked_entity_id` → successor. `founded`/`dissolved` (window) and
`split_from`/`merged_with` (branches) unchanged. Multi-way re-orgs stay
pairwise (1→2 split = two `split_from` child→parent; 2→1 merge = pairwise
`merged_with`).

### 2. `pm_event_id` dual-mode in `write_entity_events`
Per event item, optional `pm_event_id`:
- **present** → resolve the event by id; `event_not_found` if absent;
  `provenance_conflict` if the `source_key_id` same-or-NULL gate fails;
  `identity_immutable` if the payload's `event_type`/`linked_entity` differ
  from the stored row; else diff the mutable field set — skip UPDATE if
  unchanged (`auto-attached`), else UPDATE (`updated`).
- **absent** → current content-dedup: match → `auto-attached`, else INSERT →
  `new`.

### 3. Per-event disposition response
`write_entity_events` returns
`list[EventObservationResult{event_id, disposition, reason?}]`, where
`disposition ∈ {new, auto-attached, updated, rejected}` and `reason` is a slug
from the table above (present only on `rejected`). Surface it in the org
observation response and, if built, the thin sub-resource. (Today it returns
`None` and silently skips dups — usa-wa's no-op gate + #112 non-convergence
telemetry can't function without this.)

### 4. Thin `POST /organizations/{id}/events/observations`
`require_scope("observations:write")`; resolves the org, delegates to
`write_entity_events` under per-event savepoints, returns the per-event result
list. Failure isolation is the justification — the lineage backfill emits many
linked-entity events across orgs, and one not-yet-anchored successor shouldn't
roll back an org's whole observation.

## Not doing (this issue)
- No `(source, source_id)` columns (anchor A) — YAGNI until a stateless producer.
- No trigger/outbox decoupling.
- No `active` change (usa-wa's committee-active reconcile handles the ~150
  deactivation).
- No org-level `founded`/`dissolved` scalar — the event model is the surface.

## Follow-up (tracked, not in this issue)

**Event retraction / void path.** Immutable identity means a mis-linked event
(`succeeded_by`/`split_from`/`merged_with` — the dateless linked cases with
**no mutable field to refine**) can only be corrected by create-new +
retract-old, not an in-place edit. Common path (date-refine on
`founded`/`dissolved`) is fully covered by the mutable set; mis-links are rare
and admin-retractable (the #313 precedent). But the operator-attestation
surface (usa-wa#107) is append-only-with-supersede, so a corrected attestation
that re-links leaves the erroneous PM event stranded until a void operation
exists. File as the follow-up that closes the correction loop for dateless
linked events.

## Downstream note
Append-only embedded writes already no-op cleanly, so usa-wa's
`founded`/`dissolved` window emission is partially unblocked **today**; only
refine-in-place and `succeeded_by` truly gate on this issue.

## Test surface (sketch)
- `succeeded_by` seed present; `applies_to=organization`; `requires_linked_entity`.
- refine-in-place: year 2013→2011 via `pm_event_id` updates in place (no dup);
  identical re-emit → `auto-attached`, `updated_at` unchanged (diff-gate).
- provenance: update gated to same-or-NULL `source_key_id`; foreign source →
  `rejected` / `provenance_conflict`.
- immutable identity: changing `event_type`/`linked_entity` via `pm_event_id` →
  `rejected` / `identity_immutable`.
- ordering-tolerance: `succeeded_by` before successor anchored →
  `rejected` / `linked_entity_unresolved`, siblings land, heals next cycle.
- partial-success (list): batch of [good, bad] → good committed, bad reported
  with reason slug, no rollback of good.
- per-event disposition + reason slug surfaced in the response on every path.
