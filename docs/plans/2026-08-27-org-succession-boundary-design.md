# Source-keyed succession over merge — PM↔producer boundary integrity

**Date:** 2026-08-27 · **Origin:** structural residue of #467 (org merge broke the
producer's 1:1 identifier anchor) · **Status:** approved

## Problem

An upstream source (WA Legislature) re-keys a continuous institution — committee
`3532` → `31651`, same real-world committee. PM then holds two identically-named
orgs; the dedup UI offers exactly two verbs (merge / dismiss). Merge collapses two
*source records* onto one PM row, making the external-identifier→org mapping N:1 —
which breaks producers (usa-wa uniquely keys orgs by WSL id) and violates PM's own
documented read contract (`API_ORGS.md`: "one WSL Id = one committee is a stable
invariant"). #467 was this exact failure. Upstream re-keys recur (3532→31651,
20900→31639), so the collision is structural, not incidental.

## Frame (approved)

Identity is two layers, and `organizations` was playing both:

1. **Source-record identity** — what a producer's key denotes; temporally scoped;
   *externally owned*. PM may not collapse two of these.
2. **Continuant identity** — the institution over time; *PM-owned*; curated.

Resolution: the org **row** stays at layer 1 (one row per source key, forever);
layer 2 is expressed as a `succeeded_by` event edge and surfaced at the
presentation layer. "One org over time" is a view over the chain, not a row
collapse.

**Rubric (mechanical, computable in the merge preview):**
- Both candidates carry identifiers of the **same external type with distinct
  values** → two source records → **link as successors**.
- Same key twice, or keyless hand-entered dupe → true duplicate → **merge**
  (unchanged from #467 semantics).

`succeeded_by` already exists end-to-end on the wire (#321: seeded event type,
producer-writable, refine/retract, `linked_entity_unresolved` self-heal, exposed
in org reads; usa-wa already emits it). What's missing is the admin surface and
the guardrail.

## Design

### 1. Merge guardrail

Org merge preview computes the rubric: same-external-type distinct-value
identifiers on the pair → prominent warning (names the type + both values, states
the N:1 consequence), merge demoted behind an explicit extra confirm, **"Link as
successors instead"** offered as the primary action. Merge stays possible (the
source itself may have double-keyed in error) but never happens unknowingly.
Orgs only.

### 2. "Link as successors" verb + chain-aware dedup

New action on a duplicate pair (dedup review screen + merge preview): small modal
to pick direction (predecessor → successor; default inferred from assignment date
ranges when unambiguous) plus an optional succession date. Writes one
`succeeded_by` event on the predecessor via the existing admin event machinery
and resolves the pair.

The duplicate detector excludes any pair whose members belong to the same
succession **chain** — transitive closure over active `succeeded_by` edges, both
directions (recursive CTE; the chain is an equivalence class). A→B→C means A/C
never presents as a duplicate either.

### 3. Dated succession bounds the predecessor

`v_org_lifespan` gains: a **dated** `succeeded_by` event derives the
predecessor's lifespan end (same latest-date-within-precision rule as
`dissolved`). Undated succession derives no bound (same as undated
`merged_with`). This makes the manifestation temporally scoped/queryable, feeds
§4's fields, and lets the #307 assignment-lifespan audit catch stragglers. One
view change; no new columns.

### 4. Public read annotation (additive only — approved over collapse-param and
lineage-endpoint alternatives)

Org search results + org detail gain three nullable fields: `lifespan
{start, end}`, `succeeds`, `succeeded_by` (ULIDs). No query-shape change, no new
params, no collapse mode. Chain head = `succeeded_by: null` is the documented
"current manifestation" idiom. Name search stays list-shaped (it always was);
identifier search stays the exact-resolution channel and its 1:1 now holds by
construction. `API_ORGS.md` gains the who-owns-the-key rubric beside its
existing one-key-one-org invariant.

### 5. Admin continuity banner

Org detail banner when the org sits in a chain: "← Continues ‹predecessor›
(…–2020)" / "Succeeded by ‹successor› (2021–) →", linked. Whole UX scope —
chain-aggregated timelines, grouped search, typeahead annotations are out of
scope until wanted.

### 6. Operational cleanup of the #467 merge (+ usa-wa#283 guidance)

Record correction: the 136 assignments were **not** destroyed — the pre-#467
merge migrated them insert-copy-then-delete, so the history sits on the winner's
Member role under reminted ULIDs. What broke was every producer-held anchor.
Cleanup un-collapses into the succession shape, as a supervised `scripts/`
one-off (dry-run default, `--execute` gated, #402/#399 rules):

1. **Resurrect** predecessor org `01KWJA2SFGTT84H4EW13QT1F2B` and its Member
   role `01KXPB892XS90RX227MG5SJYRB` under their **original ULIDs** (PKs are
   free post-hard-delete); remove their `deleted_entities` tombstones so the ids
   don't resolve as both dead and alive. Heals usa-wa's org+role anchors with no
   producer action; unblocks their climbing REJECTED count. *Decision: id
   resurrection is the sanctioned repair for a wrongful merge (chosen over
   mint-new, which would force producer-side re-anchoring of org+role too).*
2. **Move identifier `3532`** from winner back to the resurrected predecessor.
3. **Re-point pre-2021 assignments** (reminted copies, window ≤ 2020 on the
   winner's Member role) onto the resurrected role. Reminted ULIDs are kept —
   PM never recorded the old→new mapping.
4. **Create the dated `succeeded_by` event** (predecessor → winner, at the
   2020/2021 re-key); with §3 this derives the predecessor's lifespan end.

**usa-wa#283 guidance (post after cleanup runs):** do *not* clear-and-re-produce
(would mint duplicates beside the reminted rows). Org/role anchors become valid
again as-is. For the 136 assignment anchors: sweep held `pm_assignment_id`s; on
404, re-resolve by natural key (person + start date on the healed role) and
adopt the new id. The 137th, unanchored assignment then produces normally.

## Out of scope

- People-merge same-type-identifier warning (follow-up candidate; no succession
  vocabulary for people).
- `resolve=chain_head` search param; `GET /orgs/{id}/lineage` endpoint (YAGNI
  until a consumer asks).
- Chain-aggregated admin timeline / grouped search / typeahead annotation.
- Any usa-wa schema change — their 1:1 key invariant is preserved by
  construction; they already produce and consume `succeeded_by`.
