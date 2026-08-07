# power-map — Accessibility (WCAG 2.1 AA)

Accessibility conventions for the admin dashboard: markup rules the templates must
follow, and the three test tiers that enforce them. Commands for the browser tier
live in `docs/TESTING.md` § Browser Testing.

---

## Emoji

All decorative emojis must be wrapped — never bare:

```html
<span aria-hidden="true">🌱🏛️🔍</span>
```

## Focus rings

Use `:focus-visible` (not `:focus`):

```css
outline: 2px solid var(--color-border-focus);
outline-offset: 2px;
```

## Icon-only buttons

Always include `aria-label`:

```html
<button aria-label="Toggle navigation">&#9776;</button>
```

## Row-action buttons

Every `btn--sm` in a read-row partial must have `aria-label`. Multiple identical labels
("Edit", "Delete") across rows on the same page fail WCAG 2.1 AA SC 2.4.6 / 4.1.2.

Pattern: `aria-label="[Action] [entity-specific descriptor]"`

- **[Action]**: imperative matching visible text (`Edit`, `Delete`, `Archive`, `Unarchive`, `View`, `Open`, `Unlink`, `Copy`, `Revoke`, `Grant`, `Close`)
  Exception: where the visible text *is* the descriptor (the API-key scope panel's Grant buttons show the scope id, not the verb), prefix the action in the `aria-label` (`aria-label="Grant {{ st.id }}"`). WCAG 2.5.3 (Label in Name) still holds — the visible scope id is contained in the accessible name.
- **[entity-specific descriptor]**: the row's most natural identifier — name, value, address type, etc.
  Address rows use `a.address_type` (e.g. `"Edit mailing address"`) since the full formatted address is unwieldy. If an entity has two addresses of the same type, labels will collide — acceptable given the rarity of this case.

```html
<button aria-label="Edit name {{ n.name }}">Edit</button>
<button aria-label="Delete contact {{ c.value }}">Delete</button>
<button aria-label="Archive assignment at {{ ra.org_name or '(unnamed)' }}">Archive</button>
<a aria-label="Edit {{ org.canonical_name or '(unnamed)' }}">Edit</a>
```

**Excluded**: Save/Cancel in form rows (`*_form_row.html`, `*_edit_row.html`) — only one row is
editable at a time, so disambiguation is not needed. Static linting enforced by
`tests/api/admin/test_aria_labels.py`.

**Looped buttons outside `*_rows?.html`**: the lint auto-discovers `*_row.html` / `*_rows.html`
partials only. A partial that renders repeated action buttons in a loop under a different name
(e.g. `settings/partials/_api_key_scopes.html` — per-scope Revoke/Grant) hits the same SC 2.4.6
problem but is missed by the glob. Add such files to `_EXTRA_LOOPED_BUTTON_TEMPLATES` in the lint
rather than widening the glob — most non-row partials carry single buttons (Close/Save) that
legitimately need no `aria-label`. Note the lint checks `aria-label` **presence**, not accessible
name by any mechanism, so a button labeled via visible text alone still needs an `aria-label` to
pass; when adding one, fold any `.visually-hidden` descriptor into the `aria-label` (it overrides
the text-node name) so nothing is dropped. (#247)

## Status badges

Entity state (active / inactive / archived / current / former, validation status, import
action, etc.) is **always conveyed by badge text** — never by color or icon alone (WCAG
1.4.1 Use of Color). Row-level styling (`tr.is-archived` strike-through, `tr.is-inactive`
muted first cell) is **redundant** with the in-row text badge and must never be the sole
signal; the `.badge` background color is decorative reinforcement only.

When adding a status indicator, render a text label inside the `.badge`. If a state must
appear without visible text (a space-constrained icon or colored dot), expose the state
name in a `.visually-hidden` span or `aria-label`. Audit 2026-06 (#245): no color-only or
icon-only status indicators exist in the admin UI — keep it that way.

## Presence indicators (notes) (#318)

Distinct from status badges: a **presence indicator** is an icon-only marker that some
optional payload exists, not an entity state. Assignment read rows
(`people/partials/_assignment_row.html`, `roles/partials/_assignment_row.html`) show a
`role="img" aria-label="Has notes"` glyph when `ra.notes` is set. Rules:

- The indicator carries an accessible name via `aria-label` (never a bare glyph, never
  `title`) — this does **not** breach the "no icon-only *status*" rule above, because entity
  status is still conveyed by its text `.badge` alongside.
- **Never render the note text inline.** Provenance can be long or sensitive; the row shows
  only that a note exists. Read/author the text on the standalone assignment page
  (`/admin/role-assignments/{id}/` → `_notes_read.html` / `_notes_form.html`), reached via
  the row's **Open** link. The inline create/edit forms deliberately carry no notes control
  (row real estate).

## Role & assignment attachment panels (#326)

Role and role-assignment detail pages carry the same shared-factory attachment panels as
orgs/people/jurisdictions:

- **Contacts** (email + phone) and **Links** on both — `roles_{contacts,links}.py` and
  `role_assignments_{contacts,links}.py`, thin wirings of `make_contacts_router` /
  `make_links_router` (`entity_type` `'role'` / `'role_assignment'`).
- **Identifiers** on assignments only (`role_assignments_identifiers.py`) — the role
  *definition* carries no identifiers (`entity_identifier_types` excludes `'role'`). The
  picker excludes internal types, so `role_wa_pdc` is the offered public type.
- Partials mirror the org set under `roles/partials/` and `role_assignments/partials/`
  (context id keys `role_id` / `ra_id`); detail handlers fetch the arrays into context. The
  `+ Add` buttons are gated on an active (non-archived) role/assignment; existing rows stay
  read-visible.
- **Role-level ancillary cleanup (CR).** A role's own `contact_methods`/`links`
  (`entity_type='role'`, no FK — the assignment-only `ancillary_migrate` #324 machinery does
  not cover these) must be cleaned like the assignment case, else the #326 editors orphan
  them. `src.core.ancillary_migrate` grew `rehome_role_ancillary` (merge: re-point + dedup
  onto the surviving role, emit a `'role'` 'updated' signal) and `delete_role_ancillary`
  (hard-delete: drop the rows). Wired into all three role-deleting paths: `roles.py`
  hard-delete, `orgs_roles.py::role_merge`, and both `orgs_merge.py` role-pair deletes.
- **Addresses deliberately excluded** — the address editor is hand-built per entity (not a
  shared factory) and semantically thin on a role/assignment; the public observation API
  still accepts them.
- **Known gap (#327):** these shared admin routers do raw INSERT/DELETE and do **not** emit
  a parent `entity_changes` 'updated' signal (the public observation path does). Combined
  with the no-touch-cascade tables (#324), admin edits are invisible to subscribers until
  #327 lands the consistent emit.

## HTMX live regions

All swap targets: `aria-live="polite" aria-atomic="false"`.

During requests, `aria-busy="true"` is automatically set on the swap target via global
`htmx:beforeRequest` / `htmx:afterSettle` listeners in `base.html`. No per-form work needed.

## Form labels

Every `<input>` (except `type="hidden"`), `<select>`, and `<textarea>` must have a
programmatic **accessible name**. A `placeholder` is **not** a label — it disappears on
input and many screen readers skip it (WCAG 2.1 AA SC 1.3.1 / 4.1.2). `<select>` can't
carry a placeholder at all, so it always needs an explicit name.

Three acceptable mechanisms:

1. **Visible `<label for>`** — preferred for full-page forms (`*/form.html`) where vertical
   space is free:
   ```html
   <label for="name">Canonical name</label>
   <input id="name" name="name" type="text">
   ```
2. **Wrapping `<label>`** — when the control sits inside its label:
   ```html
   <label>Visibility <select name="visibility">…</select></label>
   ```
3. **`aria-label`** — for the dense inline form-row / edit-row grids
   (`*_form_row.html`, `*_edit_row.html`, `_event_form_row.html`) where a visible `<label>`
   would break the layout. Mirror the placeholder's intent as a concise noun phrase; keep the
   `placeholder` for the visual hint:
   ```html
   <input type="text" name="event_place_text" aria-label="Place" placeholder="Place (optional)">
   <select name="event_type_id" aria-label="Event type">…</select>
   ```

Repeated controls across rows (e.g. a per-row merge checkbox) need a **disambiguating**
descriptor, same rule as row-action buttons (SC 2.4.6):

```html
<input type="checkbox" name="merge-select" aria-label="Select {{ role.title or '(untitled)' }} for merge">
```

Do **not** rely on `title` for the accessible name (see *`title` attributes* below). Enforced
at two tiers (#246): static template lint in `tests/api/admin/test_aria_labels.py` (fast,
pre-render, per-file heuristic) and the authoritative rendered-DOM sweep in
`tests/api/admin/test_a11y_render.py` (integration tier — fetches every admin GET route and
resolves label ancestry and id references against real output).

## Optional-field cue

Inline form rows signal "(optional)" only in the `placeholder`, which assistive tech reads
unreliably (same reason `placeholder` is not a label, above). Mark an optional inline field on
**both** channels:

- **Visible:** keep the `(optional)` suffix in the `placeholder`.
- **Assistive tech:** add `aria-describedby` pointing to a `.visually-hidden` hint element
  (defined in `admin.css`). Namespace the hint `id` with the row key so multiple open rows
  don't collide.

```html
<input type="text" name="event_place_text" aria-label="Place"
       aria-describedby="event-place-opt-{{ _le_key }}"
       placeholder="Place (optional)">
<span class="visually-hidden" id="event-place-opt-{{ _le_key }}">Optional</span>
```

When the parenthetical carries more than optionality (a format hint), put the full text in the
hint: `Optional — city, postal, or street precision`.

Fields with a visible `<label>` don't need this — the label already names the field accessibly;
append `(optional)` to the label text instead. Static linting:
`tests/api/admin/test_aria_labels.py::test_optional_placeholder_cue_has_describedby`.

## Form hints

Link hint text to its input via `aria-describedby`:

```html
<input id="acronym" name="acronym" aria-describedby="acronym-hint">
<div class="form-group__hint" id="acronym-hint">Short abbreviation.</div>
```

Hint `id` convention: `{field_name}-hint`.

## Modal focus management

All modals must trap focus and restore it on close. The delete modal in
`partials/delete_modal.html` is the canonical example:

- Capture `document.activeElement` (the trigger) before moving focus
- On open: focus first interactive element inside the modal
- Tab / Shift-Tab: cycle within the modal's focusable elements
- Escape: close and restore focus
- On close: null `window.__pmCloseModal` and `window.__pmHandleDeleteResult`, remove modal, restore focus to trigger
- On DELETE success: call `close()` via `window.__pmHandleDeleteResult(event)`
- On DELETE error: keep modal open, show inline `.alert--error` message; 409 → "archive first"; other → status code; status 0 → network error

## `title` attributes

Do **not** use the HTML `title` attribute — its tooltip is invisible to keyboard and
touch users and is announced inconsistently by screen readers, so it must never be the
sole carrier of information (table-cell expansions, badge state, button purpose).
Surface the text visibly, in a `.visually-hidden` span, or via `aria-label`:

```html
<!-- avoid: full name only reachable on mouse hover -->
<td title="{{ ident.type_full_name }}">{{ ident.type_name }}</td>
<!-- prefer: expansion exposed to assistive tech -->
<td>{{ ident.type_name }}<span class="visually-hidden"> — {{ ident.type_full_name }}</span></td>
```

Static linting enforced by `tests/api/admin/test_aria_labels.py::test_no_title_attribute`
(`data-*` attributes such as `data-title` are unaffected).

## Muted text

Minimum color: `var(--color-text-muted)`. Never use anything lighter.

## Skip link

`.skip-link` targets `#main-content`:

```html
<a class="skip-link" href="#main-content">Skip to main content</a>
```

Hidden off-screen by default, visible on `:focus`.

## Reduced motion

All animations and transitions collapse to `0.01ms` under `prefers-reduced-motion: reduce`.

## Screen-reader testing

The static lints in `tests/api/admin/test_aria_labels.py` catch missing accessible names
structurally, but cannot verify the *announced experience*. Manually screen-read admin
changes that touch **forms, tables/rows, modals, badges, or live regions** before merging.

**Recommended combos** (cover at least one; use both platforms for high-traffic flows):

| Platform | Screen reader | Browser |
|---|---|---|
| macOS | VoiceOver (⌘F5) | Safari (primary), Chrome |
| Windows | NVDA (free) | Firefox (primary), Chrome |
| Linux | Orca | Firefox |

**Checklist:**

- **Forms** — every field announces a name + role; `(optional)` is spoken; hints
  (`aria-describedby`) are read.
- **Row actions** — repeated "Edit"/"Delete" buttons announce their entity-specific label,
  not a bare verb.
- **Status badges** — the state word ("Archived", "Inactive") is spoken; nothing conveys
  state by color or icon alone.
- **Table cells** — no information is mouse-hover-only (no `title`-only expansions).
- **Modals** — focus moves in on open, is trapped, and returns to the trigger on close
  (Esc + button); the modal exposes an accessible name.
- **Live regions** — HTMX swaps (flash messages, inline saves) are announced via the
  `aria-live="polite"` target without stealing focus.
- **Skip link** — the first Tab from page load reveals "Skip to main content" and jumps to
  `#main-content`.

**When:** before merging any admin-template change that adds or restructures the element
types above. Pure copy or style tweaks that don't change structure or semantics don't
require a manual SR pass.
