# power-map — HTMX Interaction Patterns

How the admin dashboard swaps, redirects, flashes and re-renders: HTMX request and
response conventions, flash notifications, pagination, destructive-action flows,
inline edit, guarded deletes, live header sync, and the Activity screens.

---

## HTMX Patterns


### `is_htmx(request)` helper

Canonical implementation in `src/api/admin/deps.py`:

```python
def is_htmx(request: Request) -> bool:
    return bool(
        request.headers.get("HX-Request") and not request.headers.get("HX-Boosted")
    )
```

**Why the `HX-Boosted` guard:** `hx-boost="true"` on `admin-layout` means boosted navigation sends both `HX-Request` and `HX-Boosted` headers. Without the guard, boosted sidebar clicks receive bare fragments instead of full-page layouts.

### Flash from HTMX mutation routes

Use `flash_trigger(level, body)` from `src.api.admin.deps`. Pass it as the `headers` argument to `TemplateResponse`:

```python
from src.api.admin.deps import flash_trigger

return templates.TemplateResponse(
    request, "admin/orgs/_region.html", ctx,
    headers=flash_trigger("success", f"Merged <strong>{escape(name)}</strong>."),
)
```

HTMX dispatches a `showFlash` DOM event when it processes the `HX-Trigger` response header. `flash.js` catches that event and injects a flash `<div>` into `#flash-region` imperatively — no OOB element in the response body. This works for any swap target, including `<tr>`.

**Timing:** `HX-Trigger` fires immediately on response receipt, before the DOM swap. This is imperceptible for fixed-position flash overlays. If a future route needs to reference post-swap DOM state, use `HX-Trigger-After-Settle` as the header name instead.

