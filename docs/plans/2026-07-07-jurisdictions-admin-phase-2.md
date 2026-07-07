---
title: "#275 Phase 2 — Jurisdictions admin: CRUD parity"
date: 2026-07-07
status: draft
---

# #275 Phase 2 — Jurisdictions admin: CRUD parity

Design: `docs/plans/2026-07-06-jurisdictions-admin-surface-design.md` · Issue: #275
Branch: `feature/275-jurisdictions-admin-phase-1` (Phase 2 continues on it, per decision)

## Problem

Phase 1 made jurisdictions browsable and inspectable but **read-only** — every
detail panel is a static list, there is no create/edit/archive path, and the
attachment panels (identifiers/links/addresses/contacts) can't be mutated. A
curator still can't author a jurisdiction, fix its name/validity, retire it, or
manage its contact details from the dashboard. Phase 2 brings full CRUD parity
with the orgs/people admin. (Graph-edge + org-affiliation editing stays in
Phase 3.)

## Approach

Mirror the orgs admin, reusing the entity-parameterized factory routers. The DB
is already write-ready: `jurisdictions` INSERT/UPDATE auto-fire `updated_at` +
the `entity_changes` outbox, and hard-delete + `deleted_entities` emits the
tombstone — so admin writes land on the public change feed with **no** extra
plumbing (unlike the roles admin's known outbox gap). Create/edit write the row
directly (no `resolve_entity`), consistent with the rest of the admin.

Attachment CRUD is mostly wiring: `make_contacts_router` / `make_links_router` /
`make_identifiers_router` each take `entity_type="jurisdiction"` + a URL prefix +
small template partials (near-copies of the org partials). Addresses is
hand-built, mirroring `orgs_addresses.py` (+ the address-normalizer path). Inline
curatorial edits reuse the orgs read-partial / edit-form / POST pattern.

TDD throughout, mirroring `tests/api/admin/test_orgs*.py`.

## Tradeoffs / alternatives

- **Route create through `resolve_entity` (observation core)** — rejected;
  the admin's other create paths (`org_create`, `role_create`) write rows
  directly. Direct INSERT keeps the admin consistent and the triggers still emit
  the outbox, so there's no change-feed downside.
- **Skip inline header-sync JS** (orgs' `org-detail.js` updates the heading in
  place after a name edit) — accept for Phase 2; a name edit can reflect on the
  next render. Adding a jurisdiction-detail JS is deferrable polish, not parity.
- **Reuse org attachment partials directly** — rejected; the factory routers
  take per-entity partial paths and the row markup references entity-specific
  URLs, so jurisdictions get their own thin partials (copied + retargeted).

## Steps

1. **Create** — RED: `test_jurisdiction_create_*` (GET `/new/` renders the form;
   POST valid → 303 to detail + row exists; missing name/slug/type → 422;
   duplicate slug → 422 `UniqueViolationError`; `valid_from > valid_until` → 422).
   GREEN: `jurisdiction_new_form` + `jurisdiction_create` in `jurisdictions.py`,
   `form.html`, type dropdown; add "+ Add jurisdiction" to `list.html`.
2. **Inline curatorial edits** — RED: name / notes / valid_from / valid_until /
   slug / type each round-trip (read partial → edit form → POST → updated read
   partial); slug rename collision → 422; slug edit shows the public-`/resolve`
   caveat. GREEN: inline routes + partials mirroring the orgs inline pattern.
3. **Archive / unarchive / delete** — RED: archive sets `archived_at` (409 if
   already archived); unarchive clears (409 if not archived); delete requires
   archived (409 otherwise) and 409s when referenced by a role/relationship/
   affiliation (`ForeignKeyViolationError`); flash on redirect. GREEN: the three
   routes + `_FLASH_MESSAGES` + `resolve_query_flash`, mirroring orgs.
4. **Attachment CRUD via factories** — RED per entity: contacts / links /
   identifiers add/edit/delete. GREEN: `jurisdictions_contacts.py` /
   `_links.py` / `_identifiers.py` (factory wiring) + jurisdiction row/form
   partials; register in `router.py`.
5. **Addresses CRUD** — RED: add/edit/delete an address (normalizer-pinned via
   `local_address_normalizer`). GREEN: `jurisdictions_addresses.py` hand-built,
   mirroring `orgs_addresses.py` + `_addresses_shared` helpers + partials.
6. **Wire interactive detail** — replace the Phase 1 read-only panels in
   `detail.html` with the CRUD partials: "+ Add" buttons (add-row-guard trio:
   `data-new-row-id` / `hx-sync` / `powerMap:newRowClosed` per STYLE.md §32),
   inline edit/delete rows. Verify the add-row-guard template inventory test
   still passes.
7. **Docs + full verification** — update `docs/STYLE.md §32` (jurisdictions now
   full-CRUD, not read-only). Run `ruff` + full pytest suite; dev-server smoke
   (create → edit → add contact → archive → delete round trip on port 8001).

## Open questions / risks

- **Header-sync JS** — plan defers `org-detail.js`-style in-place heading update
  after a name edit (name reflects on next render). Confirm that's acceptable, or
  add a small `jurisdiction-detail.js`.
- **slug / type editability** — Phase-1 design approved "editable with a caveat."
  Confirm both stay editable inline (slug rename changes the public `/resolve`
  key; type change re-buckets the entity). Easy to lock either post-create if
  preferred.
- **Add-row-guard JS coverage** — `test_add_row_guard_templates.py` enforces a
  form-row partial inventory; new jurisdiction form-row partials must opt into
  the guard or the inventory test fails (intentional gate — budget for it).
- **Scope**: this is the largest phase (create + 5 edit surfaces + 4 attachment
  CRUD sets + detail rewiring). If it feels heavy in one PR, it can split at the
  attachment boundary (steps 1–3 curatorial, 4–6 attachments) — flag if you want
  that.
