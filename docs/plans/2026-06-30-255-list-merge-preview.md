---
title: Entity list-screen merges — route through preview modal (fixes Orgs data loss)
date: 2026-06-30
status: approved
issue: 255
---

# Entity list-screen merges — route through preview modal

## Problem

The **Orgs list-screen one-click merge** silently hard-deletes the loser's
**canonical name and canonical acronym**. Path: list "Keep" button →
`hx-post /admin/orgs/{winner}/merge/{loser}/` → `org_merge` →
`_execute_merge(keep_name_ids=None)`. The `None` branch transfers only
`is_canonical=FALSE` names and `DELETE`s the `is_canonical=TRUE` row
(`orgs_merge.py:209-245`). It deletes rather than demote+transfer because
`uq_org_canonical_name` permits one canonical per org.

Confirmed on prod (JLARC org `01KV6PQGDD…`): loser canonical name
"JLARC - Joint Legislative Audit & Review Committee" destroyed; re-added by
hand 2 min post-merge. Identifier `-5`, links, contacts transferred fine.

The detail page + duplicates screen avoid this — they open
`_merge_preview_modal.html` which lets the user keep the loser's canonical name
as an alias. The **list screen bypasses the modal** (bare `hx-confirm` +
direct POST in `merge-mode.js`).