**Query-param flash (`?flash=<key>`) — the redirect-landing variant.** A redirect that ends on a full page (the Danger-Zone delete → list `HX-Redirect` navigation, #376; a non-HTMX 303 fallback, #351) can't carry an `HX-Trigger`, so it passes a `?flash=<key>` query param that the target route resolves via `resolve_query_flash` and renders server-side into `{% block flash %}`. Two mechanisms then clear the now-consumed param from the address bar so a manual refresh won't re-show the message:
- **Boosted navigations** — `resolve_query_flash` returns an `HX-Replace-Url` header (on non-HTMX requests) that htmx honors while processing the boosted response.
- **Hard navigations** — `HX-Replace-Url` is inert when htmx isn't driving the request (`window.location` from `HX-Redirect`, a plain 303→GET). `flash.js` covers this: on every full page load it strips a `flash` param via `history.replaceState`, preserving other params (#379). The two are complementary — keep both.

### Mutation form pattern

```html
<form hx-post="/admin/orgs/{{ id }}/archive/"
      hx-target="#org-card"
      hx-swap="outerHTML">
  <button class="btn btn--danger btn--sm">Archive</button>
</form>
```

Always provide a non-HTMX `RedirectResponse` fallback in the route handler — for direct non-HTMX POST **clients** (the public API, tests, `curl`), and because the #349 sweep enforces it. It is **not** graceful degradation: a `<form hx-post>` with no `action=` submits nothing without JS, and the admin requires JS by policy (#287 — see `docs/ADMIN.md § JavaScript is required`). Don't add `method="post" action="…"` to make it "work without JS" — that is the half-measure #287 explicitly rejected.

### Loading states

CSS handles loading automatically — no per-form JS:

```css
.htmx-request, .htmx-request button, .htmx-request input, .htmx-request select {
  opacity: 0.6; cursor: wait; pointer-events: none;
}
```

### HTMX attribute inheritance

`hx-swap`, `hx-target`, and other HTMX attributes inherit from parent elements unless explicitly overridden. A typeahead `<input>` inside a `<form hx-swap="outerHTML">` will inherit `outerHTML` and replace its `hx-target` element entirely rather than updating its contents — destroying the `<ul>` the dropdown depends on.

**Rule:** always set `hx-swap="innerHTML"` explicitly on any typeahead search input whose parent `<form>` carries a different `hx-swap`.

```html
<input hx-get="..."
       hx-target="#search-results"
       hx-swap="innerHTML"   <!-- explicit override — do not omit -->
       ...>
```

### Do not mix table elements and non-table elements in one HTMX response

A `<tr>` and a `<div>` **cannot be siblings** in the same HTMX response body. When HTMX parses a response, it sets the HTML as `innerHTML` of a container `<div>`. A `<tr>` in non-table context is invalid HTML — the browser's foster-parenting algorithm moves or strips it. HTMX has special-case handling for table elements, but only when the **entire response** is a table fragment. Mixed content breaks that detection and silently discards the `<tr>`.

**Use `HX-Retarget` + `HX-Reswap` response headers instead of OOB** when you need a server response to update a different element than the form's `hx-target` — for example, to fill a modal portal without touching the triggering row:

```python
return templates.TemplateResponse(
    request, "admin/orgs/partials/_my_modal.html", ctx,
    headers={
        "HX-Retarget": "#modal-portal",
        "HX-Reswap": "innerHTML",
    },
)
```

HTMX overrides the client-side `hx-target` / `hx-swap` with the header values. The triggering element (e.g. the `<tr>` form row) stays untouched in the DOM. The modal portal receives the response body. The modal's own action forms then target the original row directly.

**Portal pattern — first-class use case:** `HX-Retarget` is also the right tool when a mutation *may or may not* produce a secondary UI element (confirm dialog, validation step). The form row's `hx-target` always points at itself; the server conditionally redirects the response to a dedicated portal `<div>` elsewhere in the page. The row is never touched. See `docs/FORMS.md` § Address Confirm Flow (Address confirm flow) for the full example.

**When OOB is safe:** use it only when all elements in the response share the same parsing context (all are `<div>` / block, or all are wrapped in an explicit `<table>` / `<tbody>`). Never mix table and non-table elements as OOB siblings.

### Live regions

All HTMX swap targets for list content must include:

```html
aria-live="polite" aria-atomic="false"
```

The `#flash-region` already has these attributes in `base.html`.

---

## Flash / Notification UX


### API

| Where | How |
|---|---|
| HTMX mutation route (Python) | `headers=flash_trigger(level, body)` from `src.api.admin.deps` |
| Inline / non-HTMX (Jinja) | `{% from "admin/macros/flash.html" import message as flash_message %}` then `{{ flash_message(level, body) }}` |

### Levels

| Level | CSS class | Color (light) |
|---|---|---|
| `success` | `.flash--success` | Green |
| `info` | `.flash--info` | Blue |
| `warning` | `.flash--warning` | Yellow |
| `error` | `.flash--error` | Red |

### Behavior

- Auto-dismiss after `auto_dismiss_ms` (default 4000ms)
- Hover pauses the dismiss timer; mouseleave restarts it
- Close button (`x`) removes immediately
- Animation: `flash-in` — 0.2s ease fade + slide from top

### XSS prevention

**Two escaping contexts — not competing approaches:**

| Context | Tool | Reason |
|---|---|---|
| Normal template variables (`{{ org.name }}`) | Jinja2 autoescape (automatic) | `Jinja2Templates` sets `autoescape=True` globally — no developer action needed |
| Flash body string with intentional HTML markup | `markupsafe.escape()` on user values | Body exits the Jinja2 pipeline via `\| safe` (inline) or JS `innerHTML` (HX-Trigger) — autoescape never runs |

Always `markupsafe.escape()` any DB-derived value before composing a flash body string:

```python
from markupsafe import escape
body = f"Merged org <strong>{escape(org_name)}</strong>."
```

Do **not** add `markupsafe.escape()` to values passed as normal template context — Jinja2 autoescape already handles them, and double-escaping will corrupt output (`&amp;lt;` instead of `<`).

### Persistent banners

For non-dismissible notices (e.g. dup count on org list), use `.alert--notice` with a close button — no auto-dismiss timer.

---

## Pagination Conventions


### Top pagination bar

`.pagination` — flexbox, right-aligned, with border + radius + `--color-surface-0` background:

```css
.pagination {
  display: flex; gap: var(--space-2); align-items: center;
  justify-content: flex-end; font-size: var(--font-size-sm);
  background: var(--color-surface-0); border: 1px solid var(--color-border);
  border-radius: var(--radius-md); padding: var(--space-2) var(--space-4);
}
```

### Sticky footer pagination

`.pagination--sticky` — sticky to bottom of scroll container, uses negative margins to break out of `admin-main` padding, backdrop-filter blur, border-top/bottom only (no side borders or radius):

```css
.pagination--sticky {
  position: sticky; bottom: 0; z-index: 10;
  margin-left: calc(-1 * var(--space-6));
  margin-right: calc(-1 * var(--space-6));
  padding: var(--space-2) var(--space-6);
  background: color-mix(in srgb, var(--color-surface-1) 85%, transparent);
  backdrop-filter: blur(8px);
  border-top: 1px solid var(--color-border);
  border-bottom: 1px solid var(--color-border);
}
```

### Page-size options

Standard select with options: 25, 50, 100, 250.

Both top and sticky footer pagination appear on all list views.

---

## Destructive Actions


### Archive gate

`archived_at IS NOT NULL` required before hard delete. Attempting to delete an active record returns HTTP 409.

### Danger zone

Use `.danger-zone` component for destructive actions on detail views:

```html
<div class="danger-zone">
  <div>
    <div class="danger-zone__label">Delete this organization</div>
    <div class="danger-zone__desc">Must be archived first.</div>
  </div>
  <button class="btn btn--danger btn--sm">Delete</button>
</div>
```

### Flash confirmation

Always show success or error flash after HTMX mutation. Use `flash.oob()` in the partial response.

### Dedup merge

In-place HTMX update (swap the dup pair out of the list) + OOB flash with merge result.

---

## Generic Single-Field Inline Edit Pattern


For any short text field that lives on a detail page and needs inline editing (e.g. pronouns, display label, short description), follow this pattern. The Notes variant (`docs/UI.md` § UI Components) is a special case of this general form.

### HTML structure

**Read partial** (`_{field}_read.html`):

```html
<div id="{field}-field" style="margin-top:var(--space-5)">
  <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:var(--space-3)">
    <h3 class="field-group-label">{Label}</h3>
    {% if not entity.archived_at %}
    <button type="button" class="btn btn--sm btn--secondary"
            hx-get="/admin/{entities}/{{ entity.id }}/inline/{field}/edit/"
            hx-target="#{field}-field"
            hx-swap="outerHTML">Edit</button>
    {% endif %}
  </div>
  <div style="font-size:var(--font-size-sm);color:{% if entity.{field} %}var(--color-text){% else %}var(--color-text-muted){% endif %}">
    {{ entity.{field} or '—' }}
  </div>
</div>
```

**Edit partial** (`_{field}_form.html`):

```html
<div id="{field}-field" style="margin-top:var(--space-5)">
  <form hx-post="/admin/{entities}/{{ entity.id }}/inline/{field}/"
        hx-target="#{field}-field"
        hx-swap="outerHTML">
    <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:var(--space-3)">
      <label for="{field}-input" class="field-group-label">{Label}</label>
      <div>
        <button type="submit" class="btn btn--primary btn--sm">Save</button>
        <button type="button" class="btn btn--secondary btn--sm"
                hx-get="/admin/{entities}/{{ entity.id }}/inline/{field}/"
                hx-target="#{field}-field"
                hx-swap="outerHTML">Cancel</button>
      </div>
    </div>
    <div class="form-group" style="margin-bottom:0">
      <input id="{field}-input" type="text" name="{field}"
             value="{{ entity.{field} or '' }}"
             placeholder="…">
    </div>
  </form>
</div>
```

### Rules

- `id="{field}-field"` on the outer `<div>` is the shared HTMX target for read and edit partials.
- Read partial uses `<h3 class="field-group-label">`. Edit partial replaces it with `<label for="{field}-input" class="field-group-label">` for proper screen-reader association — no `<h3>` in the edit state.
- Save and Cancel sit in the header row (same `display:flex` row as the label), not in a `form-actions` div. This prevents layout shift on toggle.
- **Archived guard:** the Edit button is wrapped in `{% if not entity.archived_at %}`. Read partials returned by route handlers (not via the full detail page) must still receive `entity` in context with `archived_at` populated.
- Empty/whitespace values saved as `NULL` (`.strip() or None`).
- Non-HTMX fallback: `POST /inline/{field}/` returns `RedirectResponse` to the detail page.

### Routes

```
GET  /admin/{entities}/{id}/inline/{field}/       → read partial
GET  /admin/{entities}/{id}/inline/{field}/edit/  → edit partial
POST /admin/{entities}/{id}/inline/{field}/       → save → read partial
```

---

## Last-Identity Guard: HTMX Response for Blocked Deletes


When a delete is blocked because it would leave the entity with no identity (e.g. deleting the only name), the server cannot return a 4xx — **HTMX ignores non-2xx responses by default** and swaps nothing, showing no feedback.

### Correct pattern

```python
# HTMX path — return 200 with empty body + flash error; row stays in DOM
if is_htmx(request):
    return HTMLResponse(
        content="",
        status_code=200,
        headers=flash_trigger("error", "Cannot remove the only name."),
    )
# Non-HTMX path — browser receives a proper error response
raise HTTPException(status_code=409, detail="Cannot remove the only name.")
```

- Empty `content=""` means the HTMX swap target is unchanged — the row stays in the DOM.
- The flash delivers the error message via `HX-Trigger: {"showFlash": {...}}` (§ Flash / Notification UX).
- The `is_htmx()` check must come before the guard logic so the non-HTMX path raises a meaningful HTTP error for API callers.

### When this applies

Any route that enforces a minimum-count or canonical invariant:
- Last name on a person or org (delete)
- Last acronym on an org when no canonical name exists (delete)
- Edit route that would leave zero canonical names (see § Canonical Invariant Guard on Edit Routes below)

### Contrast with archive gate

The archive gate (§ Destructive Actions) blocks hard delete when `archived_at IS NULL` — that can safely return 409 unconditionally because the delete button is served via HTMX but the error surface is the `delete_modal.html` which reads the response status explicitly.

---

## Canonical Invariant Guard on Edit Routes


Name edit routes (`POST /{name_id}/edit-row/`) must never allow the entity to end up with zero canonical names. The `_maybe_promote_sole_name` helper only fires when exactly one name exists; it does not cover the multi-name case where the user unchecks canonical on the only canonical row.

### Guard pattern

Check **before** the transaction whether the edit would leave zero canonical names:

```python
if is_canonical != "true" and existing["is_canonical"]:
    other_canonical = await db.fetchval(
        "SELECT id FROM person_names"
        " WHERE person_id=$1 AND is_canonical=TRUE AND id != $2",
        person_id, name_id,
    )
    if not other_canonical:
        if not is_htmx(request):
            return RedirectResponse(f"/admin/people/{person_id}/", status_code=303)
        return HTMLResponse(
            content="",
            status_code=200,
            headers=flash_trigger(
                "error",
                "Cannot remove canonical. Promote another name first.",
            ),
        )
```

- The guard fires only when the row being edited **is currently canonical** and **is_canonical is not being re-asserted**.
- HTMX path: HTTP 200, empty body, flash error (same pattern as § Last-Identity Guard: HTMX Response for Blocked Deletes).
- Non-HTMX path: `RedirectResponse` 303 (not 409 — there is no modal to surface an error, so redirect is the least-surprising degradation).
- The guard does **not** fire when editing a non-canonical row (no invariant at risk).
- The correct workflow for changing which name is canonical: promote the replacement (check its canonical toggle), which atomically demotes the current canonical via the existing `is_canonical == "true"` branch.

---

## Per-Entity Live Header Sync


Full reference for the live header sync pattern introduced in `docs/UI.md` § Page Header Pattern. Follow this checklist when adding it to a new entity type.

### Checklist

- [ ] `src/static/admin/{entity}-detail.js` — event listener
- [ ] `{entity}_header_extra()` in `src/api/admin/deps.py`
- [ ] `extra=` argument on all name-mutation routes
- [ ] `<script src defer>` in `base.html`'s `<head>` (NOT the detail template's `extra_head` — hx-boost strips it)
- [ ] 5 structural tests in `tests/api/admin/test_js.py`

