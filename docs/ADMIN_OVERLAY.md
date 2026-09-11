# Curation overlay — the admin side (#498)

A **pin** is a curator's decision that PM's value for a producer-owned field
wins over every later snapshot. Pins live in `curation_overlay`
(`docs/SCHEMA.md` § Curation overlay); the mapping models join the active ones
as `COALESCE(overlay, mapped)`, so the applier needs no per-row gate and a
curator's correction is never silently reverted (design:
`docs/plans/2026-09-11-curation-overlay-design.md`).

## The five slots

A slot is a producer-owned field on an entity in the producer's row scope — a
`live` or `merged` `producer_crosswalk` row. Outside that scope nothing here
applies: editing is direct curation, as before.

| Overlay field | The slot's value | Admin edit sites |
|---|---|---|
| `person.name` | canonical legal `person_names` row (else the earliest legal row) | names factory (`_names_shared.py`) |
| `organization.legal_name` | canonical legal `organization_names` row (else the earliest) | names factory |
| `organization.acronym` | canonical `organization_acronyms` row (else the earliest) | `orgs_acronyms.py` |
| `organization.parent_id` | `organizations.parent_id` | `orgs.py`: inline parent, add child, remove child (the *child* is tracked) |
| `organization.dissolved_year` | year of the unarchived `dissolved` event | `_events_shared.py`: create, edit, archive, unarchive |

The registry is `src/api/admin/overlay_slots.py` (`SLOTS`). It must name the
same pairs as `manifest.yml`'s `overlay:` keys and the dbt test
`overlay_field_unmapped.sql`; `tests/core/ingestion/mapping/test_overlay.py`
fails when the three drift. **Adding a slot** means all three, plus the mart
that joins it. The admin cannot read the manifest directly, because the
mapping package imports dbt and the service does not install it
(`tests/test_api_imports_without_mapping.py` guards this).

## The pin rule

**A write that changes a slot's value pins the value after the edit.** Each
edit site wraps its transaction in `tracked(db, entity_type, entity_id,
user_id=…, fields=…)`, which reads the slots before and after the write and
calls `curation_overlay.pin_changed`:

- the value moved → pin it; nothing of the slot left (the legal name deleted,
  the parent cleared, the dissolved event archived) → pin NULL;
- the value unchanged (a locale edit, a canonical toggle between equal
  values) → nothing;
- the edit raised → nothing, since the pin shares the edit's transaction.

Pinning the value *as it stands after the edit* guarantees the applier finds
it present: a noop, never a re-insert. Routes that can pin depend on
`provision_app_user`, because `created_by` is an `app_users` foreign key. A pin
is announced in the flash: the HTMX body gains a sentence, the fallback key
becomes `saved_pinned` / `removed_pinned`, and `refreshOverlay` fires.

**Unpin archives.** The row stays as history. A new value archives the old
pin and inserts a fresh one, so every decision keeps its author and time. On a
PM merge, `rehome_curation_overlay` moves the loser's pins across and archives
any the survivor already holds (`docs/MERGE.md`).

## Surfaces

- **The slot line** — `GET /admin/_overlay/{type}/{id}/{field}/`, a
  self-loading fragment in the dup-badge pattern (`hx-trigger="load,
  refreshOverlay from:body"`) under each slot's panel header. Unpinned:
  *Maintained by usa-wa* and a **Pin** that keeps the current value. Pinned: a
  `badge--pinned`, the value as a curator reads it (a parent's name, *kept
  empty* for NULL), who, when, the note, and an **Unpin**. Empty outside scope.
- **The edit note** — the same route with `?variant=note`, hosted in the name,
  acronym, parent and org event forms: *Saving a change pins your value*.
- **Pin / Unpin** — `POST …/pin/` and `…/unpin/`: HTMX partial plus the
  `with_flash` fallback to the entity page (`?flash=pinned|unpinned`).
- **`/admin/pins/`** — every pin, filtered by the #306 status axis (`active`
  by default, `archived`, `all`) and entity type. Unpin on a row archives *that*
  pin only while it is still live (`unpin_pin`), because a stale row must not
  archive the pin that replaced it.

The label "usa-wa" is `overlay_slots.PRODUCER_LABEL`, in the admin layer and
never in `src/core`. Acceptance, end to end: `tests/scripts/test_curation_overlay_seam.py`.
