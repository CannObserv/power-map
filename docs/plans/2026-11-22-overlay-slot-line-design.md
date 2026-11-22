# Overlay slot line: value, empty slots, placement, wording (#838)

**Status:** approved 2026-11-22 as recommended, `slot_changed` guard included. Part of epic #818.

## Goal

The slot line (`admin/shared/_overlay_slot.html`) offers **Pin current ‹label›**
without ever showing the value it would pin. Over an empty slot, that button
pins NULL: "keep this field empty over usa-wa", which is not what it says. Decide,
for all ten slots, what the line shows, what an empty slot offers, where the line
sits, and how Pin and Unpin read.

Pin and apply semantics (`curation_overlay`, the applier, the `tracked()` edit
hook) do not change. Only what the admin offers and says does.

## The ten slots, by what "empty" means

| Slot | Can be empty? | Value as a curator reads it |
|---|---|---|
| `person.name`, `organization.legal_name` | yes: no legal name row | the name |
| `organization.acronym` | yes: common | the acronym |
| `organization.parent_id` | yes: a top-level org | the parent's display name (`display_sql`) |
| `organization.dissolved_year` | yes: any undissolved org | the year |
| `assignment.start_date`, `.end_date` | yes: an unknown start, an open span | `YYYY-MM-DD` |
| `assignment.is_current` | no: `NOT NULL` | **Current** / **Former**, not `True` / `False` |
| `assignment.membership_type` | no: `NOT NULL DEFAULT 'principal'` | **Principal** / **Substitute** / **Ex officio** / **Acting**, not `ex_officio` |
| `role.title` | no: `NOT NULL` | the title |

So seven slots can show Pin over nothing. Three never do, but two of those print
raw stored text once pinned (`True`, `ex_officio`). And `membership_type` has no
editor, so its line's "Editing it pins your value" is false.

## Recommendation

### 1. Show the governed value, in both branches

One formatter per slot turns a live value *or* a pinned value into what a curator
reads, as in the table above. `None` reads *none*. `SlotState` gains the live
value, read with the slot's own SQL (one more query per line).

- **Unpinned, filled:** Dissolved year: **2019**. Maintained by usa-wa. Editing it pins your value. **[Pin dissolved year]**
- **Unpinned, empty:** Dissolved year: *none*. Maintained by usa-wa. Editing it pins your value. *(no button)*
- **Pinned:** **[Pinned]** Dissolved year: **2019**. PM keeps it over usa-wa's value. Pinned 2026-09-30 by ‹email› (‹note›). **[Unpin dissolved year]**
- **Pinned NULL:** **[Pinned]** Dissolved year: *none, kept empty*. PM keeps it empty over usa-wa's value. … **[Unpin dissolved year]**
- **Pinned, live value differs** (a write outside `tracked()`, e.g. the public API, moved it): the pinned line adds *PM's record reads **2020** until the next apply restores the pin.*
- **No editor** (`membership_type`): drop "Editing it pins your value."

### 2. An empty slot offers nothing

Options weighed: (a) nothing; (b) an explicit **Keep empty** with a confirmation
naming what it suppresses; (c) a quieter disclosure that opens (b).

**Recommend (a).** Reasons:

- *Keep empty* was used once, as a misclick undone 3 s later (2026-09-30). It has
  never been used on purpose.
- The case it serves, blocking a value usa-wa *might* send later, has a path
  already, after the fact: once the value arrives, the curator edits it away
  (archive the dissolved event, clear the parent). `tracked()` pins NULL, and
  the line shows *kept empty*. That path is unchanged.
- An empty line still says the field is usa-wa's and that an edit pins. So
  the line stays (muted *none*), but without a button.

**Server half.** The Pin route refuses an empty slot, so a stale page cannot pin
NULL either. Pin also posts the value the line showed (`hx-vals`), and the route
refuses when the live value differs. That extends the existing "act only on
what the line showed" rule (`already_pinned`, `pin_stale`) to the value itself.
One new warning flash key, `slot_changed`: *"That field changed since the page
loaded; nothing was pinned."* It goes in `SHARED_FLASH_MESSAGES` and in the
registry pin in `test_flash_levels.py`, with a 303 fallback as for every
mutation. `curation_overlay.pin(value=None)` stays valid; `tracked()` still
uses it.

If (b) is preferred later, it is additive: a `hx-confirm` **Keep ‹label› empty**
button (`data-confirm-variant="primary"`) whose text says *"PM will ignore any
‹label› usa-wa sends until you unpin."*

### 3. Placement: keep all ten where they are

Each line sits under the heading of the panel that edits its slot: names and
acronym above their tables, the parent under *Parent Organization*, the
assignment slots beside Status, Membership and Service Dates, the role title
under its read partial. That pairing is the point: the line explains the edits
made right below it.

The dissolved year was the one candidate to move.

- *Beside the dissolved event row:* rejected. When the slot is empty there is no
  row. With several `dissolved` events, the slot reads the earliest unarchived
  one, so the line would jump between rows. And rows re-render on every inline
  edit, so they would have to carry overlay state, which undoes the
  self-loading fragment (#547).
- *With status (the active toggle in Details):* rejected. Its edits happen in
  the Events panel.

Once the value shows (*Dissolved year: none*), the line reads correctly where it is.

### 4. Wording and confirmation

- **Pin ‹label›** / **Unpin ‹label›**: symmetric. "current" goes, because the
  value is now on the line. The label keeps each button's accessible name
  unique: the assignment page has four lines.
- **No confirmation on either.** Both act on a value the line shows, both are
  undone by the other, and both are announced in the flash. A confirmation would
  be warranted only for Keep empty, which is not offered.
- Flash text is unchanged (`pinned`, `unpinned`, the HTMX
  *"‹Label› pinned: PM keeps it over usa-wa's value."*).

## Tests (TDD, after approval)

- The line renders empty and filled for `dissolved_year` and `parent_id` (the
  issue's floor), plus a date slot. The empty case has no Pin control; the filled
  case shows the formatted value and **Pin ‹label›**.
- Formatting: `is_current`, pinned `"False"` and live `False`, both read **Former**;
  `membership_type` `ex_officio` reads **Ex officio**; a pinned NULL reads
  *none, kept empty*; a pinned value that differs from live gets the divergence
  sentence.
- Pin route: an empty slot → `slot_changed` (HTMX warning plus 303 fallback),
  no pin row; a mismatched `expected` → the same; a matching one pins.
- Unchanged and still green: `test_mutation_fallback_sweep.py`, the self-loading
  host markup (#547), `test_flash_levels.py` (new key in both places), both
  a11y tiers.

## Docs

`docs/ADMIN_OVERLAY.md`: "The nine slots" → ten. § Surfaces states the line's
four states, that an empty slot offers no Pin, and `slot_changed`. The
`membership_type` row: "only Pin pins it". `docs/ADMIN.md` is at its budget and
is not touched.

## Out of scope

- Pin and apply semantics; which fields are slots.
- A *Keep empty* control (option b): additive later if wanted.
- No version bump and no public spec change: admin-only.
