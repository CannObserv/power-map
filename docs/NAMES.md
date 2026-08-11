# power-map — Person & Organization Names

Name storage, the canonical/display pointer, visibility rules for deadnames and
legal-only names, structured name parts, readings, and the BCP 47 / ISO 15924
lookup tables.

---

## Person names — i18n & cultural awareness


Hybrid model (issue #121): `person_names.name` is the canonical UTF-8 display string; per-name-row metadata (`locale`, `script`, `sort_as`, `visibility`, `reading_of_id`) lives on `person_names`; structured parts live in the `person_name_parts` sidecar (1:0..1, keyed on `person_names.id`).

### Storage rules

- Store user input verbatim. **Never** lowercase, title-case, ASCII-fold, or strip diacritics on input — names like "McNamara", "van der Waals", or "ffrench" rely on specific casing; Vietnamese names rely on diacritics.
- `name` is the authoritative free string. Structured parts in `person_name_parts` are **never auto-decomposed** — populated only when an upstream source supplies pre-parsed structure (e.g., via the observation endpoint's `names[].parts` field) or when a human confirms a suggestion — the "David Lloyd George" ambiguity is unresolvable without cultural context. **Never auto-write parts to the database** without human confirmation or upstream pre-parsed data. Assisted *suggestion* of parts is allowed via `src.core.normalizers.person_name.suggest_parts(...)` — used by triage/backfill scripts (CSV-mediated review) and, optionally, the admin name editor (pre-fill the form for review). The decomposer never persists; only the existing `upsert_or_delete_parts` path does.
- Sort with Postgres ICU collations (e.g. `ORDER BY name COLLATE "und-x-icu"`), or by `sort_as` when present. Do not use `LOWER(name)` for sorting.
- New rows default to `visibility='public'`. The `trg_deadname_visibility` trigger downgrades any `name_type='deadname'` row from `'public'` to `'legal_only'` automatically; an explicit `'hidden'` is preserved.

### Visibility rule (single, project-wide)

A `person_names` row with `visibility ∈ {'legal_only', 'hidden'}` is excluded from:

- `v_person_display_names`
- All public API responses
- All admin search results, list pages, autocomplete, typeahead
- All duplicate-detection candidate sets and ingestion auto-match queries
- All flash messages and activity logs

It surfaces **only** on the person-detail admin page, behind an explicit "Show legal/historical names" disclosure toggle (default collapsed).

Enforcement layers:

- `v_person_display_names` filters by `visibility='public'` — use the view for all display.
- For raw `FROM person_names` / `JOIN person_names` queries, AND-append `visible_names_filter()` from `src.core.db` (or the literal `visibility = 'public'`).
- `tests/core/test_visible_names_filter.py::test_no_unguarded_person_names_queries` greps for direct access outside `ALLOWED_DIRECT_ACCESS`. New direct-access call sites must either filter visibility inline or be added to the allow-list with a `# visibility-allowlist (issue #121): <reason>` comment.

### `name_type` values

| Value | Meaning |
|---|---|
| `legal` | Government-recognized legal name |
| `preferred` | What the person asks to be called publicly |
| `alias` | Alternate identifier (pen name, handle) |
| `former` | Previous name (marriage change, divorce, voluntary change) |
| `initials` | Initialism (`JFK`, `MLK`) |
| `maiden` | Birth surname |
| `religious` | Religious / monastic name |
| `stage` | Performer / artist name |
| `deadname` | Pre-transition or pre-disclosure name; auto-`legal_only` |
| `reading` | Phonetic reading of another row (e.g. furigana) — link via `reading_of_id` |
| `romanization` | Latin-script rendering of another row (pinyin, romaji) — link via `reading_of_id` |
| `mrz` | ICAO 9303 Machine-Readable Zone form — link via `reading_of_id` |
| `variant` | Alt-spelling / nickname of an existing name on the same person (e.g. `Jodi`/`Jody`, `Kip`/`Kristopher`) — see below |

### `variant` vs neighbouring types

| | `variant` | `alias` | `preferred` | `reading`/`romanization`/`mrz` |
|---|---|---|---|---|
| Same identity? | yes | usually no (pen name, handle) | yes | yes |
| Linked via `reading_of_id`? | no | no | no | yes |
| Typical case | `Jody`/`Jodi` (uncertain spelling), `Kip`/`Kristopher` (short form) | "Mark Twain" for Samuel Clemens | What they go by | Phonetic / latin-script / passport form |

A `variant` row sits next to its `legal` row on the same person; both share `person_id`. `is_canonical=FALSE` on the variant; the legal row stays canonical. Use `variant` (not `alias`) when collation against the canonical name matters — e.g. to surface `Jody`-typed search input alongside `Jodi`-keyed records without conflating with truly separate identities.

### MRZ derivation

When generating an MRZ row from a Latin-script visual `legal` row:

| Transformation | Example |
|---|---|
| Uppercase all letters | `José` → `JOSE` |
| Strip diacritics (NFKD + ASCII-only) | `García` → `GARCIA` |
| Replace hyphens with single space | `García-López` → `GARCIA LOPEZ` |
| Drop apostrophes | `O'Brien` → `OBRIEN` |
| Use `<` as filler / separator | `GARCIA<LOPEZ<<JOSE` |

No automatic generation pipeline exists in Phase 1 — populate manually or via a future ingestion integration.

### Structured parts (`person_name_parts` sidecar)

Parts live in `person_name_parts`, keyed on `person_name_id` (1:0..1 with `person_names`). Each `person_names` row optionally has its *own* parts row — the Hant `legal` and Latn `romanization` of one person each carry distinct decompositions, not a shared set. ON DELETE CASCADE — when a `person_names` row is deleted its parts row is removed too.

Columns: `given_names TEXT[]`, `family_names TEXT[]`, `additional_names TEXT[]`, `honorific_prefix`, `honorific_suffix`, `primary_identifier`. Arrays are ordered. `primary_identifier` indicates which array drives formal address and primary sort:

- `'family'` — Western, Sinitic, Hungarian (last-name address); sort by `family_names[1]`
- `'given'` — Icelandic, mononymous fallback; sort by `given_names[1]`
- `'patronymic'` — Arabic chain, Russian; address by `given_names[1]`
- `'mononym'` — single-name people (Cher, Prince); the single token is in `name`

A `person_names` row with no corresponding `person_name_parts` row is fully valid — the free `name` string remains authoritative.

The admin UI surfaces this section as **Details** (issue #127); the DB / route names retain `parts` / `person_name_parts`.

### Canonical name = the display pointer (issue #308)

`person_names.is_canonical` marks **the one name PM displays for a person**. Two constraints carry that meaning:

| Constraint | Guarantees |
|---|---|
| `uq_person_canonical_name` — `UNIQUE (person_id) WHERE is_canonical` | at most one canonical row per person |
| `chk_person_canonical_is_public` — `CHECK (NOT is_canonical OR visibility = 'public')` | the canonical row is always displayable |

This mirrors `uq_org_canonical_name`, which was itself narrowed from `(organization_id, name_type)` to `(organization_id)`. Person and org names now use the same model.

**Why it was re-keyed.** The previous key was `(person_id, name_type, COALESCE(locale,''), COALESCE(script,''))`, so one person could hold several canonical rows at once — a `legal` and a `preferred`, or a Latn and a Jpan `legal`. Consequences, all now gone:

- `v_person_display_names` had to disambiguate with `DISTINCT ON` plus a 13-entry `name_type` priority ladder, and returned duplicate rows per person whenever it didn't.
- A canonical row in one slot could block promotion in another. In particular a curated `legal_only` name (invisible to the view) could occupy a slot, leaving the person rendering blank with no way for the observation path to repair it.
- `is_canonical=TRUE` did not imply "this person displays" — a `deadname` row came back canonical but `legal_only`, because `trg_deadname_visibility` rewrites visibility *after* `is_canonical` is computed.

Nothing consumed the per-family meaning: every read is `ORDER BY is_canonical DESC` (show the main name first) or a wholesale demote on merge, and the admin promote path has always demoted person-wide. At the time of the change production held zero people with more than one canonical row and zero non-public canonical rows.

**Consequences for writers.** A `deadname` can never be canonical — `NEVER_CANONICAL_NAME_TYPES` in `src.core.observation` filters client hints for it, so an observation asserting one is ignored rather than failing the whole request on a `CheckViolationError`. Setting a new canonical in admin must demote the current one in the same transaction (`_names_shared` already does).

**Validate against the visibility that will land, not the one submitted.** `trg_deadname_visibility` rewrites a `deadname` row to `legal_only` *before* the write, so `name_type='deadname'` + `visibility='public'` + `is_canonical` passes any check that only inspects the submitted value and then violates `chk_person_canonical_is_public`. Admin's `_validate_canonical_visibility` therefore checks **`name_type` as well as `visibility`**, and callers pass the *effective* visibility — the stored value when a form omits the field, since `_update_name` leaves the column untouched rather than resetting it. Both name routes also map the constraint to a flash as a backstop.

**Every path that can strand a person without a display pointer must repair it.** Three call the same helper, `heal_person_canonical`: the observation path (including observations carrying no names), name deletion (`_maybe_promote_sole_name`, now a thin delegation), and merge (`merge_person_into`, which demotes the loser's canonical and so must heal the winner). The `#308c` backfill is the fourth repair path but runs its own set-based SQL — it does **not** call the helper; it shares the choice via `name_type_priority_sql()`, so all four still pick the same replacement row. The org name-delete path has its own equivalent heal (`orgs_names.py` — orgs have no visibility or eligibility exclusions, so it is just the ladder plus the guard).

Do not reintroduce a "promote only when exactly one name remains" shortcut — on either the person or the org side. That was the old delete-path rule, and it left a multi-name person (or org) blank whenever their canonical was deleted — the remaining names were perfectly displayable, nothing repaired them, and only a later observation happened to fix it.

The heal is best-effort and never aborts its caller: it runs in a savepoint and swallows `PostgresError`, because failing an observation over a cosmetic display-name repair would discard its links, addresses, role assignments and events. `UniqueViolationError` (a lost race) logs at debug; **everything else logs at WARNING** — `configure_logging` defaults to INFO, so a debug-only line would hide a typo'd statement or a revoked grant while callers still report success.

**Dedup person names on identity, not on the string.** A `legal` row and an `mrz` rendering can carry identical text while being different claims, as can a Latn and a Jpan row; matching on `name` alone silently destroys the second. `write_names` uses the full `(name, name_type, locale, script)` key.

`merge_person_into` uses a deliberately looser variant: text + **visibility** + locale + script must match, and then **either** the `name_type`s are equal **or** both are ordinary display types. Consolidating two records that were each split into `legal` + `variant` would otherwise leave the winner holding the same string as both — redundant rather than two claims. `NO_AUTO_CANONICAL_NAME_TYPES` (`mrz`, `reading`, `romanization`, `deadname`) are never interchangeable: identical text in one of those is a machine-readable rendering, a distinct claim from a display name.

`visibility` is compared on **both** branches, and that is load-bearing twice over. Without it a `hidden` winner row absorbed a `public` loser row carrying the same text; since the loser's canonical is demoted immediately beforehand, that deleted the only promotable name and left the merged person blank — defeating the heal that runs a few lines later. It also silently destroyed `legal_only` claims, breaking the #121 guarantee that the winner inherits the loser's restricted names.

### Name families are edges, not shared slots (`reading_of_id`)

Re-keying the canonical index costs nothing, because **PM does not model "the same name written differently" by grouping rows that share `(name_type, locale, script)`** — it models it with an explicit FK.

`person_names.reading_of_id` points a `reading` (furigana) or `romanization` (pinyin, romaji) row at the name row it renders, `ON DELETE CASCADE` — a reading cannot outlive its source. So a Japanese legal name and its romaji rendering are:

- **two rows**, of **two different `name_type`s** (`legal` and `romanization`),
- **joined by an FK edge**, not by an implied grouping,
- of which **exactly one is the display pointer**.

That was already true before #308 and is unchanged by it. The old per-family canonical key was not what expressed the relationship; `reading_of_id` was, and still is. What the old key actually permitted was a *second* `legal` row differing only by locale/script also being canonical — which is the same content in two scripts, i.e. precisely the case `reading_of_id` + `name_type='romanization'` is designed for. Model it that way.

Admin support for the edge: `people_reading_target_search.py` powers the "reading of" picker; `_name_row.html` / `_name_form_row.html` surface the parent name.

**Merge preserves families through dedup (#309).** Because the edge is `ON DELETE CASCADE`, a naive merge that deletes the loser's parent row as a duplicate of a winner row would take the reading with it — even though the reading is not a duplicate of anything the winner holds. `merge_person_into` therefore re-points the loser's `reading_of_id` children at the winner's surviving equivalent **before** the dedup DELETE, keyed on the same name-identity match the DELETE uses (both share `_NAME_IDENTITY_MATCH_SQL` so they can't drift). Scope of #309: the automatic dedup DELETE only.

**Curated drops keep parents of kept children (#323).** The curated `keep_name_ids` drop (the preview-modal keep/drop selection, #255) shares the same `ON DELETE CASCADE` exposure, but only asymmetrically. Dropping a reading whose parent is kept, or dropping both, are deliberate admin choices and stay as-is. The one case that is *not* an informed choice is a **kept** reading whose parent is left unchecked — dropping the parent would silently destroy the explicitly-kept child. So the curated DELETE extends its keep-set to the parents of any kept `reading_of_id` child — keyed on the FK's presence, not `name_type`, so it covers `reading`, `romanization` **and** `mrz` alike; the dedup DELETE below may still collapse that kept parent into a winner equivalent, at which point the #309 re-point moves the reading. The preview modal surfaces the linkage on both actionable rows — a `(reading of "…")` note on each child and a `(a reading points at this)` note on each parent — both relational, so they stay accurate regardless of which boxes the admin toggles — so the dependency is visible before submission.

### Canonical auto-promotion on observation (#308)

`write_names` guarantees that a person with an eligible name ends up displayable, symmetric with the long-standing org behaviour:

- **Client hint present** (`is_canonical=true` on some name) — that name claims the slot, guarded by `NOT EXISTS (… person_id AND is_canonical)`; never displaces an existing canonical. A hint on a `deadname` is ignored (`NEVER_CANONICAL_NAME_TYPES`) rather than raising `CheckViolationError` and failing the whole observation.
- **No hint** — PM auto-promotes exactly one name per write, picked by `_PERSON_NAME_TYPE_PRIORITY` (`preferred` > `legal` > `alias` > …). `NO_AUTO_CANONICAL_NAME_TYPES` (`deadname`, `mrz`, `romanization`, `reading`) is never auto-promoted.

Eligibility is by **identity, not name string**: the promotion target is an index into the payload, and the append-dedup key is `(name, name_type, locale, script)`. Matching on the bare string let an `mrz` row claim the display slot ahead of a `legal` one purely by list order, and silently discarded the second claim.

Clients are not required to assert `is_canonical` — omitting it is the correct conservative default when the client can't tell whether it is creating a new person or matching an existing one. The `NOT EXISTS` guard makes displacement impossible either way.

**Heal on re-observation.** Auto-promotion fires only on *newly inserted* rows, and `write_names` skips names that already exist — so a person already canonical-less would never recover, since the steady-state client re-sends the same names every sync. `write_names` therefore ends the person branch with `_heal_person_canonical`, promoting the highest-priority eligible existing name whenever the person has no canonical. It also runs for observations carrying **no names at all**, so any observation touching a blank person repairs it, and is skipped when an insert in the same call already claimed the slot.

The heal is a read-only probe plus a guarded `UPDATE`. The probe cannot violate a constraint, so the steady-state case (already-displaying person, unchanged names) stays at one round trip with no savepoint. The `UPDATE` takes a savepoint: a concurrent commit between the two statements can still collide on `uq_person_canonical_name`, and without recovery that error propagates out of `write_names` and aborts the whole observation, which the public route reports as `db_constraint_violation` — discarding links, addresses, role assignments and events over a cosmetic display-name repair.

`scripts/backfill_person_canonical_names.py` repairs people who predate this, selecting with the **same ladder** so both repair paths choose the same row (`test_backfill_matches_heal_choice`).

### BCP 47 / ISO 15924 lookup tables (issue #123, Phase 2-prep)

`person_names.locale` and `person_names.script` are FK-constrained to `bcp47_locales(code)` and `iso15924_scripts(code)` respectively. The lookup tables are seeded by `scripts/seed_locales_scripts.py` from the `langcodes` and `pycountry` libraries, which live in the `seed` dependency group only — request-path code never imports them.

Validation layering:

| Layer | What it does | Source of truth |
|---|---|---|
| Admin form (Pydantic) | Strips whitespace, rejects empty strings | UI ergonomics |
| Database FK | Rejects unregistered codes (`'xx-XX'`, `'Xxxx'`) | Authoritative |
| Seed script (`langcodes` + `pycountry`) | Populates the lookup tables; runs once per env | Registry mirror |

No curated default-set is maintained — the typeahead's empty state shows a placeholder and narrows the full table by user keystrokes. The human-readable column differs by table: locales use `display_name`, scripts use `name`. So:

- locales: `code ILIKE '%q%' OR display_name ILIKE '%q%'`
- scripts: `code ILIKE '%q%' OR name ILIKE '%q%'`

pg_trgm GIN indexes are present on both columns of both tables (Postgres' planner may still pick Seq Scan at current row counts; the index is load-bearing as the data grows). Re-seed at any time to pick up registry updates: `uv run --group seed scripts/seed_locales_scripts.py --execute` (dry run without `--execute`, #402).

ON UPDATE CASCADE is set on both FKs, so a registry-driven `code` rename propagates to existing person_names rows. ON DELETE NO ACTION (default) blocks lookup-row deletion when referenced — the registry doesn't shrink, so this is correct.

A symmetric FK on `bcp47_locales.script → iso15924_scripts(code)` (same `ON UPDATE CASCADE`) keeps locale rows consistent with the script registry. Phase 2b code may join `bcp47_locales.script → iso15924_scripts.code` to enrich locales with their script's `name`/`numeric_code` without defensive existence checks.

---

The two invariants below keep an org from losing its display identity when name or
acronym rows are deleted. They are enforced in admin route handlers, not by the DB.

## Auto-promote invariant


Every **delete** route on `organization_names` must call `_maybe_promote_sole_name(org_id, db)` inside its transaction (from `src.api.admin.orgs_names`) — promotes the sole remaining non-canonical name to canonical, keeping `v_org_display_names.display_name` non-NULL. Edit routes do not need this — the canonical edit guard prevents completing when it would leave zero canonical names.

Equivalent for acronyms: `_maybe_promote_sole_acronym(org_id, db)` (from `src.api.admin.orgs_acronyms`) — call inside the transaction of every **delete** route on `organization_acronyms`.

---

## Last-identity guard


`name_delete` blocks when the org has exactly one name and no canonical acronym; `acronym_delete` blocks symmetrically.

- HTMX: return HTTP 200 with `flash_trigger("error", ...)` and empty body
- Non-HTMX: raise `HTTPException(409)`