People (`merge_person_into`) and Roles (`role_merge`) list merges do **not**
have a data-loss bug — People demotes+transfers all names (incl. deadnames,
#121); Roles have no canonical-name concept. They lack a rich preview entirely.

## Goal

Route **all three entity list-screen merges** (Orgs, People, Roles) through a
rich preview/confirm modal — replacing the bare `hx-confirm` — matching the
Orgs modal's structure. Fixes the Orgs data loss as a side effect.

Scope = the three **list** screens. Out of scope: People/Roles *duplicates*
screens (still post directly — possible follow-up), and the Orgs/People detail
+ duplicates flows (already use, or unaffected by, the modal).

## Approved approach

### Shared mechanism

`#merge-modal-portal` (base.html:110) is site-wide on every admin page.
Opening a modal = `hx-get <preview-route>` → `hx-target="#merge-modal-portal"
hx-swap="innerHTML"`. The modal contains the form that POSTs the merge.

All three `/merge/` endpoints already re-render the list region when
`HX-Target == "<entity>-list-region"` (org_merge:475, person_merge:385,
role_merge:213). **The modal's POST reuses that existing list branch** — post
to the entity's `/merge/` with `hx-target="#<entity>-list-region"`. On success
the list region swaps (preserving merge mode via the factory's existing
`onRegionSwap`) and the modal closes the portal via an after-request hook.

### `merge-mode.js` factory change

Add config:
- `buildPreviewUrl(winnerId, loserId, winnerEntry, loserEntry)` → GET url of the
  preview modal (carries `ctx=list`).
- `previewTarget` (default `#merge-modal-portal`).

When two rows are selected and `canMerge` passes, wire the Keep buttons with
`hx-get=buildPreviewUrl(...)`, `hx-target=previewTarget`, `hx-swap=innerHTML`
— and **remove** the old `hx-post`/`hx-confirm`. The bare confirm is gone; the
modal is the confirm step. `disableKeepBtn` also clears `hx-get`.

### Orgs (modal exists; fix the loss)

1. **`_execute_merge` `keep_name_ids=None` branch → non-lossy** (defense in
   depth). Replace the delete-canonical with demote+transfer+dedup, mirroring
   the contacts/links pattern: delete loser names whose `lower(name)` already
   exists on the winner, then `UPDATE … SET organization_id=winner,
   is_canonical=FALSE` for the rest. Same for acronyms. This alone fixes the
   reported bug even if the UI routing regresses.
2. **`org_merge` (`/merge/`) accepts** optional `keep_name_ids`,
   `keep_acronym_ids`, `merge_role_pairs` form fields (like `org_merge_with`),
   passed to `_execute_merge`. Its list-region branch already exists.
3. **Modal form action is `ctx`-aware**: `ctx=list` → `hx-post /orgs/{w}/merge/{l}/`,
   `hx-target=#orgs-list-region`; default → current `/merge-with/`,
   `hx-target=body` (detail/duplicates, redirect to detail). Same keep/drop
   checkboxes in both.
4. **`merge-preview` route** passes `ctx` through to the template.
5. Factory: Orgs `buildPreviewUrl` →
   `/admin/orgs/{w}/merge-preview/{l}/?winner={w}&ctx=list`.

### People (build modal — confirmation style)

New read-only `GET /admin/people/{winner}/merge-preview/{loser}/?ctx=list` →
`_merge_preview_modal.html` (people): kept/deleted header + swap, reassignment
counts (role assignments, contacts, links, addresses, identifiers), and a
"names preserved as aliases" note. **No per-name keep/drop** — People merge
must inherit ALL names incl. deadnames/hidden (#121); display respects
visibility (`visible_names_filter()` / `visibility='public'`; hidden names
summarized as a count, never enumerated). Modal posts to the **existing**
`person_merge` `/merge/` with `hx-target=#people-list-region` (no merge-logic
change). Factory: People `buildPreviewUrl`.

### Roles (build modal — confirmation style)

New read-only `GET /admin/orgs/{org}/roles/{winner}/merge-preview/{loser}/?ctx=list`
→ roles modal: kept/deleted role title + swap, # assignments reassigned, #
duplicate assignments dropped (same person+start_date), notes-merge note. No
keep/drop (no names). Posts to existing `role_merge` `/merge/` with
`hx-target=#roles-list-region`. Same-org gate already enforced by factory
`canMerge` + route 409. Factory: Roles `buildPreviewUrl` (org id from
`entry.group`).

### Portal close on success

Modal form gets `hx-on::after-request` (or equivalent) to clear
`#merge-modal-portal` on `event.detail.successful`, so the modal dismisses when
the list region swaps in.

## Decisions (resolved)

- **D1 — keep/drop everywhere (RESOLVED: yes).** Per-name keep/drop checkboxes
  on the People modal too, for parity. Implications, handled carefully:
  - `merge_person_into` gains an optional `keep_name_ids` param. When provided,
    only those loser names transfer (demoted non-canonical); the rest are
    deleted. When `None`, current behavior (inherit ALL — #121) is preserved, so
    script/test callers are unaffected.
  - **All loser names default to CHECKED (keep)** in every modal (People AND
    Orgs — including the loser's *canonical* name). The safe, non-lossy choice is
    the default; an admin must deliberately uncheck to drop. This directly
    prevents the reported loss and honors the #121 spirit (nothing dropped
    unless explicit). NB: this changes the existing Orgs modal default, which
    currently leaves the loser canonical name unchecked.
  - People modal name display follows the admin person-detail pattern (which
    already shows all names incl. hidden/deadnames for management); each name's
    visibility is shown as a badge so an admin unchecking a sensitive name does
    so knowingly. We never silently drop.
  - Roles: no names → no keep/drop; confirmation + counts + conflicts only.
- **D2 — Land on list after list-merge** (chosen): re-render the list region
  (stay on list, preserve filters + merge mode), reusing existing `/merge/`
  list branches. Detail/duplicates Orgs flows still redirect to winner detail.

## Steps (TDD; Red → Green → Refactor)

1. **Orgs regression (the bug).** Failing integration test: list-style merge
   (no keep ids) preserves loser's canonical name + acronym as non-canonical
   aliases on winner. Then fix `_execute_merge` None branch. Green.
2. **Orgs `/merge/` keep-ids + ctx-aware modal.** Tests for `org_merge`
   accepting keep_name_ids/acronym_ids/role_pairs; modal renders list-targeted
   form under `ctx=list`. Implement.
3. **Factory rewire.** Update vitest (test_orgs_merge_js / test_people_merge_js
   / test_roles_merge_js / test_merge_mode_js) to expect `hx-get` → portal
   instead of `hx-post`/`hx-confirm`. Implement `buildPreviewUrl`/`previewTarget`.
4. **People preview route + modal.** Route test (counts, visibility-safe names,
   ctx form action) + modal template. Wire factory.
5. **Roles preview route + modal.** Route test (assignment counts, conflict
   count, same-org) + modal template. Wire factory.
6. **Portal-close + end-to-end.** Confirm modal dismisses and list refreshes;
   flash fires; dup badge refresh where applicable.

## Test plan

- pytest: new preview routes (auth 307, 404s, ctx form action, counts,
  visibility), Orgs merge non-lossy regression, `/merge/` keep-ids honoring.
- vitest: factory opens modal (hx-get/portal), no hx-post/hx-confirm on Keep;
  per-entity buildPreviewUrl; same-org gate unaffected (Roles).
- Manual on dev :8001 — each list screen: select 2 → preview modal → execute →
  list refreshes, modal closes, loser data preserved.

## Risks

- Factory is shared + boost-sensitive (#249/#250). Keep changes additive;
  preserve lazy resolution + delegated listeners.
- Visibility leak in People modal — must route all name display through the
  visibility filter; never enumerate hidden/deadnames.
- `htmx.process()` must run on the rewired Keep buttons (already called).
- Modal close timing vs list-region swap (single request, two effects).
