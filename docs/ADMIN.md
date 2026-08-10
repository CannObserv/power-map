# power-map — Admin Server Conventions

Server-side conventions for the Jinja2 + HTMX admin dashboard: auth, the archive
model, HTMX partial responses, flash notifications, and the per-panel rules each
admin surface follows. Client-side patterns live in `docs/STYLE.md`, `docs/UI.md`,
`docs/HTMX.md`, `docs/FORMS.md` and `docs/MERGE.md`; accessibility in
`docs/ACCESSIBILITY.md`.

---

## Admin Server Conventions


### Key rules at a glance

One-line-per-rule index of this section — read it first, then the subsection
a rule names. Where an entry ends `Full rules → …`, the target is a
subsection of this same section.

- **JavaScript is required to edit (#287)** — the admin is an HTMX application, not a progressively-enhanced one. Browsing degrades (hx-boost over real links); editing does not. The 303 fallbacks serve non-HTMX **clients**, not JS-disabled browsers. Full rules → `§ JavaScript is required`
- Auth: `user: AdminUser = Depends(get_admin_user)` on every route — raises `HTTPException(307)` redirect when exe.dev headers absent
- Archive model: `archived_at TIMESTAMPTZ` — NULL = active, non-NULL = archived; hard delete requires archived (409 otherwise); archive/unarchive both return 409 if already in that state
- HTMX partials: `is_htmx(request)` from `src.api.admin.deps` (checks `HX-Request and not HX-Boosted`); always include `RedirectResponse` fallback, wrapped with `with_flash(url, key)` (`saved`/`removed`/`invalid`/`exists`) so non-HTMX mutations confirm on the target page (#351); CI-enforced by `test_mutation_fallback_sweep.py`
- Flash: `flash_trigger(level, body, extra=None)` on mutation routes; always `markupsafe.escape()` DB-derived values
- Dup counts: `await invalidate_dup_count_cache(db)` (from `org_dups` or `people_dups`) after any merge or dismiss; `db` must be the route's connection
- List status filters (#306): each `*_queries.py` declares `STATUS_PREDICATES` + `VALID_STATUSES` (incl. first-class `all`); unknown status → `active`, never no-filter. A search must never silently hide other-status matches — `query_*_rows` returns `hidden_matches` (grouped `count(*) FILTER` pass via `list_status.count_with_hidden_matches`) rendered as the "N more matches — Show all" affordance (`admin/_hidden_matches.html`; plain link, not hx-get). Full rules → `§ Admin Server Conventions
- Citations indicator (#341): row-level active-citation counts come from the **row-fetch SQL** via `citation_count_lateral` (`_citations_shared.py`) — never a side template dict (side dicts go stale on single-row HTMX re-renders). Non-drawer rows render the `citation_indicator` macro; #319 Cite-drawer rows (names/events) hold the count in a `cite-count-<id>` span the citations factory OOB-refreshes on create/delete. Full rules → `§ Admin Server Conventions

### JavaScript is required

**Policy (#287, audited 2026-08-10): the admin requires JavaScript. The public API is the no-JS / programmatic surface.**

The audit measured the admin surface rather than arguing it:

| Surface | Count | Reachable without JS |
|---|---|---|
| `hx-get` reveals (add-row, inline edit, merge modal) | 180 | no — the form never enters the DOM |
| `hx-post` / `hx-delete` controls | 147 | no |
| `<form hx-post>` carrying an `action=` | 0 of 24 | no |
| `<form method="POST" action=…>` | 9 (1 = logout) | yes |
| Typeahead comboboxes | 12 | no |

**Reading already degrades** — `hx-boost="true"` on `.admin-layout` sits over real `<a href>` links, so every list and detail page renders in a JS-disabled browser. **Writing does not, and cannot cheaply.** The blocker is the `hx-get` reveal, not the mutation buttons: every inline add/edit form is fetched as a partial on demand, so converting all 147 mutation controls to `<form>`s would buy archive/unarchive/delete and still leave a no-JS operator unable to add a name, event, contact, address, link, identifier, child org, affiliation, role, relationship, or citation. Typeaheads have no no-JS equivalent (their endpoints return `<ul>` swap fragments; a `<select>`-of-everything doesn't scale to the people/orgs tables), and merge holds selection state entirely client-side.

Consequences to hold to:

- **Don't add `<form method="post" action="…" hx-post="…">` as a progressive-enhancement pattern.** Half-measures cost real markup on every control and still leave the admin unusable without JS. Every Danger Zone control an entity *has* is a bare `hx-post` / `hx-delete` button, across all five entity types — ratcheted by `tests/api/admin/test_js_required_policy.py`. (Which actions each entity has differs: roles have no unarchive — see `§ Archive model`.)
- **Keep every 303 fallback, and keep the sweep.** Their job is a defined contract for non-HTMX clients (the public API, tests, `curl`) and the substrate that makes the #280 / #349 / #351 correctness guarantees expressible — *not* graceful degradation for browsers.
- **`base.html` ships a `<noscript>` banner** so a JS-disabled operator is told the admin needs JS instead of clicking inert controls.
- **This is not an accessibility decision.** Screen readers run JavaScript; the a11y programme (#300 / #369, three tiers plus the weekly sweep) is unaffected. See `docs/ACCESSIBILITY.md`.
- **#280 is not a counterexample.** Its non-HTMX address-persist path (`_addresses_shared.py`) hardens a contract for non-HTMX *clients*; the address form it protects is itself only reachable via `hx-get .../addresses/new-row/`, so a JS-disabled browser never reaches that code path.

The top-level create/edit forms (people, orgs, roles, jurisdictions, role-assignments) remain real `<form method="POST" action=…>` elements and 303 unconditionally. That is not a no-JS commitment — it's how boosted native form posts already work; leave them alone.

### Auth

exe.dev proxy injects `X-ExeDev-UserID` + `X-ExeDev-Email` headers. Missing headers → redirect to `/__exe.dev/login?redirect=<url-encoded path+query>`.

Every route handler: `user: AdminUser = Depends(get_admin_user)` — `get_admin_user` (from `src.api.admin.deps`) raises `HTTPException(307)` with `Location` header; FastAPI propagates the redirect automatically.

### Archive model

`archived_at TIMESTAMPTZ` — NULL = active, non-NULL = archived.

- Hard delete: gated on `archived_at IS NOT NULL` (returns 409 if not archived)
- `POST /{id}/unarchive/`: sets `archived_at = NULL`, preserves prior `active` state (returns 409 if not archived)
- Archive: returns 409 if already archived — enforced across all entity types (orgs, people, roles, role-assignments)
- Flash on detail pages: `org_detail`, `person_detail`, `ra_detail` accept `?flash=` param via `resolve_query_flash`; add entity-specific flash keys to the module-level `_FLASH_MESSAGES` dict. `resolve_query_flash` also falls back to `SHARED_FLASH_MESSAGES` (`saved`/`removed`/`invalid`/`exists`) — the generic ancillary-fallback keys (#351) — so a target route need not re-declare them; a route-local key of the same name still wins

**Danger Zone interaction model (#281, extended #287).** The Danger Zone actions on entity-detail pages share one HTMX model across **all five** entity types (orgs, people, jurisdictions, roles, role-assignments — the last two were `<form method="POST">` holdouts until #287). Archive / unarchive / delete are `hx-post` / `hx-delete` buttons (no `<form method="POST">`), and each route branches on `is_htmx(request)`:

- HTMX, **archive/unarchive** (target = detail page) → `Response(status_code=204, headers={"HX-Location": target})`. `HX-Location` is a client-side `htmx.ajax('GET', target)` that swaps the response `innerHTML` into `<body>` — **not** a full navigation. It is safe here only because detail GET routes (`org_detail`/`person_detail`/`ra_detail`/…) render the **full page unconditionally** (no `is_htmx` branch), so the body-swap lands the whole layout.
- HTMX, **delete** (target = list page) → `Response(status_code=200, headers={"HX-Redirect": target})` — a real `window.location` navigation (#376). **Do not use `HX-Location` here:** its follow-up ajax GET carries `HX-Request`, so the list route's `is_htmx(request)` returns the `_region.html` **partial** (table only) and HTMX swaps that fragment into `<body>` — you lose header/nav/chrome. `HX-Redirect` issues a plain browser GET (no HX headers) → the list renders full `list.html`. Same pattern as `roles.py::role_delete`. The `?flash=deleted` query rides along on the navigation.
- non-HTMX → `RedirectResponse(target, status_code=303)` — same target. **Note:** the controls are bare `hx-post`/`hx-delete` buttons (no `<form>`), so they require JS; this 303 branch serves direct/non-HTMX POST clients (API, tests), **not** JS-disabled browsers, where the buttons are inert. That is the settled, admin-wide policy — see `§ JavaScript is required` (#287).

`target` for archive/unarchive is the detail page (`/admin/{entities}/{id}/?flash=archived|unarchived`); for delete it is the list page (`?flash=deleted`). The **detail vs list target** distinction is exactly why archive/unarchive can use `HX-Location` but delete must use `HX-Redirect` (list routes render partials under `is_htmx`; detail routes render full). All four entity deletes (orgs, people, jurisdictions, role-assignments) plus roles follow the `HX-Redirect` rule. 409-on-already-in-state guards fire before the branch, so they hold for both request kinds. The org "Restore from archive" control in `orgs/partials/_active_toggle.html` follows the same `hx-post` model.

**Not every entity has all three actions.** Roles carry **archive + delete only — there is no `role_unarchive` route or control**, so an archived role's sole exit is hard delete. That asymmetry is pre-existing and out of scope for #287; tracked separately (see #424). People, jurisdictions and role-assignments have explicit unarchive controls; orgs restore via the `_active_toggle.html` partial.

### List status filters & search discoverability (#306)

**Default status filters never silently hide search matches.** Every admin list (orgs / people / roles / role-assignments / jurisdictions) filters by a status axis defaulting to `active`; a name search under that default used to drop same-named rows sitting under another status — the dedup-hunting trap of #306 (two "WA House RSG" orgs, one `active=FALSE`, only one visible).

The pattern, shared across all five lists:

- Each `*_queries.py` declares its axis as `STATUS_PREDICATES` (status → SQL predicate, in dropdown order) and `VALID_STATUSES = set(STATUS_PREDICATES) | {"all"}`. `all` is a first-class validated status (fourth dropdown option, no predicate); an **unknown status falls back to `active`** — never to no-filter (pre-#306, `?status=banana` silently returned everything including archived).
- When a search is active (`q`; for roles also `org_q`), the count query is one grouped pass via `count_with_hidden_matches()` from `src.api.admin.list_status` — `count(*) FILTER (WHERE <predicate>)` per status — and the query helper returns `hidden_matches`: `{"status", "count"}` per non-current status holding matches. Extra list filters that aren't the status axis (jurisdictions' `type`) constrain those counts like any search condition.
- `query_*_rows` returns `(rows, count, pctx, hidden_matches)`; list routes and list-flow merge branches put `hidden_matches` in the template context.
- `_region.html` renders `admin/_hidden_matches.html` above the table: "N more matches outside the current status filter (…) — Show all". **The Show all link is a plain `<a>`, not `hx-get`** — the status dropdown lives in `list.html` outside the swap region, so only a full-page render keeps it in sync with `status=all`.
- Merge-flow `_VALID_STATUSES` duplicates are gone: `orgs_merge.py` / `people_merge.py` / `orgs_roles.py` import `VALID_STATUSES` from their `*_queries.py` so route, merge filter parsing, and query can't drift.

Deliberate exception: the public API `/search` endpoints (orgs, people) filter archived rows behind an explicit, documented `include_archived=false` query param — opt-in, not silent — and stay as they are (see `docs/CONVENTIONS.md`).

### HTMX partial responses

`is_htmx(request)` from `src.api.admin.deps` — checks `HX-Request and not HX-Boosted`. Boost sends both headers; omitting the `not HX-Boosted` guard causes boosted sidebar nav to receive bare fragments instead of full page layouts.

Always include a `RedirectResponse` fallback on mutation routes. It exists for non-HTMX **clients** — the public API, tests, `curl` — and as the substrate the #280 / #349 / #351 correctness rules are written against. It is **not** graceful degradation for JS-disabled browsers: the controls that call these routes are bare HTMX buttons and are inert without JS (see `§ JavaScript is required`, #287).

**Non-HTMX fallback is CI-enforced (#349).** Every admin mutation route (POST/PUT/DELETE/PATCH) must carry the branch — delete handlers included, even when the HTMX response is just an empty body or OOB fragment: non-HTMX → `RedirectResponse(<owning detail/list url>, status_code=303)` after the mutation. The archive-style variant (`Response(204, HX-Location)` for HTMX + `RedirectResponse` fallback) also satisfies it. `tests/api/admin/test_mutation_fallback_sweep.py` AST-sweeps `src/api/admin/*.py` and fails on any mutation handler whose body lacks `is_htmx` / `RedirectResponse` / `HX-Location`; vetted exceptions go in its `ALLOWED` set with a reason. #349 retrofitted 12 delete-family handlers (the four ancillary factories + addresses, relationships, affiliations, org children, API keys). Shared-factory redirects use the factory's `detail_url` param — citations via its `_dest` helper, which prefers `redirect_resolver` for indirect citable types (`person_name` → owning person).

**Fallback redirects carry a `?flash=` confirmation (#351).** A non-HTMX 303 fallback landed on the detail/list page silently — the HTMX `HX-Trigger` flash a redirect can't carry. Every fallback now wraps its URL with `with_flash(url, key)` (from `src.api.admin.deps`), appending one shared key — `saved` (create/edit ok), `removed` (delete ok), `invalid` (bad input, nothing changed), `exists` (uniqueness/blocked conflict) — that the target route surfaces via `resolve_query_flash` → `SHARED_FLASH_MESSAGES`. The keys are deliberately generic: the static `?flash=` param can't carry the per-row value the HX-Trigger flash includes, matching the Danger Zone precedent. Severity levels (#353 taxonomy, see "Flash notifications → Level taxonomy"): `saved`/`removed` → success (a delete is a successful mutation, aligned with the Danger Zone `deleted` precedent), `invalid`/`exists` → warning. Every redirect *target* route resolves flash: the five entity detail/list routes already did; #351 wired the settings catalogs (api-keys / link-types / identifier-types), both `duplicates` pages, and the roles list, each rendering the shared `{% block flash %}`. A second AST sweep in `test_mutation_fallback_sweep.py` (`test_every_nonhtmx_fallback_redirect_carries_flash`) fails on any mutation-handler `RedirectResponse` whose target carries no `flash` — resolving a flash-bearing local variable (the Danger Zone `target = f"...?flash=archived"` idiom) before flagging; vetted exceptions go in `FLASH_ALLOWED`.

### hx-boost re-execution

`hx-boost="true"` on `admin-layout` makes navigation a boosted swap: htmx fetches the full page, then **discards the response `<head>` entirely** (its fragment parser strips `<head>…</head>`) and swaps only the `<body>` plus `<title>`. Two consequences:

- **Body `<script src>` tags re-run on every boosted navigation.** A persistent `document.addEventListener` in `<body>` accumulates duplicate listeners. For unavoidable inline body scripts use the replace-then-add idiom: `document.removeEventListener(evt, document.__pmKey); document.__pmKey = fn; document.addEventListener(evt, document.__pmKey)` — see `base.html` `aria-busy` and `__pmNavKeydown`.
- **Head `<script>` tags in a _detail template's_ `extra_head` never run when the page is reached via a boosted link** — they are stripped with the rest of `<head>`. They execute only on a full (non-boosted) page load.

So any script that must run on (or register listeners for) a detail page reached by clicking an in-app link belongs in **`base.html`'s `<head>`**, which loads once on the first full page load and persists across every boosted swap. Scripts loaded this way today: `htmx`, `dark-mode.js`, `admin-modal.js`, `flash.js`, `typeahead-combobox.js`, and the detail-interaction scripts (`org-detail.js`, `person-detail.js`, `role-merge.js`, the list-merge engine + consumers `merge-mode.js` / `people-merge.js` / `orgs-merge.js` / `roles-merge.js` (#249/#250/#251), `add-row-guard.js`, the `person-name-*` editor scripts, and `event-form-row.js` — the entity-event form-row typeahead + linked-entity scope wiring, #172). See #237.

### Page-specific scripts

Detail pages once injected their scripts via `{% block extra_head %}`. **Do not** — `extra_head` renders inside `<head>`, which hx-boost strips from boosted-navigation responses, so the script silently never runs when the page is reached by clicking a link (#237). Instead:

- **Persistent listeners, or any behavior needed on a boost-reached detail page** → load from `base.html`'s `<head>` with `defer`. Because it now loads on every admin page, make the script defensive (no-op when its target elements are absent); if it binds per-element, make it idempotent and re-bind on `htmx:load` (the boosted-swap signal) without double-binding.
- Extract inline scripts to files in `src/static/admin/` — no inline `<script>` blocks.

`{% block extra_head %}{% endblock %}` remains available for `<link>` / meta tags or page-specific assets whose effect need not survive a boosted navigation.

### Flash notifications

`flash_trigger(level, body, extra=None)` from `src.api.admin.deps` — sets `HX-Trigger: {"showFlash": {...}}`; `flash.js` injects the flash into `#flash-region`.

- Pass as `headers=flash_trigger(level, body)` to `TemplateResponse`
- For non-HTMX inline flash: `message(level, body)` from `admin/macros/flash.html`
- Always `markupsafe.escape()` DB-derived values before interpolating into `body`
- `extra` co-emits additional HX-Trigger events (merged into one JSON object): `flash_trigger("success", "Saved.", extra={"myEvent": {...}})`

**Level taxonomy (#353) — one convention per action class, HTMX HX-Trigger and non-HTMX fallback alike.** The level answers *"did the action succeed"*; the **body text** carries *what happened*. Same verb ⇒ same level across every admin surface (a Danger Zone delete and an ancillary delete both flash `success`; an HTMX rejection and its non-HTMX fallback both flash `warning`).

| Level | When | Examples |
|---|---|---|
| `success` | any mutation that **changed state** — create / edit / delete / archive / unarchive / dismiss / toggle | "Saved.", "Name removed.", "Person deleted.", "Marked inactive." |
| `warning` | mutation **rejected, nothing changed** — bad input, uniqueness / 409 conflict, business-rule violation | "Couldn't save — check your input.", "Cannot delete: this link type is in use.", "Current assignments cannot have an end date." |
| `error` | **unexpected operation failure only** (not user error) | client-side "Copy failed — clipboard access denied" (`_link_row.html`) |
| `info` | **retired** from mutation confirmations | — |

Deletes/removes flash **`success`** (the body — "removed"/"deleted" — carries the destructive meaning), never `info`: `info` would put an irreversible Danger Zone hard-delete at a *lower* alarm than unlinking a re-addable child. A user-input rejection flashes **`warning`**, never `error`: red is reserved for real operation failures. No server-side `flash_trigger` emits `error` — the only `error`-level flash is the client-side clipboard-copy failure in `admin/*/partials/_link_row.html` (a genuine operation failure, the exemplar of the reserved level). CI-enforced by `test_flash_levels.py` (no `info` level; no `error` level outside `ERROR_ALLOWED`; every server-side `flash_trigger` level is a string constant unless allowlisted; registries conform). The generic fallback keys map `saved`/`removed` → success, `invalid`/`exists` → warning (`SHARED_FLASH_MESSAGES`).

### Page header sync

On any mutation route that may change an org's canonical name or acronym, pass `extra=await org_header_extra(org_id, db)` to `flash_trigger` (from `src.api.admin.deps`). Returns `{"updateOrgHeader": {"display": ...}}`; `org-detail.js` handles the event and updates `#page-heading`, `#breadcrumb-current`, and `document.title` in-place. Equivalent `person_header_extra` for person routes. → `docs/HTMX.md` § Per-Entity Live Header Sync for full client-side pattern.

### Lingering-state warnings (#307)

When an entity enters a terminal-ish state (archived / inactive / lifespan ended) while still carrying live children that now read as stale — e.g. an org past its lifespan with open role assignments — surface it twice:

- **Persistent banner** on the detail page: `alert alert--warning` block under the page header, rendered whenever `<terminal condition> AND <live-children count>`. The banner body lives in a shared partial (`admin/orgs/partials/_lifespan_banner.html`, `{% if open_assignment_count %}`) wrapped by a stable-id container (`<div id="org-lifespan-banner">`) on org detail. Banner names the count, the state, the boundary date when known, and the remedy ("close or re-home"). Persistent beats transient here — the condition outlives the mutation that created it (archive redirect, merge, external ingest).
- **In-place OOB re-render on the active toggle** (#320): the toggle is an inline HTMX POST that never reloads the page, so it must re-render the banner itself or it goes stale (banner lingers after re-activating, and never appears when deactivating). The toggle POST returns `_active_toggle_response.html` — the toggle partial (primary swap into `#active-toggle`) plus an `hx-swap-oob="true"` copy of the `#org-lifespan-banner` container. Both the detail GET and the toggle derive `(open_assignment_count, org_ended_on)` from the one helper `resolve_lifespan_banner(conn, org)` (`src.core.org_lifecycle`), so the "when to warn" gating (`archived_at OR not active OR ended`) can't drift between surfaces.
- **Warning flash** on the mutation that creates the condition: upgrade the flash to `level="warning"` and append the count + remedy (deactivate toggle), or append a `" Warning: …"` suffix to a success flash when the mutation's primary outcome succeeded (org merge into an ended/inactive winner — `_winner_lifespan_note` in `orgs_merge.py`).

Count predicates live in `src.core` next to the domain logic (`count_open_assignments`, and the banner-gating `resolve_lifespan_banner`, in `src.core.org_lifecycle`), never inlined per-route — neither the "open" definition nor the "when to warn" gating may drift between surfaces. Domain rules → `docs/OBSERVATIONS.md` §"Org lifespan bounds on assignments".

### Person-name metadata controls (Phase 2a–2d, #123)

Person-name CRUD shares its router factory with org-name CRUD via `make_names_router` in `src.api.admin._names_shared`. The factory accepts `supports_person_metadata: bool = False`:

- `org_names`: leaves the default (`False`) — `organization_names` has no person metadata columns.
- `people_names`: passes `supports_person_metadata=True` — accepts `visibility`, `locale`, `script`, `sort_as` Form fields on create/edit and persists them.

A second, independent gate `supports_effective_dates: bool = False` (#239) controls the org name-validity timeline:

- `org_names`: passes `supports_effective_dates=True` — the create/edit form accepts `effective_start` / `effective_end` date inputs and writes them to `organization_names` (form-as-source-of-truth: an empty input clears the column to NULL). `_parse_optional_date` converts empty strings to None; a malformed value raises `_DateParseError` → 422/flash. The DB `chk_org_name_effective_date_order` CHECK is caught (`asyncpg.CheckViolationError`, `constraint_name` match) and surfaced as a friendly flash rather than a 500. The org name read row shows the effective range and the names table has an `Effective` column (colspan 5).
- `people_names`: leaves the default (`False`) — `person_names` has no effective-date columns; person forms are unaffected. Kept separate from `supports_person_metadata` so the two entity types stay decoupled.

Validation layering:

- Pydantic / FastAPI validates `visibility` against the `PersonNameVisibility = Literal["public","legal_only","hidden"]` from `src.core.types` at request parse — invalid values return 422.
- `_normalise_optional_str` strips whitespace and converts empty strings to None for `locale`/`script`/`sort_as` so blank inputs become NULL columns rather than ''.
- The org-vs-person divergence is the inline `vis = visibility if supports_person_metadata else None` gate at the top of each handler. The same `... if supports_person_metadata else None` shape is used for `loc`, `scr`, `sa` immediately below. Payloads sent to org_names are silently dropped.
- `_metadata_pairs(...)` returns the canonical (column, value) tuple ordering used by both builder helpers:
  - `_insert_name`: includes a column only when its value is non-None — DB defaults (`visibility='public'`, others NULL) handle the rest.
  - `_update_name(write_metadata=True)`: SETs every metadata column to the supplied value (form is the source of truth) — except visibility, which is skipped when None so the DB default + `trg_deadname_visibility` trigger keep authority.
- FK violations on `locale` / `script` are caught in both create and edit handlers; HTMX → 200 + flash trigger with column-specific message via `_fk_violation_message`; non-HTMX → 422. Never a bare 500.

Locale + script typeahead (Phase 2b):

- HTML option-list endpoints `GET /admin/people/_locale_search` and `/_script_search` (in `src.api.admin.people_locale_script_search`) return `<li role="option" data-id data-label>` partials shaped for the existing `typeahead-combobox.js` factory. Substring filter on code OR human-readable column with `escape_like` + `ESCAPE '\\'`; sorted code ASC; capped at `limit` (default 20, max 100); empty `q` returns no rows.
- The form-row template's display input mirrors its trimmed value to the hidden code field on `blur`, so typed-but-not-selected input still submits — invalid codes then trip the FK-violation flash rather than being silently discarded.

Sort + collation (Phase 2b):

- `v_person_display_names.sort_key = COALESCE(sort_as, name)`. Every person ORDER BY uses `sort_key COLLATE "und-x-icu" NULLS LAST` for diacritic-aware ordering (Å near A) with `sort_as` overrides honored.

Linked names — `reading_of_id` (Phase 2c, #123):

- Name CRUD accepts a `reading_of_id` Form field gated by `supports_person_metadata`. The column is a self-FK on `person_names` (ON DELETE CASCADE) — a `reading` / `romanization` / `mrz` row may point at the visual row it transliterates.
- Typeahead `GET /admin/people/{person_id}/_reading_target_search` returns same-person rows whose `name_type` is OUTSIDE `_READING_TYPES` (`reading`, `romanization`, `mrz`) — only visual rows are valid parents. Filters `visibility = 'public'` to mirror the default detail view; uses `escape_like` + `<> ALL($N::text[])` for the type filter.
- `_validate_reading_of_target` (in `_names_shared.py`) runs before the INSERT/UPDATE and surfaces four bypass attempts as form errors (HTMX flash; non-HTMX 422):
  1. Target row doesn't exist (DB FK catches it too — this gives a friendlier message).
  2. Target is on a *different* person (cross-person link).
  3. Target equals the editing row's own id (self-reference; `name_id` is threaded through on the edit path).
  4. Target's `name_type` is itself in `_READING_TYPES` (chain — A→B→C is rejected even if each link is technically same-person).
- The form template's reading-of block is hidden by default; inline JS shows it when `name_type ∈ _READING_TYPES`.
- Read-row template indents linked rows (`class="name-row--child"`) and renders a "↳ {name_type} of: <em>{parent_name}</em>" subtitle. The handler enriches each row with `reading_of_name` (LEFT JOIN parent) and `reading_child_count` (LATERAL count) — both the detail-page and the post-mutation tbody re-render in `_fetch_names_for_rows` carry the enrichment so cancel-from-edit + post-save look identical.
- Delete confirm text becomes "Delete this name and its N linked reading row(s)? (cascade)" when `reading_child_count > 0`.

Structured parts — `person_name_parts` (Phase 2d, #123 / Issue #127):

- Sidecar table, 1:0..1 with `person_names`, ON DELETE CASCADE.
- Server-side validation (in `upsert_or_delete_parts` helper, `src.api.admin.people_name_parts`):
  - Cap: 5 entries per array (`given_names`, `family_names`, `additional_names`). Cap is checked BEFORE trimming so the message reflects what the user typed.
  - Empty-string trim: blank entries are dropped before INSERT; `_trim_array` preserves user order.
  - `primary_identifier` allowlist matches the DB CHECK (`family` / `given` / `patronymic` / `mononym`); a blank value becomes NULL.
  - All-empty payload semantics (Issue #127): if the row already had a parts row, Save **deletes** it; if it never had one, no-op. There is no separate Remove button — clearing every parts field and clicking Save is the delete path.
- Read-row subtitle: handlers attach a `parts_summary` field via `build_parts_summary(family, given, additional)` from `src.api.admin.deps` — a "<family> · <given> · <additional>" line; None when nothing structural is set so the template's `{% if n.parts_summary %}` guard hides the row.

Person-name editor — single form / single Details disclosure / single Save (Issue #127):

- One `<form>` per name row in `_name_form_row.html` posts to `/edit-row/` (existing rows) or `/` (new rows). The earlier two-form split (outer name form + inner parts form) is gone; one Save commits both halves in one transactional upsert via `name_create` / `name_edit_row_post` calling `upsert_or_delete_parts` inside the existing `async with db.transaction():` block.
- Inline portion of the row (visible without expanding anything): name input, name_type select, is_canonical toggle, Save, Cancel.
- **`name_type` dropdown source of truth**: the `<select name="name_type">` iterates a `name_types` context variable supplied by the names-router factory. People-side routes pass `PERSON_NAME_TYPES` and orgs-side passes `ORG_NAME_TYPES` (both in `src/core/types.py`); the settings page reads from the same constants. To add a new value: update `src/core/types.py` (Literal + tuple) and the schema CHECK in lockstep — the dropdown, settings badges, and parametrized round-trip tests pick it up automatically. `tests/core/test_types.py` enforces tuple ↔ Literal ↔ schema parity and fails on drift. The form handlers also reject unknown `name_type` values with a 422 / flash before they reach the DB CHECK, so a stale cached form surfaces a friendly error instead of a raw `CheckViolationError`.
- A single `<details>` "Details" disclosure (rendered by `_name_parts_editor.html`) holds, in order:
  1. **Metadata**: visibility / sort_as / locale / script / reading_of_id (from `_name_metadata_fields.html` — order paired so flex-wrap puts visibility+sort_as on one row and locale+script on the next at most viewport widths; updated in #131).
  2. `<hr>` separator.
  3. **Name parts**: primary_identifier, the given/family/additional CardStack inputs (`person-name-parts-cardstack.js`), and honorific prefix/suffix.
- Auto-open predicate (in `_name_parts_editor.html`): the disclosure renders with `open` when any non-default metadata or parts value is present on the editing row:
  ```jinja
  {%- set _meta_set = n and (
      (n.visibility and n.visibility != 'public') or
      n.locale or n.script or n.sort_as or n.reading_of_id
  ) -%}
  {%- set _parts_set = parts is not none -%}
  ```
  The `n and (...)` guard is defensive — `_name_form_row.html` only includes the parts editor when `n` is set, but the predicate stays safe if that gate ever changes.
- New-name form (`n is None`) does not render the Details disclosure (no `name_id` to attach parts to). To keep metadata fields reachable when creating a row, `_name_form_row.html` includes `_name_metadata_fields.html` directly inline in that branch — same markup, different host.
- Typeahead init `<script>` (locale / script / reading_of_id) lives in `_name_form_row.html` AFTER the include so it runs once the inputs (which may be inside the disclosure) are in the DOM. Browsers query elements inside `<details>` regardless of open state.
- The standalone `POST /parts/` and `POST /parts/delete/` routes are deleted — `_summary_oob_fragment` and `_ensure_name_belongs_to_person` are gone with them. The unified Save flow re-renders the whole tbody so the OOB-swap pattern is no longer used.

Issue #131 follow-ups (lookup bug fix + redesign II):

- **Typeahead query parameter**: locale / script / reading-of inputs must send `q={value}` to their search endpoints. The display inputs are unnamed (so they don't pollute the parent Save POST) and use `hx-vals='js:{q: (event && event.target ? event.target.value : "")}'` to set the request param. The earlier shape (`name="q_locale"` + `hx-params="q"`) silently sent no `q` because no input was named `q` — the filter dropped everything. Don't reintroduce `hx-params="q"` here.
- **Per-row ID namespacing**: every typeahead element id (`locale-search-display`, `locale-search-results`, `locale-hidden`, the script + reading-of equivalents, and `reading-of-block`) is suffixed with a `_uid` discriminator. `_name_metadata_fields.html` and `_name_parts_editor.html` agree on `{%- set _uid = n.id if n else 'new' -%}`; the form row also exposes `data-name-row-typeahead data-uid="{{ _uid }}"` on its `<tr>` for the per-row JS module to discover. This lets an open Edit drawer and the inline new-name form coexist on the page without `getElementById` collisions.
- **Per-row JS wiring**: typeahead init + reading-of-block toggle live in `src/static/admin/person-name-row-typeahead.js`. The module hooks `DOMContentLoaded` for server-rendered rows and `htmx:afterSwap` for HTMX-injected rows, scans the swap target for `[data-name-row-typeahead]`, and reads `data-uid` to compose the namespaced element ids. No inline `<script>` in `_name_form_row.html`.
- **+ Add duplicate-row guard (`add-row-guard.js`, #238)**: every inline "+ Add" button that prepends an unsaved `<tr id="<entity>-row-new">` is disabled while that row exists, so a double-click can't create two colliding `#<entity>-row-new` rows (the id-collision class #131 / #237 fought on person names and events). A button opts in with **`data-new-row-id="<tr-id>"`** (e.g. `name-row-new`, `acronym-row-new`, `email-row-new`). `src/static/admin/add-row-guard.js` is loaded site-wide from `base.html` (#237), registers document-level listeners once (`htmx:afterSwap`, `htmx:load`, `powerMap:newRowClosed`), and `sync()` scans `button[data-new-row-id]` on every call — disabling each iff its row is present. Document-scoped (not table-scoped) so it survives hx-boost and catches outerHTML row swaps; each `<entity>-row-new` is page-unique, so a global id check is correct. This one guard replaced the per-feature `person-detail-add-name-guard.js` and `event-add-guard.js`, and uniquely handles pages with **multiple** add-buttons (org detail) that a single-button-by-id guard could not. New-row Cancel handlers must dispatch `powerMap:newRowClosed` (they remove the row client-side with no HTMX round-trip); existing-row Cancel uses an `hx-get` read-row swap and needs no dispatch.

  **Two race windows, two owners — don't cross them.** Double-clicking "+ Add" is two separate races: (A) a second request fired *while the first is in flight*, before any row exists; and (B) a deliberate second click *after* the row is rendered. The guard owns **B** — it's a UI invariant over DOM state ("disabled while `#<entity>-row-new` exists"), and it is the **sole writer of `disabled`**. Window **A** is a request-lifecycle concern and belongs to htmx: every guarded button carries **`hx-sync="this:drop"`**, so htmx drops a second concurrent request from the same element. Do **not** use `hx-disabled-elt="this"` here: htmx re-enables a disabled-elt after the swap (`htmx:afterRequest`, after `htmx:afterSwap`), which clobbers the guard's disable and reopens window B (#238 CR). Because `hx-sync` never touches `disabled`, the two mechanisms compose without conflict and without depending on htmx event ordering. In-flight *visual* feedback needs no per-button `hx-indicator`: htmx adds `htmx-request` to the requesting button by default, and the global loading rule (`admin.css` — `.htmx-request { opacity:.6; cursor:wait; pointer-events:none }`) dims it for the request's duration. That rule is CSS-only, so it too leaves `disabled` to the guard.
- **`powerMap:` custom event prefix**: project-wide convention for client-side custom DOM events that don't go through HTMX's `HX-Trigger` header (those follow the `update{Entity}Header` camelCase shape — see [§JS file](#js-file-srcstaticadminentity-detailjs)). Today's only `powerMap:` event is `powerMap:newRowClosed` (dispatched by every new-row inline Cancel; #238); future custom events use the same prefix to avoid colliding with browser/library events. Page-wide `powerMap:*` events are dispatched on `document` and listened on `document` (matches the page-wide `htmx:afterSwap` listener convention used by `person-name-deadname-confirm.js` and `person-name-parts-cardstack.js`); element-scoped events should target the relevant element directly.
- **Hint-as-placeholder convention**: locale/script/sort_as/honorific-prefix/honorific-suffix carry concrete examples in their `placeholder` attributes (e.g. `Locale` → `BCP 47 — e.g. en, en-US, ja-JP`). The previous below-control `<small>` helpers under honorific prefix/suffix are removed; the placeholder is the single source of truth for one-line guidance. Primary Identifier is the exception: its multi-line cultural-context help (`<small>` with `family in Japan; patronymic in Iceland; mononym ...`) sits between the label and the `<select>` — placeholders can't hold that much text.
- **Cardstack inputs full-size**: each card in the given/family/additional CardStacks wraps its `<input>` in `<div class="form-group" style="margin-bottom:0;flex:1">` so the input inherits the baseline `.form-group input` rule (font-size, padding, `min-height: 44px`). A bare `<input style="flex:1">` falls back to browser-default text input styling and renders visibly smaller than the rest of the form.
- **Reorder focus-follows-value (#145)**: after a ↑/↓ click on a cardstack arrow, `person-name-parts-reorder.js` moves focus to the neighbor card's same-direction button so repeated keypresses walk the value through the stack. At the boundary (neighbor's same-direction button is disabled), focus falls back to the neighbor's input — the cell that just received the value. Lookups are scoped to the neighbor element (form-scoped via `cardsIn(stack)`), so concurrent reorder in one form never moves focus out of that form.

### Roles — structural fields (#264)

The roles admin surfaces a role's structural fields (`role_type_id` / `jurisdiction_id` / `qualifier`, #261) under the **Role type** label. Rules that keep the admin from becoming a title-drift vector (#267):

- **One structural block, not three independent fields.** The three columns are constraint-coupled, so they're edited together via a single inline read/edit pair (`_structural_read.html` / `_structural_form.html`) on `#structural-field`, and as one `<fieldset>` on the new-role form. Both use **progressive disclosure**: role-type `<select>` (from `role_types`) → reveal jurisdiction typeahead → reveal qualifier. Selecting role type = "none" clears the jurisdictional sub-fields (demotes it to a plain role). The list + detail **badge shows the role-type name** (`badge--role-type`), never a generic composite label.
- **Jurisdiction typeahead**: `GET /admin/jurisdictions/search/` (`src.api.admin.jurisdictions`) is a read-only `<li role="option" data-id data-label>` fragment for the shared `typeahead-combobox.js` factory. Mirrors `/admin/orgs/search/`. (Jurisdictions also have a full admin surface — see "Jurisdictions — admin surface" below.)
- **Title is PM-curated for a role with a jurisdiction.** Both create and inline structural-save **always synthesize** the canonical title via `src.core.role_title.synthesize_role_title` for a fully-qualified role (WA legislative only) — any supplied title is ignored so the admin can't diverge from the canonical form. A manual title is kept/required only when synthesis is unavailable (non-WA jurisdictions). The free-text title editor is **gated** when a role has a role type: `_title_read.html` shows "Curated from the role type" instead of Edit, and `POST /inline/title/` refuses to retitle a role with a `role_type_id`.
- **Validation mirrors the DB.** Both handlers reproduce `chk_role_qualifier_needs_jurisdiction` (qualifier ⇒ jurisdiction) and `chk_role_jurisdiction_needs_role_type` (jurisdiction ⇒ role type) with clear flash errors, and catch `UniqueViolationError` for both `uq_role_structural` and `uq_role_org_title`.
- **Known gap:** admin `role_create` / inline structural-save write the row directly (no `resolve_role`, so no outbox/entity_changes emission) — consistent with the rest of the admin, unlike the observation path.

### Dup count cache

`count_org_duplicates(db)` in `src.api.admin.org_dups` and `count_person_duplicates(db)` in `src.api.admin.people_dups` are TTL-cached (5 min, process-local). Call `invalidate_dup_count_cache()` from the appropriate module after any merge or dismiss. All people and org routes inject both counts via deps; sidebar badges use these template vars directly (no HTMX XHR).

Caveat: cache is not shared across gunicorn workers — counts may lag by up to 5 min per worker.

### Jurisdictions — admin surface (#275)

`src.api.admin.jurisdictions` surfaces jurisdictions as a first-class managed entity (sidebar link, Entities-index card, dashboard count):

- **List** (`GET /admin/jurisdictions/`) via `query_jurisdictions_rows` (`jurisdictions_queries.py`). Search is **ILIKE on name/slug** — jurisdictions have no `search_tsv` (unlike orgs/people, which use `pm_prefix_tsquery` — last-token prefix FTS, #316). Type filter from `jurisdiction_types`; three-value status axis **active / superseded / archived** partitioning on `(archived_at, superseded_at)` (a superseded row keeps `archived_at IS NULL` — supersession is not soft-delete). HTMX region swap mirrors orgs.
- **Create** (`GET/POST /admin/jurisdictions/new/`): slug/name/type required, slug-uniqueness (422) + validity-range validation, unknown-type guard. Direct `INSERT` (no `resolve_entity`); the `updated_at` + `entity_changes` triggers emit the change feed with no extra plumbing (unlike the roles admin's known outbox gap).
- **Detail** (`GET /admin/jurisdictions/{id}/`): a single inline **Edit details** form (name/slug/type/validity/notes) — slug carries a public-`/resolve`-key caveat + 422s on collision; empty `type_id` keeps the current type. A name change emits `updateJurisdictionHeader` and `jurisdiction-detail.js` (loaded site-wide, boost-safe) updates the heading in place. **Archive/unarchive/delete** mirror the orgs lifecycle (delete requires archived; FK-guarded 409 when referenced by a role/relationship/affiliation). **Attachment panels** (identifiers/links/contacts via the shared factory routers `jurisdictions_{contacts,links,identifiers}.py`; addresses hand-built in `jurisdictions_addresses.py`) are interactive (+ Add / inline edit / delete). **Referencing roles** (= the reciprocal of the role picker) and **Lineage** stay read-only (derived views).
- **Graph editing (Phase 3)**:
  - **Relationship edges** (`jurisdictions_relationships.py`) — interactive Relationships panel: add a typed edge (target typeahead reuses `/admin/jurisdictions/search/`; `rel_type` grouped by category; a **direction** toggle for asymmetric types with a live phrase preview — symmetric types store once, read both ways; validity + notes), inline-edit validity/notes (the temporal *end*), hard-delete (no `archived_at` — the table has none). DB guards `chk_no_self_rel` / `chk_rel_valid_range` surfaced as 422. **Lineage-category** edges are creatable here but render in the read-only Lineage panel, not Relationships (the detail query filters `category <> 'lineage'`). **Category display labels** render via the `rel_category_label` Jinja filter — source of truth `RELATIONSHIP_CATEGORY_LABELS` in `src.core.jurisdictions`, injected on all admin template envs at startup (`inject_rel_category_label_into_admin_templates`), sync with the schema CHECK enum test-enforced (#278).
  - **Change feed**: `jurisdiction_relationships` had no trigger, so a `touch_parent_jurisdiction()` trigger (mirrors `touch_parent_org`) touches both endpoints' `updated_at` on any edge INSERT/UPDATE/DELETE → fires `trg_entity_changes_jurisdictions` on each. Keeps jurisdictions outbox-gap-free.
  - **Org affiliations** (`jurisdictions_affiliations.py`, two routers over one table) — **bidirectional**: the "Affiliated organizations" panel on jurisdiction detail (org typeahead + type) *and* a reciprocal "Affiliated jurisdictions" panel on **org detail** (jurisdiction typeahead + type). Unique `(org, jur, type)` → 409. Affiliation writes touch **both** sides' change feed — the org via `trg_touch_org_on_affiliation_change` and the jurisdiction via `trg_touch_jurisdiction_on_affiliation_change` (#275 Phase 3) — so a subscriber on either entity sees the edit.
- **No dup surface** — jurisdictions have no dup tables (no merge/dismiss, no dup badge).
- **Shared lineage helper**: the recursive lineage CTE lives once in `src.core.jurisdictions.fetch_lineage`, shared by the public lineage endpoint (#168) and the admin detail — anchor on a resolved id (callers resolve slug→id first).

### Person voice-embeddings section (#284)

`src.api.admin.people_embeddings` adds a **read-only** "Voice Embeddings" section to the Person detail view. No create/paste-in (the row's NOT-NULL voice provenance + `created_by_key_id` FK make console entry impractical); no metadata edit; no similarity search.

- **Registry-driven, multi-model**: `fetch_person_embeddings(db, registry, person_id, include_archived=…)` loops `app.state.embedding_registry.all()` and unions rows across every model table, tagging each with its `model_id`. Table names come **only from the registry** (never user input) — same injection-safe pattern as the public embeddings API. Loaded in the `person_detail` handler and rendered server-side like Identifiers.
- **Vector column**: only a preview (`left(embedding::text, 10)`) is rendered in-page; the full 256-float literal is fetched on demand from `GET …/{model_id}/{eid}/vector/` (`PlainTextResponse`) by `embedding-copy.js` (document-delegated, site-wide, boost-safe), which writes it to the clipboard and fires a `showFlash` event.
- **Archived toggle**: `?show_archived_embeddings=1` full-page reload (mirrors the Names `show_historical` pattern) reveals archived rows dimmed; the toggle label shows the archived count.
- **Lifecycle** (archive-model conventions): Delete soft-archives (409 if already archived); Restore clears `archived_at` (409 if already active); **Delete permanently** hard-deletes and **requires the row be archived first** (409 otherwise). Each write is guarded on `archived_at IS [NOT] NULL … RETURNING id` to close the check-then-act window; a matched-nothing write re-checks existence to report 404 (row gone) vs 409 (wrong state). Mutations re-render the **whole** `#person-embeddings-section` (header + table) via `_embeddings_section.html` — not just the tbody — so the "Show archived (N)" toggle refreshes with a fresh count; the current `show_archived_embeddings` state passes through a query param so the swap stays in the same mode; all carry `flash_trigger` + a `RedirectResponse` non-HTMX fallback.

### Citations indicator on entity rows (#341)

Rows whose entity carries active citations surface a compact count so sub-entity citations (esp. `role_assignment`) are discoverable without opening the row.

- **Count lives in the row-fetch SQL, never a side dict.** Every query that feeds a row partial joins `citation_count_lateral(entity_type, id_expr)` (from `src.api.admin._citations_shared`) — one `LEFT JOIN LATERAL count(*)` probe per row on `idx_citations_entity`, active rows only (`archived_at IS NULL`). Rationale: row partials re-render standalone via single-row HTMX routes (read-row Cancel, archive, edit-save); a template-context dict would have to be recomputed on every such path or the indicator silently disappears after a swap. Single-row handlers that build a dict context (e.g. names factory `name_read_row`) may instead attach one scalar `count(*)` — one row, not an N+1.
- **Rendering — two affordance shapes:**
  - Rows *without* an inline Cite drawer (assignment rows, org-roles rows): the `citation_indicator(count, href=…)` macro (`admin/macros/citation_indicator.html`) — `📚 N`, emoji `aria-hidden`, wrapper `aria-label="N citation(s)"`, count as visible text (never emoji-only); pass `href` to the page hosting the citations panel (assignment detail / role detail). Renders nothing at 0.
  - Rows *with* the #319 Cite drawer-toggle button (person-name rows, event rows): the count renders **inside the existing button**, held in a `<span id="cite-count-<entity_id>">` so in-drawer mutations can refresh it — `citation_create`/`citation_delete` in the citations factory emit an `hx-swap-oob` fragment (`admin/citations/partials/_cite_count_oob.html`) with the fresh active count; htmx silently drops the fragment for panel-hosted entity types that have no row button. The button `aria-label` stays **count-free** (a stale label is worse than none; the count is a visual supplement, and the open drawer lists the rows).
- **Scope:** citable sub-entities shown as rows. Top-level entity lists (orgs / people / jurisdictions) are out — those surface citations on their own detail panel. `org_name` is **not** a citable type; org name rows never get a count.
- **Tests:** indicator renders with count ≥ 1, absent at 0, archived citations excluded, and the single-row re-render path keeps the indicator (the regression the SQL-embedding rule exists to prevent).