### JS file (`src/static/admin/{entity}-detail.js`)

```javascript
document.addEventListener('update{Entity}Header', function (e) {
  var display = e.detail && e.detail.display ? e.detail.display : '';
  var h1 = document.getElementById('page-heading');
  var crumb = document.getElementById('breadcrumb-current');
  if (h1) h1.textContent = display;
  if (crumb) crumb.textContent = display;
  if (display) document.title = display + ' \u2014 {Entity type label}';
});
```

- Event name is `update{Entity}Header` — camelCase, e.g. `updatePersonHeader`.
- `\u2014` is an em dash — never a hyphen.

### `deps.py` helper

```python
async def {entity}_header_extra({entity}_id: str, db) -> dict:
    """Return extra dict for flash_trigger with the current {entity} display name."""
    row = await db.fetchrow(
        "SELECT display_name FROM v_{entity}_display_names WHERE {entity}_id=$1",
        {entity}_id,
    )
    display = row["display_name"] if row and row["display_name"] else {entity}_id
    return {"update{Entity}Header": {"display": display}}
```

### Mutation route usage

```python
headers=flash_trigger(
    "success",
    f"Name <strong>{escape(name)}</strong> saved.",
    extra=await {entity}_header_extra({entity}_id, db),
)
```

Pass `extra=` on every route that creates, edits, or deletes a name row — including deletes (the display name may change after a deletion removes the canonical).

