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

- **Don't add `<form method="post" action="…" hx-post="…">` as a progressive-enhancement pattern.** Half-measures cost real markup on every control and still leave the admin unusable without JS. Every Danger Zone control an entity *has* is a bare `hx-post` / `hx-delete` button, across all five entity types — ratcheted by `tests/api/admin/test_js_required_policy.py`. (All five now carry archive / unarchive / delete; roles closed the last gap in #424 — see `§ Archive model`.)
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

**All five entities carry all three actions (#424).** People, jurisdictions, roles and role-assignments have explicit archive / unarchive / delete controls; orgs restore via the `_active_toggle.html` partial. Roles were the holdout — archive was a one-way door whose only exit was an irreversible delete — until #424 added `role_unarchive` on the people/jurisdictions shape.

**Restoring can be legitimately blocked (#424).** A **unique index partial on `archived_at IS NULL`** encodes identity among *active* rows only, so archiving a row frees its slot and a new row may reoccupy it before the restore. Four tables carry one:

| Table | Index | Slot | Unarchive path |
|---|---|---|---|
| `roles` | `uq_role_org_title` | (org, `lower(title)`) — role with no jurisdiction | `role_unarchive` — **handled** |
| `roles` | `uq_role_structural` | (org, role_type, jurisdiction, qualifier) — role with one | `role_unarchive` — **handled** |
| `role_assignments` | `uq_role_assignment_person_role_start` | (person, role, start_date) | `ra_unarchive` — **handled** |
| `role_assignment_relationships` | `uq_assignment_relationship_identity` | (from, to, rel_type) | none today |
| `citations` | `uq_citation_identity` | (entity_type, entity_id, field_name, url) | none today |

**The rule is conditional, not a fixed list of two entities:** any unarchive path onto a table in this list needs the treatment below. Relationships and citations are safe today only because nothing sets their `archived_at` back to NULL — add such a path and it inherits the hazard. `people`, `organizations`, `jurisdictions` and `entity_events` carry no `archived_at`-partial unique index, which is why their unarchive handlers are a bare `UPDATE` (verify before assuming a new table joins them: `grep -B3 'archived_at IS NULL' src/core/schema.sql | grep 'CREATE UNIQUE INDEX'`).

The treatment, on `role_unarchive` and `ra_unarchive`:

- The `UPDATE` runs inside `async with db.transaction():` — a savepoint. A plain `execute` that raises leaves the connection's transaction aborted and the response path unusable (same idiom and reason as `ra_create`, #288).
- `asyncpg.UniqueViolationError` is caught and surfaced as a **flash**, not a 409: the admin registers no `HTTPException` handler and no client-side `htmx:responseError` hook, so a 4xx from an `hx-post` is silently inert and the curator sees a dead button. HTMX → `Response(204, headers=flash_trigger("warning", …))` (warning = reject, per `§ Flash notifications → Level taxonomy`); non-HTMX → 303 to the detail page with the shared `exists` key.
- **The message names the remedy the colliding index actually allows.** Roles branch on `jurisdiction_id`: a seat role's title is synthesized from role type + jurisdiction + qualifier, so "rename it" is not on offer there — only "archive the role holding the seat". Same split as `role_create`'s create-time `UniqueViolation` branch. The conflicting title is DB-derived → `markupsafe.escape()`.
- The 409 stays for the "not archived" precondition, which is a race, not a curator-actionable state.

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

### Dup count cache

`count_org_duplicates(db)` in `src.api.admin.org_dups` and `count_person_duplicates(db)` in `src.api.admin.people_dups` are TTL-cached (5 min, process-local). Call `invalidate_dup_count_cache()` from the appropriate module after any merge or dismiss. All people and org routes inject both counts via deps; sidebar badges use these template vars directly (no HTMX XHR).

Caveat: cache is not shared across gunicorn workers — counts may lag by up to 5 min per worker.

### Per-panel rules — moved out

Panel-specific rules live beside the panel they govern, so this file stays the
conventions every admin route needs:

- `docs/ADMIN_NAMES.md` — the person-name editor: metadata gates, locale/script typeahead,
  linked reading rows, structured parts, the single-form/single-Save shape
- `docs/ADMIN_PANELS.md` — lingering-state warnings (#307), roles structural fields (#264),
  the jurisdictions surface (#275), person voice-embeddings (#284), the citations indicator (#341)
