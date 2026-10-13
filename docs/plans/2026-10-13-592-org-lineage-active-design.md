---
title: "#592 usa-wa org_lineage → org link events; organizations.active stays PM's"
date: 2026-10-13
status: approved
---

# #592 org_lineage and organizations.active

## Problem

usa-wa#447 (PR usa-wa#464, nightly `v20261004T080416Z-d68664`) ships two things that #592 has to decide on:

- **`org_lineage` 1.0.0, a new dataset.** It carries 162 current committee links (`succeeded_by` / `split_from` / `merged_with`). The key is `(subject_entity_id, slug, linked_entity_id)`. Each row also has both WSL ids, `effective_year`, `evidence_url` and `notes`. When a link is superseded, it drops out of the next version.
- **`organizations` 1.2.0, INV2.** The subject of a current `succeeded_by` or `merged_with` link now publishes `active=false`. The `contract_hash` did not change, so PM already holds 1.2.0 and ignores `active` (#591).

#592 asked five questions about `active` (Q1–Q5). The lineage dataset adds two more: whether to take the links, and how to reconcile the Civic Health edge PM curated by hand on 2026-09-30 (`01KWBJ5X…` `succeeded_by` `01M3QSSY…`). That edge is undated in PM. usa-wa publishes it with `effective_year` 2026.

## Audit (2026-10-13, prod read-only + org_lineage pulled to scratch)

- **org_lineage contents:** 162 links: 105 `succeeded_by`, 34 `merged_with`, 23 `split_from`. All 162 are dated. Both ends of every link resolve to a live `usa_wa` org anchor (220 anchors, all `live`).
- **PM already holds every link:** 162 of 162 match on subject, slug, linked org and year. The only difference is Civic Health 35341→36500, which PM holds undated and the dataset dates 2026. PM has no extra link between anchored orgs, and none of its links touches an unanchored org. Three archived `succeeded_by` rows exist in PM and are out of play.
- **Multi-successor links.** Four subjects have more than one outgoing link of one type, and one predecessor has three successors. The admin chain guard (`_in_same_chain`) would refuse the second edge. `uq_entity_events_succession_edge` (unique per pair) does not.
- **`active` agrees on 220 of 220 anchored orgs.** INV2 flipped only 35341, which PM had already set inactive by hand. Breakdown:
  - committees: 152 inactive and dissolved, 34 active
  - Joint/Other: 21 active, 2 inactive (GFIE, Civic Health 35341)
  - parties: 2 active, 6 inactive
  - chambers and legislature: all active
- **Applier fit.** A child binding with `match: key` already takes multi-column keys. Retraction-as-report for owned event types exists (`desired_entity_events`, `owned_event_types: [dissolved]`). Two gaps:
  1. The manifest requires an overlay field on every child binding, but overlay slots are scalar per `(entity, field)`, so a single edge cannot be pinned.
  2. The retract entry rebuilds its row from the type column alone.

## Decisions

| # | Question | Decision |
|---|---|---|
| L1 | Take `org_lineage`? | Yes. The applier owns `succeeded_by` / `merged_with` / `split_from` on **anchored pairs** (both ends in scope). |
| L2 | A curator disagrees with a published link | **Fix it upstream; PM locks the link.** No overlay. Admin and the observation API refuse writes to dataset-owned links. |
| L3 | usa-wa drops (supersedes) a link | The applier **archives** it. The archive counts under the existing `archives: 0` threshold, so execution is gated. If the link reappears later, it is inserted fresh. |
| L4 | Civic Health edge | No hand action. The first run updates `event_year` from null to 2026, and the edge locks from then on. |
| Q1 | Is `active` worth owning? | **No.** For committees, `dissolved` already carries it. For links, INV2 already carries it. |
| Q2 | Applier `active` vs `dissolved` | Moot. The applier never writes `active`, and #320 is untouched. |
| Q3 | Rollover all-inactive gap | Moot. The applier writes nothing, so nothing mass-deactivates. |
| Q4 | `organization.active` overlay slot | Not built. |
| Q5 | Parties | PM stays authoritative (#442). |
| H | Hint | A warn-level dbt test names anchored orgs with a null `last_biennium` (Joint/Other, parties, chambers) where usa-wa's `active` ≠ PM's. That covers the GFIE class. Committees are excluded, so the rollover stays quiet. |

## Design

### §1 Lineage pipeline and applier binding

**Source and pin (#536).** `sources.yml` gains `usa_wa.org_lineage`, read with `all_varchar`, with `schema_major: 1` and `contract_hash: "sha256:a7585012f837a8c0d2c79e43aff97c82b60a0b176702c4b2e63420e39d8dc019"`. `src/core/ingestion/mapping/__init__.py` maps it to `PM_SRC_ORG_LINEAGE`. Once deployed, the nightly pull lands it.

**`stg_usa_wa__org_lineage`.** Trims each column and casts `effective_year` to an integer. A `not_null` + `accepted_values` test (severity **error**) restricts `slug` to the three link types: an unknown slug fails the build instead of being ignored.

**`desired_organization_lineage` mart.** One row per published link, with both ends resolved through `desired_organizations` with a non-null `pm_id`:

| column | type | from |
|---|---|---|
| `producer_id` | VARCHAR | subject's usa-wa id |
| `pm_id` | VARCHAR | subject's PM id |
| `event_type` | VARCHAR | `slug` |
| `linked_producer_id` | VARCHAR | linked org's usa-wa id |
| `linked_pm_id` | VARCHAR | linked org's PM id |
| `event_year` | INTEGER | `effective_year` |

A link drops out when either end is a create, archived or unresolved. The warn test `unresolved_org_lineage` names it, and it lands the night after its create executes (precedent: `unresolved_org_parents`). Key uniqueness gets its own singular test (`desired_organization_lineage_key_unique`).

**Manifest table:**

```yaml
desired_organization_lineage:
  entity: organization
  key: [producer_id, event_type, linked_producer_id]
  pm_key: pm_id
  retraction: archive
  owned_event_types: [succeeded_by, merged_with, split_from]
  owned_columns: [event_year]
  overlay: none            # links are locked (§2), not pinned
  types: {producer_id: VARCHAR, pm_id: VARCHAR, event_type: VARCHAR,
          linked_producer_id: VARCHAR, linked_pm_id: VARCHAR, event_year: INTEGER}
  target:
    shape: child
    table: entity_events
    parent: entity_id
    columns: {event_type: event_type_id, linked_pm_id: linked_entity_id, event_year: event_year}
    constants: {entity_type: organization, linked_entity_type: organization}
    match: key
    key_columns: [event_type, linked_pm_id]
    lookups: {event_type: {table: entity_event_types, from: slug, to: id}}
    archived: archived_at
    insert_defaults: {visibility: public}
```

Only the year is owned. PM keeps month and day, as it does for `dissolved`.

**Applier changes:**

1. **Child `retraction: archive`** (new). On an in-scope parent, a live row of an owned type that the snapshot no longer publishes becomes an `archive` entry: `archived_at = NOW()` where `archived_at IS NULL`. No anchor is stamped. A child row is not an anchor, and once the lock is in place PM never archives an owned link itself. The entry counts under `archives`, which `thresholds.archives: 0` gates.
2. **Absence scope is the anchored pair.** A PM row of an owned type counts for retraction only when its `linked_entity_id` is also in scope. Links to unanchored orgs stay invisible.
3. **Retract and archive entries carry every key column**, not just the type.
4. **Manifest loader.** `overlay: none` is accepted only on a child binding whose `owned_event_types` all lie in the lock's type list. Anything else stays a `ManifestError`.

`desired_entity_events` (`owned_event_types: [dissolved]`) is unchanged. Its owned set and the new one don't overlap, so each binding sees only its own types.

**Expected first run:** 161 `noop`, 1 `update` (Civic Health, `event_year` null → 2026), 0 inserts, 0 archives, verdict `clean`. `v_org_lifespan.ended_on` for 35341 stays `2026-01-31`: the `dissolved` 2026-01 event is earlier than the 2026-12-31 a year-only link derives.

**Accepted as-is:**
- Applier link writes do not expire the org dup-count cache. Its 5-minute TTL self-heals, and the run is a nightly batch.
- Multi-successor links are written directly. The admin chain guard applies only to admin's own creation path.

### §2 The lock

**Rule.** A link is *dataset-owned* when all three hold:
- its type is in `DATASET_OWNED_LINK_TYPES` (`succeeded_by`, `merged_with`, `split_from`)
- its subject org is in scope
- its linked entity is an org that is also in scope

"In scope" means a live anchor in `producer_crosswalk`, the same test as `src.core.curation_overlay.in_scope`.

**Predicate.** One core function, `dataset_owned_link(conn, entity_type, entity_id, slug, linked_type, linked_id) -> bool`, in `src/core/` (WA-free). Admin cannot read the manifest, because the mapping package imports dbt. A drift test therefore pins the constant to the manifest's `owned_event_types` for `desired_organization_lineage`, the same pattern as the overlay-slot registry test.

**Doors.** Every door checks the row's state after the write; an edit also checks its state before. Without that, an edit could move an unlocked event into a locked one, or free a locked one by changing its type or link.

| Door | Behaviour on a dataset-owned link |
|---|---|
| Admin `POST /admin/orgs/{pred}/link-successor/{succ}/` (also the dup-detection "link as successors" modal) | Refused. Warning flash: "Links between usa-wa organizations come from its org_lineage dataset; change it upstream." The non-HTMX path redirects with that flash, keeping `test_mutation_fallback_sweep.py` green. |
| Events panel: add, edit-row, archive, unarchive, hard delete (`_events_shared.py`) | Same refusal, same fallback. The row renders read-only with a `usa-wa` badge instead of its edit and archive controls. |
| Observation API: event create, refine and `op="retract"` on an organization | `REJECTED`, with the new `EventRejectReason.EVENT_OWNED_BY_DATASET` (`event_owned_by_dataset`). Otherwise the next run would archive the write, and a gated archive blocks the nightly. |

**Not locked:**
- Org merge re-homing (#611) and hard-delete guards (#608). These are system operations, and the next run reconciles them.
- Links whose two ends are not both anchored.
- Every other event type, `dissolved` included (#498 pins).

**Public contract:** one new `EventRejectReason` value. It is additive, not breaking. Bump the minor version and regenerate the client (#618).

### §3 The `active` hint

- `stg_usa_wa__organizations` gains `active` (`try_cast` to boolean).
- `scripts/export_pm_tables.py` exports `organizations` (`id`, `active`, `archived_at`) to `_pm/`. That adds a `pm.organizations` source and `PM_SRC_PM_ORGANIZATIONS`, and extends the export column-order test.
- `tests/org_active_divergence.sql` (severity **warn**) names every org that is in `desired_organizations` with a `pm_id`, is unarchived in PM, has a null `last_biennium` in the producer, and whose usa-wa `active` IS DISTINCT FROM PM's `active`. Today it names none.
- It surfaces as the build's `WARN` line in the journal. A curator acts through `set_org_active`. The applier never reads it.
- `sources.yml` organizations comment: 1.2.0 (INV2); `active` is read only by the hint.

### §4 Testing, docs, ship

**TDD, red first:**

- **dbt fixtures:**
  - `slug` accepted values
  - the mart's both-ends resolution, and dropping a create or archived end
  - `unresolved_org_lineage` and the key-unique test
  - `org_active_divergence`: fires on a null-biennium mismatch, silent on a committee mismatch, silent when they agree
  - the `types` declaration holding from an empty store (#569)
- **Manifest:** `overlay: none` is accepted on the lineage binding and refused on an unlocked child binding and on a column binding.
- **Applier (units):**
  - inserts a new link
  - updates the year
  - archives a dropped owned link on an anchored pair
  - ignores a link to an unanchored org and every unowned type
  - entries carry the full key
  - an archive counts toward `archives` and blocks at 0
  - the child archive write sets `archived_at`
  - a reappearing link is a fresh insert
- **Applier (integration, `-m integration`, run serially):** an execute against the test DB writes and archives `entity_events`.
- **Lock:** the predicate's truth table (both / one / neither anchored, each type, a person-linked event); each admin door refuses, with both the HTMX and the redirect path; the row renders read-only; the API rejects create, refine and retract; an edit that would move a row into or out of the lock is refused; the drift test pins the constant to the manifest.
- **Real-snapshot test** extended to `org_lineage`. It skips without the store.

**Docs:**

| Doc | Change |
|---|---|
| `RUNBOOK_DESIRED_STATE.md` | the lineage binding, gated child archives, the hint |
| `ADMIN_OVERLAY.md` | locked vs pinned |
| `OBSERVATIONS.md` / `API_EVENTS.md` | the reject reason |
| `MERGE.md` | re-homing is not locked |
| `manifest.yml` header | the `overlay: none` exemption |

**Version:** 0.63.0 → 0.64.0 in `pyproject.toml`, `package.json` and the `uv.lock` client entry, plus `uv run python -m scripts.regenerate_client`.

**Ship order** (merge, deploy and restart are each authorized separately):

1. PR, CR rounds, merge.
2. Pull into main, then restart `power-map`.
3. `scripts.pull_datasets`, which lands `org_lineage`. #553: pull after the deploy, before the build.
4. Dry run: expect 161 noop / 1 update / 0 archives, `clean`.
5. The nightly streak executes.
6. Verify `event_year = 2026` on the Civic Health edge.
7. Post the decisions on #592, then close it.

## Out of scope

- Writing `organizations.active` from the applier, and an `organization.active` overlay slot (Q1–Q4).
- Per-edge overlay pins for links (L2 rejected the option).
- A lineage endpoint or chain-aggregated UX (#469 follow-up candidates).
- Loosening the contract gate (#591).
- Same-night landing of a link to an org the same run creates. It lands the next night.
