# power-map — Form Controls

The three composite input controls the admin dashboard builds by hand: the
typeahead/combobox, the address confirm flow, and the paired date control.

---

## Typeahead / Combobox


Used when selecting a related entity by searching (e.g. parent organization, child organization). Combines an HTMX search input with a JS-managed dropdown and a hidden ID field.

### HTML structure

```html
<div class="form-group" style="margin-bottom:0;position:relative">
  <input id="parent-search" type="text" autocomplete="off"
         placeholder="Type to search…"
         value="{{ parent.display_name if parent else '' }}"
         hx-get="/admin/orgs/search/"
         hx-trigger="input changed delay:200ms"
         hx-target="#parent-search-results"
         hx-params="q"
         name="q"
         role="combobox"
         aria-expanded="false"
         aria-haspopup="listbox"
         aria-controls="parent-search-results"
         aria-autocomplete="list">
  <input type="hidden" name="parent_id" id="parent-id-hidden"
         value="{{ parent.id if parent else '' }}">
</div>
<ul id="parent-search-results" class="typeahead-results" role="listbox"></ul>
```

- **Hidden input** (`name="parent_id"`) holds the selected entity's ID. The visible text input is display-only; the form submits the hidden value.
- **`hx-params="q"`** — sends only the query string, not the other form fields.
- **`hx-trigger="input changed delay:200ms"`** — debounces; `changed` prevents re-firing on arrow-key navigation.
- **`hx-swap="innerHTML"`** must be explicit when the input is inside a `<form>` with a different `hx-swap` — see `docs/HTMX.md` § HTMX Patterns HTMX attribute inheritance. If the typeahead input is not inside a `<form>` element (e.g. it sits directly in a `<tr>` with standalone `hx-post` buttons), HTMX defaults to `innerHTML` and no override is needed.

### Search results template (`_search_results.html`)

```html
{% for r in results %}
<li id="opt-{{ r.id }}" role="option"
    data-id="{{ r.id }}"
    data-label="{{ r.display_name }}">{{ r.display_name }}</li>
{% endfor %}
```

- `role="option"` on every `<li>`.
- `data-id` — entity ID; JS copies this to the hidden input on selection.
- `data-label` — display text; JS copies this to the visible input.
- `id` must be unique across the page. When multiple typeaheads coexist, prefix the ID in the `htmx:afterSwap` listener: `li.id = listboxId + '-' + li.id`.

### Scoped search endpoints

When the candidate list must exclude already-linked items (e.g. children), use a scoped endpoint rather than the generic `/search/`:

```
GET /{org_id}/children/search/?q=...
```

The scoped endpoint filters out the current entity and any already-linked records.

### JavaScript — shared factory

All typeahead comboboxes use the shared factory loaded from `base.html`:

```javascript
window.initTypeaheadCombobox({
  inputId:   'my-search',
  listboxId: 'my-results',
  hiddenId:  'my-id-hidden',
});
```

The factory is defined in `src/static/admin/typeahead-combobox.js` and loaded with `defer` in `<head>`. If a form partial also needs extra logic (e.g. disabling a date field when a checkbox is checked), put it in a separate IIFE after the factory call — do not mix it into the combobox wiring.

#### Mounting contract (#435)

A call site never has to know whether the deferred factory has loaded yet — but that is because of a queue, **not** because `defer` happens to be early enough:

| Path | Ordering |
|---|---|
| **Hard page load** | The inline `<script>` in `<body>` executes during parse — *before* any deferred `<head>` script. `window.initTypeaheadCombobox` at that moment is the **queue stub** (inline, non-deferred, in `base.html`), which records the config and returns a deferred handle. `typeahead-combobox.js` then loads, replaces the stub with the real factory, and drains the queue from the bottom of the file — after parse, so every element the queued configs name exists. |
| **Boosted nav / HTMX swap** | htmx executes the swapped-in inline script long after the factory is real, so the call goes straight through and never queues. |

Both paths therefore wire exactly once: the queue is emptied on drain and the stub is gone, so nothing can be mounted twice.

Rules that follow from this:

- The queue stub in `base.html` **must stay inline and non-deferred**, above the `typeahead-combobox.js` tag. Moving it into a deferred (or externally fetched) script reintroduces the race — a hard load then silently leaves the combobox unwired: results still swap in, but the dropdown never opens and option ids stay unprefixed. That regression is pinned at three tiers: `test_typeahead_wires_on_hard_load` in `tests/api/admin/test_browser_smoke.py` (a hard `page.goto`, not a boosted click), `tests/js/typeahead-mount-queue.test.js`, which evals the stub extracted from `base.html`, and `test_typeahead_mount_queue_stub_precedes_the_deferred_factory` in `tests/api/admin/test_base_template.py`, which parses the rendered page and fails if the stub gains `defer`/`src` or falls below the factory.
- Do **not** add a `typeof window.initTypeaheadCombobox === 'function'` guard around a mount. It was never protective — on the path where the factory is missing the guard is exactly what swallowed the mount. The stub guarantees the global is callable. (The surviving guards in existing templates are harmless leftovers.)
- Anything else that must be callable from an inline `<body>` script on a hard load needs the same treatment; a deferred `<head>` script alone is not available there.

### JavaScript contract

The factory implements:

| Behaviour | Detail |
|---|---|
| **Open dropdown** | `htmx:afterSwap` on the `<ul>` — position via `getBoundingClientRect`, set `aria-expanded="true"` |
| **Close dropdown** | Outside click, scroll (capture phase), Escape key, or item selection — set `aria-expanded="false"`, clear `ul` |
| **Arrow navigation** | `ArrowDown` / `ArrowUp` — cycle `.is-active` class on `<li>` items, scroll into view, set `aria-activedescendant` |
| **Enter to select** | Copy `data-id` → hidden input, `data-label` → visible input, close dropdown |
| **Escape** | Close dropdown without selection |
| **Mouse select** | `ul.addEventListener('mousedown')` + `e.preventDefault()` — delegate to `closest('[data-id]')`. `mousedown` is used instead of `click` because mousedown fires first; without `preventDefault`, the input loses focus before `click` fires and the event is swallowed or re-routed in some browsers. |
| **Scoped IDs** | In `afterSwap`, prefix each `li.id` with the listbox's own `id` to prevent duplicate IDs when two typeaheads are mounted |
| **Stale-id guard (#358)** | The hidden id is valid only while the visible text equals the label of the last selection (seeded from the server-rendered value). Any input that diverges — emptying the box, or editing it — clears the hidden id, so blanking the search box can never silently re-submit the previous selection. |

### Clearing a selection (#358)

An optional visible "×" button lets a user unset an **optional** picker (e.g. a role's jurisdiction, an event's linked entity) without editing the text. Required pickers need no button — blanking + submit surfaces the existing "select an X" validation error.

Wrap the visible input in `.typeahead-input-wrap` (so the button centres over the input, not a label above it) and pass `clearButtonId`:

```html
<div class="typeahead-input-wrap">
  <input id="jurisdiction-search" ...>
  <button type="button" class="typeahead-clear" id="jurisdiction-clear"
          data-typeahead-clear aria-label="Clear jurisdiction"
          {% if not sel_jurisdiction_id %}style="display:none"{% endif %}>&times;</button>
</div>
<input type="hidden" name="jurisdiction_id" id="jurisdiction-id-hidden" value="...">
```

Seed `style="display:none"` on the button when the field renders empty (mirror the hidden id's condition): the factory reconciles visibility on mount, but the inline seed avoids a pre-hydration flash of a `×` with nothing to clear.

```javascript
window.initTypeaheadCombobox({
  inputId: 'jurisdiction-search',
  listboxId: 'jurisdiction-search-results',
  hiddenId: 'jurisdiction-id-hidden',
  clearButtonId: 'jurisdiction-clear',   // optional — omit for required pickers
  onClear: () => { /* optional — react to a cleared selection */ },
});
```

- The `×` button clears text + hidden id, closes the dropdown, and refocuses the input. The factory shows it only while a selection exists (hidden id non-empty), so an empty picker carries no clear affordance.
- `onClear()` fires (from either clear path) only when a non-empty selection was actually dropped — use it to reset dependent UI (e.g. a relationship phrase preview). Do **not** reuse `onSelect('')` for this; `onSelect` consumers that navigate on select (e.g. the merge target picker) must not fire on a clear.

---

## Address Confirm Flow


When an address form is submitted, the server normalizes the input and — if normalization produces a meaningful result — shows a confirm modal before persisting. This uses `HX-Retarget` (`docs/HTMX.md` § HTMX Patterns) to inject the modal without touching the form row.

### Two-mode POST

The address create and edit routes accept a `mode` form field:

| `mode` value | Behaviour |
|---|---|
| `confirm` (default) | Normalize input; if result differs, return the confirm modal via `HX-Retarget` |
| `save` | Skip normalization; persist immediately and return the updated row |

The form row's `hx-post` always omits `mode` (defaults to `confirm`). The confirm modal's action buttons submit `mode=save`.

### Portal div

Place an empty `<div id="address-confirm-portal"></div>` in the detail template **after** the last section and **before** the metadata footer. The server uses `HX-Retarget: #address-confirm-portal` to inject the modal here without touching the form row.

```html
<!-- detail.html — after last <section>, before metadata <p> -->
<div id="address-confirm-portal"></div>
```

### Server response when confirm needed

```python
return templates.TemplateResponse(
    request,
    "admin/{entity}/partials/_address_confirm_modal.html",
    {
        "entity_id": entity_id,
        "addr_id": addr_id,          # None for create, str for edit
        "original": original_ctx,    # dict of submitted field values
        "normalized": normalized_ctx, # dict from normalizer result
        "validation_status": ...,
        "validation_provider": ...,
    },
    headers={"HX-Retarget": "#address-confirm-portal", "HX-Reswap": "innerHTML"},
)
```

### Confirm modal structure

The modal contains two action forms that both POST to the same create/edit endpoint with `mode=save`:
- **Keep my input** — hidden inputs carry the original field values
- **Accept** — hidden inputs carry the normalized field values

Both forms include `hx-on::after-request="if (event.detail.successful) window.__pmAddrConfirmClose()"` to close the modal on success.

The modal JS (inline in the partial) handles: Escape to close, Tab/Shift-Tab focus trap, `window.__pmAddrConfirmClose()` for programmatic close, focus restoration to the triggering element.

### When normalization is skipped

If the normalizer returns no result (service unavailable, address unparseable), `_maybe_confirm()` returns `None` and the route falls through to the save path directly — no modal, no `HX-Retarget`.

---

## Paired Date Control Pattern


For any section that displays two related date fields side-by-side on a detail page (e.g. boundary dates, service dates), follow this pattern. Both read and edit partials share the same outer `id` as their HTMX swap target.

### Section label naming

Use a descriptive two-word label that clarifies the *semantic role* of the date pair:

| Context | Section label |
|---|---|
| Role boundary dates (established / abolished) | **Boundary Dates** |
| Role assignment service dates (start / end) | **Service Dates** |

Avoid the bare label "Dates" — it gives no context.

### Read partial (`_dates_read.html`)

```html
<div id="dates-field" style="margin-top:var(--space-5)">
  <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:var(--space-3)">
    <h3 class="field-group-label">{Section Label}</h3>
    {% if not entity.archived_at %}
    <button type="button" class="btn btn--sm btn--secondary"
            hx-get="/admin/{entities}/{{ entity.id }}/inline/dates/edit/"
            hx-target="#dates-field"
            hx-swap="outerHTML">Edit</button>
    {% endif %}
  </div>
  <div style="display:flex;gap:var(--space-6);font-size:var(--font-size-sm)">
    <div>
      <div class="field-group-label" style="font-size:var(--font-size-xs)">{Field A label}</div>
      <div style="color:{% if entity.{field_a} %}var(--color-text){% else %}var(--color-text-muted){% endif %}">
        {{ entity.{field_a} or '—' }}
      </div>
    </div>
    <div>
      <div class="field-group-label" style="font-size:var(--font-size-xs)">{Field B label}</div>
      <div style="color:{% if entity.{field_b} %}var(--color-text){% else %}var(--color-text-muted){% endif %}">
        {{ entity.{field_b} or '—' }}
      </div>
    </div>
  </div>
</div>
```

- Each field renders its sub-label (e.g. "Established", "Start") as a `<div class="field-group-label">` — all-caps via CSS, xs size via inline override.
- Null values display as `—` in muted text color. Non-null values use `var(--color-text)`.
- Do **not** lump both values into a single bordered box (`start — end`) — that conflates two distinct fields and makes null states ambiguous.

### Edit partial (`_dates_form.html`)

```html
<div id="dates-field" style="margin-top:var(--space-5)">
  <form hx-post="/admin/{entities}/{{ entity.id }}/inline/dates/"
        hx-target="#dates-field"
        hx-swap="outerHTML">
    <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:var(--space-3)">
      <h3 class="field-group-label">{Section Label}</h3>
      <div>
        <button type="submit" class="btn btn--primary btn--sm">Save</button>
        <button type="button" class="btn btn--secondary btn--sm"
                hx-get="/admin/{entities}/{{ entity.id }}/inline/dates/"
                hx-target="#dates-field"
                hx-swap="outerHTML">Cancel</button>
      </div>
    </div>
    {% if error %}
    <div class="alert alert--error" role="alert" style="margin-bottom:var(--space-3)">{{ error }}</div>
    {% endif %}
    <div style="display:flex;gap:var(--space-4)">
      <div class="form-group" style="margin-bottom:0;flex:1">
        <label for="{field-a}-input" class="field-group-label" style="font-size:var(--font-size-xs)">{Field A label}</label>
        <input type="date" id="{field-a}-input" name="{field_a}" value="{{ {field_a}_input or '' }}">
      </div>
      <div class="form-group" style="margin-bottom:0;flex:1">
        <label for="{field-b}-input" class="field-group-label" style="font-size:var(--font-size-xs)">{Field B label}</label>
        <input type="date" id="{field-b}-input" name="{field_b}" value="{{ {field_b}_input or '' }}">
      </div>
    </div>
  </form>
</div>
```

### Rules

- **Section label:** `<h3 class="field-group-label">` in both read and edit states — no layout shift on toggle.
- **Field sub-labels in edit:** `<label class="field-group-label" style="font-size:var(--font-size-xs)">` — all-caps from the class, xs size from the inline override. Associates correctly with the input for screen readers.
- **`flex:1` on both form-groups** — inputs share available width equally; no hard widths.
- **No `flex-wrap`** — date inputs are compact enough to sit side-by-side at all supported viewport widths.
- **Error alert** between the header row and the inputs — same position used by the contact form inline error pattern.
- **Non-HTMX fallback:** POST returns `RedirectResponse` to the detail page.
- **Archived guard:** Edit button hidden when `entity.archived_at` is non-null.

### Routes

```
GET  /admin/{entities}/{id}/inline/dates/       → read partial
GET  /admin/{entities}/{id}/inline/dates/edit/  → edit partial
POST /admin/{entities}/{id}/inline/dates/       → save → read partial (or re-render form on error)
```

### Inline row variant — date-range labeling

When the date pair sits inside a **repeatable inline form row** (a flex action row in a
table, not a standalone detail-page section), the stacked labels above don't fit. Use a
visible prefix label + presentational separator instead. Reference:
`admin/orgs/partials/_address_form_row.html` (validity window).

```html
<label for="valid-from-{% if a and a.id %}{{ a.id }}{% else %}new{% endif %}"
       style="font-size:var(--font-size-sm);color:var(--color-text-muted);white-space:nowrap">Valid from</label>
<div class="form-group" style="margin-bottom:0">
  <input type="date" name="valid_from"
         id="valid-from-{% if a and a.id %}{{ a.id }}{% else %}new{% endif %}"
         value="…">
</div>
<span aria-hidden="true"
      style="font-size:var(--font-size-sm);color:var(--color-text-muted)">to</span>
<div class="form-group" style="margin-bottom:0">
  <input type="date" name="valid_until" aria-label="Valid until" value="…">
</div>
```

**Rules:**

- **First input:** visible `<label for>` is its accessible name — do **not** also set
  `aria-label` (conflicting double name). Label sits *outside* the `.form-group` (the
  `.form-group label` CSS rule forces `display:block`, which breaks the inline flex).
- **Label/input ids are row-scoped** (`{field}-{{ a.id }}` / `{field}-new`) — same suffix
  convention as `address-structured-fields-*`; multiple rows in edit mode must not
  produce duplicate ids.
- **Separator** (`to`, `—`) is `aria-hidden="true"` — purely presentational.
- **Second input:** keeps `aria-label` for its accessible name; its only visible
  "label" is the separator, which is not an accessible name. Screen readers announce
  the pair as "Valid from" / "Valid until", never a dangling "to".

Applied to the header-less flex form rows: `_address_form_row.html` (validity),
`people/_assignment_form_row.html` + `roles/_assignment_form_row.html` (start/end),
and `orgs/_name_form_row.html` (effective dates) — #259.

**Not** for date pairs in **table cells** under `<th>` column headers (the
assignment *edit* rows, `_assignment_edit_row.html`): the column header already
supplies visible context, so the input keeps a plain `aria-label` and no in-cell
label is added (it would duplicate the header). This variant is for flex rows where
the dates float without a header.
