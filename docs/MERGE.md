# power-map — Dedup & Merge UI

The duplicate-detection workflow and the merge-bar pattern that drives it, across
the people, orgs and roles list and detail screens.

---

## Dedup Workflow


1. **Banner** on org list when `count_org_duplicates(db) > 0` — `.alert--notice` with link to review screen
2. **Review screen** at `/admin/orgs/duplicates/` — shows potential duplicate pairs
3. **Actions:** merge, **link as successors** (orgs, #469), or dismiss per pair — HTMX partial response + OOB flash
4. **Nav badge** updates via lazy `hx-get="/admin/orgs/duplicate-count-badge/"` with `hx-trigger="load"`
5. **Cache:** `count_org_duplicates(db)` is TTL-cached (5 min, process-local). Call `_invalidate_dup_count_cache()` after merge, link, or dismiss
6. **Caveat:** cache is per-process — under multi-worker gunicorn, counts may lag up to 5 min per worker

### Merge vs. succession — who owns the key (#469)

An upstream re-key of a continuous org (WSL committee `3532` → `31651`) presents
as an identically-named pair, but the two rows are two **source records**: a
producer keys each external identifier value to its own org, and merging makes
that mapping N:1 — breaking the `API_ORGS.md` one-key-one-org contract and every
producer-held anchor (the #467 failure). The rubric is mechanical:

- Both candidates carry **the same external identifier type with distinct
  values** → two source records → **link as successors**. Merge stays possible
  behind an explicit acknowledgement (the source itself may have double-keyed).
- Same key twice, or a keyless hand-entered dupe → true duplicate → **merge**.

Machinery, all in `orgs_succession.py` + `orgs_merge.py`:

- **Guardrail:** `org_merge_preview` detects same-type/distinct-value external
  (`NOT is_internal`) identifiers; the modal names the type + both values,
  ships Execute disabled behind a `merge-identifier-conflict-cb` checkbox, and
  offers "Link as successors instead".
- **Link verb:** `GET .../link-successor-preview/...` modal (direction radio,
  defaulting from assignment-activity recency; optional date) → `POST
  /{pred}/link-successor/{succ}/` writes one `succeeded_by` event on the
  predecessor. A dated link also ends the predecessor's lifespan (#307 view).
  A pair already in one chain (either direction, transitively) is rejected with
  a warning — no double edges, no cycles.
- **Chain exclusion:** the duplicate detector excludes any pair inside one
  succession chain via `v_org_succession_pairs` (symmetric transitive closure
  of active `succeeded_by` edges — shared-successor siblings count). Linked
  chains never re-present as duplicates.
- **Continuity banner:** org detail shows the org's chain neighbours
  (`_succession_banner.html`), predecessor linking forward, successor back.

---

## Data Contract — what a merge may and may not change (#467)

The UI below decides *which* rows merge. This is what the server owes the data and
its consumers once it does. All five paths share it: org merge
([orgs_merge.py](../src/api/admin/orgs_merge.py) `_execute_merge`), person merge
([people_merge.py](../src/api/admin/people_merge.py)), and role merge
([orgs_roles.py](../src/api/admin/orgs_roles.py) `role_merge`).

**Identity is preserved, not re-minted.** Migrating a role assignment onto the
surviving role or person is an `UPDATE ... SET role_id` / `SET person_id`. The row
keeps its ULID, its `source_key_id`, and every ancillary row hanging off its
`entity_id`. Org merge used to migrate the loser role's assignments by inserting
copies and deleting the originals; the data survived and every producer-held
`pm_assignment_id` anchor broke silently. `_absorb_role` is the single primitive
now, shared by both of `_execute_merge`'s conflict passes — they were near-identical
blocks, which is how one came to drop `source_key_id` while the other preserved it.
Guard: `test_merge_identity_sweep.py` fails any merge module containing an
`INSERT INTO role_assignments`.

**The submitted pairs are scoped before they are trusted.** `merge_role_pairs`
arrives as form input, and `_absorb_role` hard-deletes the loser role it is handed
*and* publishes a `role/deleted merged_into=…` row for it. `_execute_merge`
therefore drops any pair whose winner role is not in the winner org or whose loser
role is not in the loser org, logging `merge_role_pair_out_of_scope`; a dropped
pair that is a genuine conflict is still resolved by the safeguard pass. The
preview modal only ever renders in-scope pairs, so this bites on manipulation, not
on normal use.

**The one destructive case, and its bound.** A loser assignment sharing
`(person_id, role_id, start_date)` with a winner row cannot be re-pointed —
`uq_role_assignment_person_role_start` holds that tuple once. Only those rows are
deleted. `dropped_assignments` counts them and the merge flash reports the count;
the preview modal states it *before* the merge, alongside how many assignments will
move with their ids intact. Archived assignments always re-point: the unique index
is partial on active rows, so a retracted tenure is never the collision.

**Every hard delete is announced.** The outbox triggers are `INSERT OR UPDATE` — a
`DELETE` emits nothing. So each deleted role and role_assignment gets a
`deleted_entities` row via
[`record_merge_tombstones`](../src/core/merge_signals.py), with `merged_into` naming
**that row's** survivor (the sibling assignment, the absorbing role) rather than the
parent merge's winner. That makes the feed signal a rebind, not a bare drop. A
parent org/person tombstone does not substitute: subscribers poll
`/api/v1/changes` filtered by their own per-entity subscriptions, so a tombstone
they do not hold tells them nothing. Guard:
`test_merge_identity_sweep.py` fails any admin module that hard-deletes a role or
role_assignment without tombstone machinery.

**Subscriptions gain the survivor, and keep the loser.** `mirror_subscriptions`
subscribes every watcher of a deleted id to the row that absorbed it, so a
subscriber does not stop seeing the data merely because it moved. It **adds** and
never moves: the feed resolves subscriptions when the consumer polls, not when the
merge runs (`changes.py`: `JOIN ... ON s.entity_id = ec.entity_id`), so deleting
the loser's subscription would erase the audience for the loser's own tombstone —
the subscriber holding the retired anchor is the only party that needs it. A
subscription on a retired id is a supported state: `_BATCH_RESOLVE_ENTITY_TYPE`
resolves ids through `deleted_entities` for exactly that reason. Retiring the stale
row is the consumer's to do (`DELETE /api/v1/subscriptions/{entity_id}`, or the bulk
form) — nothing prunes subscriptions server-side, so the set grows by one row per
merge. Note also that absorbing a collision emits **no** outbox row for the survivor
when the dropped row carried no ancillary: nothing about the survivor changed, so
`merged_into` is the signal to re-fetch it, not a promise of a follow-up event.

---

## Merge Bar Pattern


A fixed-position page overlay for bulk-select-and-merge on tables with potentially duplicate rows. Used on the Roles table of the org detail page (`role-merge.js`), the People list (`people-merge.js`), the Organizations list (`orgs-merge.js`), and the Roles list (`roles-merge.js`).

> **Shared engine (#250):** the three **list** flows (People, Orgs, Roles) are thin consumers of `merge-mode.js`, which exposes `window.createMergeMode(config)` — one boost-safe, document-delegated implementation parameterized by `{ tableId, btnId, btnWrapId, barId, listRegionId, rowAttr, nounPlural, buildPreviewUrl, untitledLabel }` plus the optional `{ previewTarget, groupAttr, canMerge, cannotMergeLabel }` (#251/#255) for group-scoped merges and portal override. `people-merge.js` / `orgs-merge.js` / `roles-merge.js` only supply config. `role-merge.js` (org-detail roles table) predates the factory and keeps its own `init()`-per-table lifecycle (#237). Load order in `base.html` matters: `merge-mode.js` must precede its consumers (`defer` preserves document order).

> **Preview modal (#255):** the Keep buttons no longer POST the merge directly with a bare `hx-confirm`. Each supplies `buildPreviewUrl(winner, loser, winnerEntry, loserEntry)` and the factory wires the Keep buttons to **`hx-get` the entity's `merge-preview` modal into `#merge-modal-portal`** (the shared portal in `base.html`); the modal *is* the confirm step. The modal form drives the actual merge POST and, on success in the list context, swaps the list region back in and closes the portal (shared `admin/_merge_modal_script.html`). Two shapes: **Orgs & People** post to a curated `.../merge-with/...` with per-name keep/drop checkboxes (loser's canonical name/acronym default **checked** = keep, #255 — this also applies to the detail/duplicates modals, which share the template) and `return_to=list`; **Roles** have no name selection, so their modal posts to the existing `.../merge/...` route. The lossy `keep_name_ids=None` bulk branch in `_execute_merge` was also made non-lossy (demote+transfer) as defense-in-depth. **#323:** the People modal annotates each `reading`/`romanization`/`mrz` row with a `(reading of "‹parent›")` note (`reading_of_name`, LEFT JOIN parent — same enrichment as the name-management read-row), and the curated drop keeps the parent of any *kept* child even when the parent is unchecked, so an explicitly-kept reading can't cascade away via `reading_of_id ON DELETE CASCADE`.

> **Same-org predicate (#251):** role merge is org-scoped (route `/admin/orgs/{org}/roles/{winner}/merge/{loser}/`, unique per `(organization_id, lower(title))`), but the Roles **list** is cross-org. So `roles-merge.js` supplies `groupAttr: 'orgId'` (the factory captures each row's `data-org-id` into the selection entry's `.group`) and `canMerge: (a, b) => a.group === b.group`. When two selected roles span different orgs the factory shows `cannotMergeLabel` ("Roles must be in the same organization to merge") and leaves both Keep buttons disabled — no preview `hx-get` is wired, so a doomed cross-org pair can't even open the modal. `buildPreviewUrl` reads the shared org from the winner entry's `.group`. People / Orgs omit these keys and are unaffected (always-mergeable).

> **Positioning:** `.merge-bar` is `position: fixed; bottom: 3rem; left: var(--sidebar-w); right: 0` — a full-width overlay above the sticky pagination, not a block contained by its parent element. It is placed inside `table-wrapper` in the DOM for logical proximity only; the containing block has no effect on layout.

### Required DOM structure

```html
<!-- Toggle button — outside the table -->
<button id="roles-merge-btn" class="btn btn--sm btn--secondary" type="button">Merge</button>

<!-- Table — data-org-id used by the JS to build merge POST URLs -->
<div class="table-wrapper" style="position:relative">
  <table id="roles-table" class="data-table" data-org-id="{{ org.id }}">
    <thead>
      <tr>
        <!-- Merge checkbox column — hidden until merge mode -->
        <th scope="col" class="merge-col" style="display:none;width:2rem;padding-right:0"></th>
        <th scope="col">Title</th>
        ...
      </tr>
    </thead>
    <tbody>
      {% for role in roles %}
      <tr data-title="{{ role.title or '' }}" data-role-id="{{ role.id }}">
        <td class="merge-col" style="display:none;padding-right:0">
          <input type="checkbox" name="merge-select" value="{{ role.id }}">
        </td>
        <td>...</td>
        ...
      </tr>
      {% endfor %}
    </tbody>
  </table>

  <!-- Merge action bar — hidden until merge mode; fixed-position overlay, placed here for DOM proximity -->
  <div id="roles-merge-bar" class="merge-bar" style="display:none">
    <span class="merge-bar__label">Merge roles:</span>
    <button class="btn btn--sm btn--primary merge-bar__keep-a" type="button"></button>
    <button class="btn btn--sm btn--primary merge-bar__keep-b" type="button"></button>
  </div>
</div>
```

### JS data contract (`role-merge.js`)

| Element / attribute | Purpose |
|---|---|
| `#roles-table` | Root — JS attaches change delegation and reads `data-org-id` |
| `data-org-id` | Used to build `/admin/orgs/{id}/roles/{keep}/merge/{discard}/` POST URL |
| `#roles-merge-btn` | Toggle button — JS toggles text ("Merge" ↔ "Cancel merge") and classes (`btn--secondary` ↔ `btn--ghost`) |
| `#roles-merge-bar` | Action bar — shown when merge mode is active |
| `.merge-col` | Column cells hidden/shown en bloc on mode toggle |
| `input[name="merge-select"]` | Per-row checkbox; `value` = role ID; `data-title` read from the parent `<tr>` |
| `tr[data-title]` | Used by both merge checkbox reading and the inline roles filter |
| `.merge-bar__keep-a` / `.merge-bar__keep-b` | JS sets `hx-post` and `hx-confirm` dynamically; calls `htmx.process()` after attribute mutation |

### Progressive disclosure states

| Checked count | Label | Button A | Button B |
|---|---|---|---|
| 0 | "Select 2 roles to merge:" | `—` (disabled) | `—` (disabled) |
| 1 | "Select 1 more:" | Selected role title (disabled) | `—` (disabled) |
| 2 | "Merge roles:" | `Keep "A"` (enabled, `hx-post` set) | `Keep "B"` (enabled, `hx-post` set) |

Max selection is 2; additional checkboxes are `disabled` once two are checked.

> This table describes `role-merge.js` (org-detail table), which still wires `hx-post` + `hx-confirm` directly. The **list** flows differ since #255: at two selections the factory wires `hx-get` to open the preview modal instead (see the "Preview modal (#255)" note above) — no `hx-post`/`hx-confirm` on the Keep buttons.

### Exit conditions

Merge mode exits automatically after a successful merge (JS listens for the `showFlash` event dispatched by the flash system after the server responds).

### Client-side roles filter

The same `role-merge.js` also handles the roles filter input (`#roles-filter`). It filters `tr[data-title]` rows client-side by comparing `data-title.toLowerCase()` against the input value — no server round-trip.

```html
<input type="search" id="roles-filter" placeholder="Filter roles…"
       class="filter-card__search">
```

### List variants (`people-merge.js`, `orgs-merge.js`, `roles-merge.js`)

Both consume the shared `merge-mode.js` engine; the table below contrasts the list lifecycle against the org-detail roles table. Same DOM contract, five deltas:

| Delta | Roles (org detail) | People list |
|---|---|---|
| Table id / data-attrs | `#roles-table[data-org-id]`, rows carry `data-role-id` | `#people-table` (no `data-org-id`), rows carry `data-person-id` |
| Swap target | `#roles-table tbody` (rows only) | `#people-list-region` (entire region: table, caption count, sticky pagination) — keeps post-merge totals consistent |
| Sticky pagination | None on org detail roles table | `.pagination--sticky` present; JS hides it on `enterMergeMode`, restores on `exitMergeMode` / `showFlash` — single sticky slot, no overlap |
| Filter input | Inline client-side `#roles-filter` | None; the list uses server-side search via the filter card |
| Region swap survival | Roles table never swapped wholesale | Filter card swaps `#people-list-region` on every search / status / page-size change. people-merge.js uses lazy element resolution, document-level event delegation, and re-applies merge-mode visual state on `htmx:afterSwap` so the UI keeps working through any swap |
| Boost survival (#249) | `role-merge.js` re-runs an idempotent `init()` on `htmx:load`, guarded by `table.dataset.mergeBound` | Loaded **site-wide from `base.html`** (was wrongly in the People list's `extra_head`, which hx-boost strips — Merge was a silent no-op). The toggle click is document-delegated (button element is replaced on each boosted nav); a `#people-list-region` partial swap **preserves** merge mode while a boosted full-page arrival (detected via `htmx:load`, same signal as role-merge.js — its loaded subtree carries the page-header merge button, a region swap's does not) **resets** to a clean state (no stale mode/selection) |

Since #255 the Keep button opens the preview modal (`buildPreviewUrl` → `GET /admin/people/{winner_id}/merge-preview/{loser_id}/?ctx=list`); the modal then POSTs the curated merge to `/admin/people/{winner_id}/merge-with/{loser_id}/` (with `keep_name_ids` + `return_to=list`). Both that route and the bulk `person_merge` (`/merge/`) share the list-region re-render: the route ([src/api/admin/people_merge.py](../src/api/admin/people_merge.py)) detects the list flow via `HX-Target == "people-list-region"` and returns `_region.html` instead of `_duplicates_region.html`. Filter state (`q`, `status`, `page`, `page_size`) is parsed from `HX-Current-URL` so the refreshed region respects the user's active filters; the shared query helper lives in [src/api/admin/people_queries.py](../src/api/admin/people_queries.py) and is used by both the list route and the merge routes' list-flow branch.

Merge button always renders (the People list mixes active + archived via the status filter); the `_btn-wrap` shows the `not-allowed` cursor + tooltip when fewer than 2 rows are visible in the current tbody. Selection state clears on `htmx:afterSwap` (search, pagination, page-size change) — cross-page selection persistence is intentionally not implemented.

The **Orgs list** (`orgs-merge.js`) mirrors the People list exactly, with rows carrying `data-org-id`; since #255 the Keep button opens `GET /admin/orgs/{winner}/merge-preview/{loser}/?winner={winner}&ctx=list` and the modal POSTs to `org_merge_with` (`/merge-with/`, curated names/acronyms + `return_to=list`). The route ([src/api/admin/orgs_merge.py](../src/api/admin/orgs_merge.py)) detects the list flow via `HX-Target == "orgs-list-region"` and returns `_region.html` instead of `_duplicates_region.html` (shared `_render_orgs_list_region` helper, used by both `org_merge` and `org_merge_with`); the shared query helper is [src/api/admin/orgs_queries.py](../src/api/admin/orgs_queries.py). The `HX-Current-URL` filter parsing is shared with People via [src/api/admin/list_filters.py](../src/api/admin/list_filters.py) (`parse_list_filters`); each route binds its own valid-status set. One difference from People: the org status axis is three-valued (`active` / `inactive` / `archived`), so the Orgs caller passes `inactive` as a valid status — copy-pasting the People set would collapse it to `active`. Page-size bounds live in [pagination.py](../src/api/admin/pagination.py) (`PAGE_SIZE_*`), used by both the route `Query` validators and the parser. A list-flow merge that drops conflicting role assignments appends the count to the flash (`_dropped_assignments_note`, shared with the detail-flow `org_merge_with`).

The **Roles list** (`roles-merge.js`, #251) is the cross-org variant, and differs from People/Orgs in three ways:

- **Namespaced IDs.** Both the org-detail roles table and the roles list would otherwise use `#roles-table` / `#roles-merge-*`. The list uses `#roles-list-table` / `#roles-list-merge-btn` / `#roles-list-merge-bar` / `#roles-list-merge-btn-wrap` so `role-merge.js` (which binds `#roles-table`) and `roles-merge.js` never double-bind. Rows carry `data-role-id` (row identity / count via `rowAttr`) **and** `data-org-id` (the same-org key via `groupAttr`).
- **Same-org predicate.** See the "Same-org predicate (#251)" note in § Merge Bar Pattern — `canMerge` gates the 2-selection enable point; cross-org pairs show the hint and stay disabled. Since #255 the Keep button opens `GET /admin/orgs/{org}/roles/{winner}/merge-preview/{loser}/?ctx=list` (org-scoped, built from the shared `entry.group`); the confirmation-style modal then POSTs to the org-scoped merge route below.
- **Backend reuses the org-detail merge route.** The merge POST needs no curated endpoint (roles have no name/acronym selection), so the modal posts to the existing `role_merge` (`/admin/orgs/{org}/roles/{winner}/merge/{loser}/`) in [src/api/admin/orgs_roles.py](../src/api/admin/orgs_roles.py), which gains an `HX-Target == "roles-list-region"` branch returning `admin/roles/_region.html` (filters re-derived from `HX-Current-URL`); without that header it keeps returning the org-detail `_role_rows.html` partial. A read-only `role_merge_preview` GET route (#255) renders the modal. The shared query helper is [src/api/admin/roles_queries.py](../src/api/admin/roles_queries.py); filter parsing reuses `parse_list_filters` with `extra_text_params=("org_q",)` for the roles-only organization-name filter (status axis is two-valued — roles have no `active` flag).
