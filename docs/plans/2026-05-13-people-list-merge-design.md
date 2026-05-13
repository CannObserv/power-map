# People List — Merge Action

**Issue:** #137
**Date:** 2026-05-13
**Status:** Approved

## Goal

Add a manual "Merge" action to the People list screen (`/admin/people/`), mirroring the [role merge UX on the org detail page](2026-04-05-role-merge-design.md). Two-person checkbox selection → sticky "Keep A / Keep B" bar → reuse the existing `person_merge` route. Provides an entry point that doesn't require the candidate pair to be flagged by automated duplicate detection.

## Approved Approach

### Backend reuse — no new merge logic

`POST /admin/people/{winner_id}/merge/{loser_id}/` already exists at [src/api/admin/people_merge.py:261](../../src/api/admin/people_merge.py#L261) and is fully functional (used today by the People Duplicates review screen). The route already:

- Wraps [`merge_person_into()`](../../src/api/admin/people_merge.py#L80) inside a single transaction.
- Reassigns names / role_assignments / polymorphic refs (contact_methods, links, entity_addresses, import_provenance, field_confidence) / identifiers / duplicate_dismissals.
- Calls `invalidate_person_dup_count_cache()`.
- Returns `_duplicates_region.html` partial on HTMX, redirect on non-HTMX.

**One small route change** — branch the HTMX response on the caller:

```python
if is_htmx(request):
    target = request.headers.get("HX-Target", "")
    if target == "people-table-body":
        # Called from the People list — re-run list query honoring the user's
        # current filters (parsed from HX-Current-URL) and return refreshed rows.
        q, status, page, page_size = _parse_list_filters_from_hx(request)
        people, ... = await _query_people(db, q=q, status=status, page=page, page_size=page_size)
        return templates.TemplateResponse(
            request,
            "admin/people/_rows.html",
            {"people": people},
            headers=flash_trigger("success", body),
        )
    # Existing duplicates flow — unchanged
    return templates.TemplateResponse(request, "admin/people/_duplicates_region.html", ...)
```

**Filter-state preservation: parse `HX-Current-URL` (option a).** The list flow's Keep buttons stay attribute-free of filter state; the route reads `HX-Current-URL` (a standard HTMX request header), pulls `q`, `status`, `page`, `page_size` query params, and re-runs the list query with those values. Defaults match the list route's defaults if any param is missing or unparsable. No template change to the buttons; one small helper in `people_merge.py`.

If `page` × `page_size` lands past the end after a merge (the loser was the only row on the last page), clamp `page` to the new last page. Simple `max(1, min(page, total_pages))` calculation.

### UI: Checkbox selection mode with sticky merge bar (footer-swap)

**Entering merge mode:**
- "Merge" button next to "+ Add person" (`btn--sm btn--secondary`).
- Disabled when fewer than 2 people in the current tbody (same disabled-wrapper pattern as `roles-merge-btn-wrap`).
- JS toggles `data-merge-mode` on `#people-table`; checkbox column reveals (CSS-gated on `[data-merge-mode]`).
- Button text changes to "Cancel merge".

**Selecting rows:**
- Cap at 2 selections; subsequent checkboxes `disabled` (same as roles).
- Single `change` listener delegated on the table.

**Action bar — swaps with sticky pagination, does not stack:**

This is the divergence from role-merge: the org detail roles table has no pagination, so `roles-merge-bar` could sit at `position: sticky; bottom: 0` unopposed. The People list has [`.pagination--sticky`](../../src/static/admin/admin.css) occupying that slot.

**Decision: one strip, two states.** While in merge mode, hide `.pagination--sticky` and show `#people-merge-bar` in the same sticky slot. Top-of-page pagination (already rendered above the table per the [admin list refactor](2026-03-21-admin-list-refactor-design.md)) remains available for navigation.

```
Normal:                            Merge mode:
┌─ pagination (top) ───────┐       ┌─ pagination (top) ───────┐
│  table                   │       │  table (+ checkboxes)    │
│  ...                     │       │  ...                     │
├─ .pagination--sticky ────┤       ├─ #people-merge-bar ──────┤  ← same slot
└──────────────────────────┘       └──────────────────────────┘
```

Mechanics: `people-merge.js` adds `style.display='none'` to `.pagination--sticky` in `enterMergeMode()`, restores it in `exitMergeMode()`. No CSS positioning changes. No z-order conflict.

**Visual distinction:** `#people-merge-bar` uses an accent background (`var(--color-warning-soft)` or analogous) and a left border stripe so the user never mistakes the merge bar for pagination. Same vertical height to avoid layout shift on toggle.

**Progressive disclosure** — identical to role-merge:

| Checked count | Label | Button A | Button B |
|---|---|---|---|
| 0 | "Select 2 people to merge:" | `—` (disabled) | `—` (disabled) |
| 1 | "Select 1 more:" | `Selected: "<name>"` (disabled) | `—` (disabled) |
| 2 | "Merge people:" | `Keep "A"` (enabled, `hx-post` set) | `Keep "B"` (enabled, `hx-post` set) |

Each Keep button: `hx-post="/admin/people/{winner_id}/merge/{loser_id}/"`, `hx-target="#people-table-body"`, `hx-swap="innerHTML"`, `hx-confirm="Merge \"<loser>\" into \"<winner>\"? This cannot be undone."`.

**After merge:**
- Server returns refreshed `_rows.html` for `#people-table-body` + `HX-Trigger: showFlash` header.
- `showFlash` listener exits merge mode (hide checkboxes, restore pagination bar, reset button text). Same hook role-merge uses.

### Selection state across HTMX swaps

Pagination, search, and `Per page` changes swap `#people-list-region` via HTMX. The existing role-merge pattern clears selections on `htmx:afterSwap`. **Adopt the same behavior for v1**: switching pages or refining the search cancels the in-progress selection.

Rationale: the dominant workflow is "search narrows candidates to the same page, then select two adjacent rows." Cross-page selection persistence (via `sessionStorage`) is a useful follow-up but adds JS state we don't need on day one.

If the user reports this is needed: store `{tableId: Set<personId>}` in `sessionStorage`, re-hydrate on `htmx:afterSwap` by re-checking matching checkboxes. Out of scope for #137.

### Archived people

Unlike role-merge (button hidden when org archived), the People list shows active + archived together based on the `status` filter. The merge button stays visible regardless. The route already accepts archived people on either side; no guard change needed. (If the team later decides archived-into-active should 409, that's a separate decision.)

### Client-side JS

New file `src/static/admin/people-merge.js`, structurally parallel to [role-merge.js](../../src/static/admin/role-merge.js). Differences from role-merge:

| Aspect | role-merge.js | people-merge.js |
|---|---|---|
| Root element | `#roles-table` | `#people-table` |
| Toggle button | `#roles-merge-btn` | `#people-merge-btn` |
| Action bar | `#roles-merge-bar` | `#people-merge-bar` |
| URL template | `/admin/orgs/{org_id}/roles/{a}/merge/{b}/` | `/admin/people/{a}/merge/{b}/` |
| Row data-attrs | `data-role-id`, `data-title` | `data-person-id`, `data-title` |
| Min-rows-to-merge | `>= 2` | `>= 2` (same) |
| Pagination interplay | n/a | Hide `.pagination--sticky` in merge mode |
| Inline filter | client-side `#roles-filter` | n/a (server-side search via existing filter card) |

Loaded via the existing `extra_head` or `extra_scripts` slot on `list.html`.

**Recommend parallel JS file, not parameterization.** The org-vs-person scope difference is small but real, and the data contract (`data-org-id`, `data-role-id` vs nothing/`data-person-id`) doesn't generalize without churn. Two ~200-line files are cheaper than one shared file with two callers and conditional branches.

### Template changes

**`src/templates/admin/people/list.html`**:
- Add `#people-merge-btn-wrap` containing `#people-merge-btn` next to `+ Add person`.
- Include `people-merge.js` (via `extra_head` or `extra_scripts` block, mirroring role-merge).

**`src/templates/admin/people/_region.html`**:
- Add `data-people-table` data attrs as needed on `#people-table` (none required beyond ID — no `data-org-id` analogue).
- Add `.merge-col` `<th>` to the header row (visually hidden until merge mode via CSS — same pattern as roles).
- Append `#people-merge-bar` inside `.table-wrapper` (or just outside, parallel to where pagination sits). Place adjacent to `.pagination--sticky` so the JS swap is local.

**`src/templates/admin/people/_rows.html`**:
- Prepend `<td class="merge-col"><input type="checkbox" name="merge-select" value="{{ person.id }}"></td>`.
- Add `data-title` and `data-person-id` to the `<tr>`.
- Update the empty-state row `colspan` from `4` → `5`.

### CSS

`.merge-bar`, `.merge-col`, and the merge-mode column visibility CSS already exist from role-merge work. Reuse as-is. Add a small modifier for the people-list visual distinction (accent background) if not already covered by the shared `.merge-bar` class.

## Testing Strategy

**JS unit tests** — new `tests/js/people-merge.test.js`, mirror `tests/js/role-merge.test.js`:
- Merge mode enter/exit; button text + class swap.
- Checkbox cap enforcement.
- Action bar visibility gated on `data-merge-mode`.
- Progressive disclosure states (0/1/2 selected).
- URL construction: `/admin/people/{a}/merge/{b}/`.
- Disabled state when `< 2` people in tbody.
- Pagination bar hidden in merge mode; restored on exit.
- Selection cleared on `htmx:afterSwap`.

**Integration tests** — extend `tests/api/admin/test_people_duplicates.py` (or new `test_people_list_merge.py`):
- POST `.../merge/...` with `HX-Target: people-table-body` returns `_rows.html`-shaped body (assert tbody markup, not duplicates region).
- POST with HTMX from duplicates page (existing behavior) still returns `_duplicates_region.html`. Regression guard.
- Flash header present on both branches.
- Non-HTMX still redirects.

**Structural test** — `tests/api/admin/test_people_merge_js.py` parallel to [test_role_merge_js.py](../../tests/api/admin/test_role_merge_js.py): assert anchors (`people-table`, `people-merge-btn`, `people-merge-bar`, URL pattern) so renames don't silently break the script.

## Out of Scope

- Cross-page selection persistence (deferred — add only if reported).
- Automated duplicate detection improvements (already handled by `people_dups.py`).
- Bulk merge of >2 people.
- Merge preview modal (the role-merge pattern uses confirm dialog; people merge accepts the same risk profile by design).
- Archived-vs-active merge guards (separate policy decision if needed).

## Key Decisions

| Decision | Rationale |
|---|---|
| Reuse existing `person_merge` route | All reassignment logic already proven via the duplicates flow; only the HTMX response needs branching |
| Branch HTMX response on `HX-Target` | Avoids new query params or duplicated routes; one route, two presentations |
| Parse `HX-Current-URL` for filters (option a) | Preserves user's `q`/`status`/`page`/`page_size` across merge without coupling button templates to filter state |
| Swap (not stack) the bottom sticky bar | Eliminates overlap/z-order with `.pagination--sticky`; top pagination remains for navigation; mode change is visually obvious |
| Parallel JS file (people-merge.js) | Cheaper than parameterizing role-merge.js; data contracts differ enough that abstraction would obscure both |
| Selection cleared on `afterSwap` (v1) | Matches role-merge; dominant workflow is same-page; cross-page persistence is a future enhancement |
| Merge button always visible | People list mixes active + archived; route already accepts both sides |