### Script loading (`base.html`)

Load the listener site-wide from `base.html`'s `<head>` — never a detail template's `extra_head`, which hx-boost strips on boosted navigation (#237):

```html
<script src="/static/admin/{entity}-detail.js?v={{ asset_version }}" defer></script>
```

`?v={{ asset_version }}` is the commit-hash cache-bust injected at startup; no manual increment needed.

### Structural tests (`test_js.py`)

Add a block after the analogous org-detail block:

```python
_ENTITY_DETAIL_JS_PATH = Path("src/static/admin/{entity}-detail.js")
ENTITY_DETAIL_JS = _ENTITY_DETAIL_JS_PATH.read_text() if _ENTITY_DETAIL_JS_PATH.exists() else ""

def test_{entity}_detail_js_exists():
    assert _ENTITY_DETAIL_JS_PATH.exists()

def test_{entity}_detail_js_listens_for_update_{entity}_header():
    """Listener must be keyed to update{Entity}Header — any other name breaks sync silently."""
    assert "update{Entity}Header" in ENTITY_DETAIL_JS

def test_{entity}_detail_js_targets_page_heading():
    """Must target id='page-heading' on the <h1> — changing the ID breaks live sync."""
    assert "page-heading" in ENTITY_DETAIL_JS

def test_{entity}_detail_js_targets_breadcrumb_current():
    """Must target id='breadcrumb-current' on the breadcrumb span."""
    assert "breadcrumb-current" in ENTITY_DETAIL_JS

def test_{entity}_detail_js_updates_document_title():
    """Must update document.title — tab title sync is the third live-update target."""
    assert "document.title" in ENTITY_DETAIL_JS
```

---

## Activity › API Requests screens (#260)


Observability of public API traffic, under the **Activity** section
(`src/api/admin/activity_requests.py`, templates under
`src/templates/admin/activity/requests/`). Read-only over `api_request_log` — see
`docs/CONVENTIONS.md` § "API request log" for the capture/data contract.

- **Landing card** — `admin/activity/index.html` gains an "API Requests" card
  with a 24h pulse subtitle (`N requests · M rejected`) linking to the list, plus
  a "Busiest key (24h)" line (#294) so a runaway client is visible from the
  landing page.
- **Dashboard panel** — `admin/dashboard.html` Activity grid gains an
  "API Activity (24h)" card: total requests, observation dispositions (new /
  attached / **rejected**), changes polls/rows, error rate, last-request time.
  Rejections and a non-zero error rate render in `--color-danger`.
- **Per-key panel** (#294) — on the list page, between the stats strip and the
  filter form: one row per key active in the window (hottest first, from
  `src.core.anomaly.key_activity`), showing label (links to the key-filtered
  list), request count, req/hr, 429 count (danger-colored when non-zero), and
  last-seen. Rows at/above `API_ANOMALY_HOURLY_THRESHOLD` req/hr get the problem
  tint + a "hot" badge (threshold `<= 0` disables highlighting). The req/hr rate
  is a **window-average** — a short burst dilutes across a wide window, so the
  hourly anomaly timer, not this panel, is the burst detector. NULL-key traffic
  aggregates into a "— (no key)" row (`data-key-row="anon"`). Same 24h/7d window
  as the stats strip.
- **List** — `/admin/activity/requests/`. Stats strip (24h/7d window toggle via
  `request.url.include_query_params`), filter form (endpoint group defaulting to
  observations+changes, key **by label**, status class, disposition, free-text
  on path/reason), empty `/changes` polls hidden by default with a "Show empty
  polls" toggle. Rows with `status_code >= 400` or `disposition='rejected'` are
  tinted `rgba(220,38,38,0.06)`. Filters live in query params so a filtered view
  is shareable. Offset pagination (consistent with Import History), keyset on
  `id DESC`.
- **Detail** — `/admin/activity/requests/{id}/`. Metadata table + pretty-printed
  request/response JSON (`<pre>`, monospace). `result_entity_id` resolves to an
  admin entity link (`person` → `/admin/people/`, `organization` →
  `/admin/orgs/`) with a "(removed)" fallback when the entity no longer exists;
  the resolved key label + scopes are shown. `active_section = 'activity'` on all
  three routes so the sidebar Activity link stays highlighted.
- **PII** — the list shows metadata only; raw bodies live on the detail view
  (admin-authed). No live auto-refresh — reload to refresh.
