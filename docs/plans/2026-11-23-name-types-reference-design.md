# Name types: one reference per type, and a `misspelling` type (#857)

**Status:** approved 2026-11-23 as recommended; Observo owns the row's reclassification (decision 8). Part of epic #818.
Operator decision 2026-11-22 (issue comment 7084754512): option B, a separate
`misspelling` type. Verified against `origin/main` @ `fb002950`.

## Goal

1. A name a person never uses, kept only so search finds them (a common
   misspelling), gets its own type: `misspelling`. It is cleanly separable from a
   `variant` (a form the person legitimately uses or is known by).
2. Every person and org name type has one reference entry: what it means, when
   to use it, when not to, an example, and how power-map treats it. Code, schema,
   docs and the served catalog are held together by tests.

No other type changes behaviour.

## Behaviour today is spread over six sets, not five

The issue counts five. A sixth sits in the parts normalizer:

| Set | Where | Answers |
|---|---|---|
| `NO_AUTO_CANONICAL_NAME_TYPES` | `src/core/observation.py:40` | may it be promoted unasked (insert, heal)? |
| `NEVER_CANONICAL_NAME_TYPES` | `observation.py:46` | is a canonical hint ignored / refused? |
| `_PERSON_NAME_TYPE_PRIORITY` | `observation.py:52` | display ladder rank (8 types) |
| `_MERGE_DISTINCT_NAME_TYPES` | `src/core/person_merge.py:32` | does merge collapse it only against its own type? |
| `trg_deadname_visibility` | `schema.sql:1533` | landing visibility (`deadname` → `legal_only`) |
| `NON_DECOMPOSABLE_TYPES` | `src/core/normalizers/person_name.py:45` | is a parts suggestion / editor offered? |

Plus the `reading_of_id` convention (`reading`, `romanization`, `mrz`), which
is documentation only — no DB rule requires the link.

A seventh ranking exists but is dormant: the #308 re-key `DO` block
(`schema.sql:1288-1298`) ranks types to demote surplus canonicals, and runs only
while `uq_person_canonical_name` is not yet keyed on `(person_id)`. It is a
frozen migration; `misspelling` falls to its `ELSE 99`. Left alone.

## Decisions

### 1. One registry in code

New module `src/core/name_types.py`. A frozen dataclass per type:

```
NameTypeSpec(
    slug, label,
    meaning, use_when, not_for, example,       # the reference prose
    display_rank: int | None,                  # ladder; None = never on it
    auto_promote: bool,                        # outside NO_AUTO_CANONICAL
    never_canonical: bool,                     # hint ignored; admin refuses
    never_canonical_reason: str | None,        # admin refusal wording, per type
    landing_visibility: "public"|"legal_only", # what the trigger forces
    renders_row: bool,                         # links via reading_of_id
    merge_distinct: bool,
    decomposable: bool,                        # parts suggestion offered
    presentable: bool,                         # a consumer may show it as a name
)                                              #   the person goes by
```

`PERSON_NAME_TYPE_SPECS` and `ORG_NAME_TYPE_SPECS` are ordered tuples. The six
sets above become derivations with their current names, so no call site
changes. `src/core/types.py` keeps the `PersonNameType` Literal and
`PERSON_NAME_TYPES` tuple (every importer reads those); a test holds registry
order == tuple order == Literal.

Parity tests:
- registry ↔ tuple ↔ Literal ↔ both schema `CHECK`s ↔ the observation enum
  (extends `tests/core/test_types.py`);
- each derived set equals its pre-change value plus `misspelling` where decided
  below (a regression guard: "no other type changes behaviour");
- integration: insert each type `public`, read back `visibility` — equals
  `landing_visibility` (holds the trigger to the registry);
- the docs table (decision 3) lists every slug, and its flag columns match.

### 2. `misspelling`'s behaviour

| Flag | Value | Why |
|---|---|---|
| landing visibility | `public` | search must find it; `people.search_tsv` indexes public names |
| display rank | none | never displayed |
| auto-promote | no | in `NO_AUTO_CANONICAL_NAME_TYPES`; also keeps `heal_person_canonical` (`observation.py:195`) from promoting one when a canonical is deleted |
| never canonical | yes | in `NEVER_CANONICAL_NAME_TYPES`: a hint is ignored on the wire, as for `deadname`; the admin form refuses |
| `reading_of_id` | no | it is a spelling, not a rendering of a row |
| merge-distinct | **yes** | see below |
| decomposable | no | added to `NON_DECOMPOSABLE_TYPES`: no parts suggestion (the editor and the wire's `parts` stay open, as for every type) |
| presentable | no | consumers use it as a search key only, never as "also known as" |

**Merge-distinct, confirmed against `_NAME_IDENTITY_MATCH_SQL`.** Two
non-distinct types sharing `(name, visibility, locale, script)` collapse, and the
**loser's** row is the one deleted. If `misspelling` were a display-class type, a
winner holding `misspelling` "X" would delete the loser's `legal` "X": the real
name lost, the error kept. Distinct means it only collapses against another
`misspelling`, so both claims survive. `variant` stays non-distinct (a display
type), unchanged.

**DB backstop: yes.** `NEVER_CANONICAL` is enforced only in code (writer,
admin). `deadname` has an indirect DB stop (trigger → `legal_only` →
`chk_person_canonical_is_public`); a public `misspelling` has none, so a merge
carry or a hand `UPDATE` could make one canonical. Add:

```sql
CONSTRAINT chk_person_canonical_name_type
    CHECK (NOT is_canonical OR name_type NOT IN ('deadname','misspelling'))
```

with an idempotent reconciliation `DO` block (add when absent; demote any
violating canonical first, `RAISE NOTICE` the count — expected zero), placed
beside `chk_person_canonical_is_public` (`schema.sql:1327`). `person_names` has
no `updated_at`, so no `set_updated_at()` trigger to order against; the
type-`CHECK` edit (inline `:370` + the drop/re-add block `:1233`) already sits
in its own reconciling block. Listing `deadname` is redundant but makes the parity
test a plain equality with `NEVER_CANONICAL_NAME_TYPES`.

**Admin refusal wording per type.** `_validate_canonical_visibility`
(`src/api/admin/_names_shared.py:290`) says a deadname "is recorded privately
and is never displayed" — false for a misspelling. It reads
`never_canonical_reason` from the registry instead. Misspelling: "it is a
misspelling kept so search finds this person, and is never displayed". Same
422/flash path; no new flash key.

### 3. The human reference: an annex doc

Measured: `docs/NAMES.md` ≈ 8.8k / 10k tokens; a 16-entry reference with
behaviour columns needs ~1.5–2k. `AGENTS.md` is at 5,994 / 6,000, so a new doc
cannot take its own index line (`docs/CONTEXT.md` Rule 3, #521).

→ New **`docs/NAME_TYPES.md`** as an **annex** of `NAMES.md` (Rule 3's
exception: no task needs it except by way of names). `NAMES.md` § `name_type`
values and § `variant` vs neighbouring types **move** there, leaving one routing
line, so `NAMES.md` shrinks. `API_PEOPLE.md:43` repoints to it. Holds:

- one entry per person type (14) and org type (3): meaning, use when, not for,
  example;
- one behaviour matrix, generated-shape but hand-written, checked by the parity
  test;
- the misspelling rule and the boundary cases below.

`OBSERVATIONS.md` and `PUBLIC_API.md` are not touched. If the 2026-11-24 cadence
run touches `NAMES.md` or `API_PEOPLE.md`, rebase on it.

### 4. `variant` narrowed; the boundary cases

- **`variant`:** an alternative form the person legitimately uses or is known
  by — a short form, nickname, or a second spelling the person's own usage
  attests.
- **`misspelling`:** a spelling not attested by the person's own usage or an
  authoritative record — a transcript's guess, a common third-party typo. Kept
  only to be found.

| Case | Type | Why |
|---|---|---|
| `Kip` for Kristopher | `variant` | short form the person goes by |
| `Becky` for Rebecca (Observo's declined case) | `variant` (`preferred` if they ask to be called it) | a nickname, not another spelling — never `misspelling`. Observo declined it under the old "alt-spelling" wording; the narrowed one covers it |
| `Jody`/`Jodi`, person's own documents use both | `variant` | attested by the person |
| `Jodi` from a transcript, record says `Jody` | `misspelling` | not attested |
| `Caitlin Ryan` from a spoken self-introduction, record `Kaitlyn Ryan` (Observo's live row) | `misspelling` | audio cannot confirm a spelling |
| "Mark Twain" for Samuel Clemens | `alias` | different name, not a spelling |

The "uncertain spelling" example leaves `variant`'s definition.

### 5. Reads: return misspellings, typed

`GET /people/{id}` already returns every visible name with `name_type`
(`src/api/public/people.py:358`). Keep returning misspellings there, typed; the
reference says consumers never display one (`presentable: false`). Leaving them
out would hide data a curator or the cli may need, and break the symmetry with
`variant`.

**Read schemas stay `str`.** `PersonName.name_type` / `OrgName.name_type`
(`schemas.py:216`, `:131`) are not retyped as an enum: the generated client
parses enums with `Enum(value)`, which raises on an unknown value, so every
future type would break older clients reading a person. The catalog (decision 6)
serves the vocabulary instead. The catalog's own `landing_visibility` is a string for the
same reason (found at the first client regen).

Search needs no change: `misspelling` is public, so `search_tsv` indexes it and
the result carries the display name.

### 6. Served catalog, not docs only

**Recommended:** `GET /api/v1/person-name-types` and
`GET /api/v1/organization-name-types`, one module
(`src/api/public/name_types.py`), same envelope and auth as
`/entity-identifier-types` (`{"data": [...]}`, `require_api_key`,
`catalog_validator` ETag over the serialized registry). Backed by the registry —
no table. Each item: `slug`, `label`, `meaning`, `use_when`, `not_for`,
`example`, and the behaviour flags.

**Docs-only alternative, weighed:** steps 1–3 without the route. Cheaper (no
route, smaller spec diff). But the request enum already serves the *values*
(OpenAPI); what connecting projects lack is the *meaning and behaviour*, and
Observo's review step now has to choose `variant` vs `misspelling` in code. A
doc they can't read at runtime means each project hard-codes its own copy — the
drift this issue exists to stop. The four sibling vocabularies all have a route.
The cost is small because the registry exists either way.

### 7. Wire change

- `ObservationPersonName.name_type` gains `misspelling` (request side: no
  client parse hazard). Derived from the Literal so it cannot drift.
- Two catalog routes (+ `operation_id`s, response models).
- Minor bump 0.75.0 → **0.76.0**, `pyproject.toml` + `package.json` together,
  then `uv run python -m scripts.regenerate_client`. `generated/` never
  hand-edited.
- Org name types get no `misspelling`: no consumer has asked, orgs search by
  acronym and `dba` already, and no producer sends org transcripts.

### 8. The existing row: Observo reclassifies it

Name identity is `(name, name_type, locale, script)`, so a curator retype in
admin would strand Observo's later `op: "retract"` of its `variant`, and a
re-send of the `variant` would create a second row. Observo owns the change.

**As it happened:** Observo retracted the `variant` "Caitlin Ryan" at
2026-11-23 03:11:05Z (disposition `retracted`, confirmed read-only from
`api_request_log` and `person_names`), before `misspelling` existed. Its
citation went with the row. After 0.76.0 deploys, Observo re-sends it as one
`misspelling` item with the citation; until then "Caitlin" does not find the
person. (Had the row still been live, the recipe was one observation carrying
the retract and the `misspelling` together.)

### 9. Observo's contract

#845 promised "always `variant`". The relay to CannObserv/observo#874, drafted
on #857 and posted only with operator consent, says: the new type and version;
the narrowed `variant`; the boundary table (decision 4), including that `Becky`
→ `variant` is in bounds; the catalog route; the reclassification recipe
(decision 8): re-send as `misspelling`. Dispositions and retract guards are unchanged.

### 10. Surfaces that pick it up unchanged

Admin name-type dropdown (`people_names.py:57`), Settings › Person Name Types
(`settings.py:46`), names panel and Activity › Key-written names (#847) show the
type as text. No visual change: the type label is enough; revisit if curators
ask. Producers unchanged: usa-wa emits `legal` only, the CSV importer
`legal`/`former`.

**Duplicate detection** (`src/api/admin/people_dups.py:15-22`) compares every
non-hidden name of any type by trigram > 0.85, so a misspelling can raise a
candidate pair against a different person whose real name is that spelling.
Same as `variant` today; the dismissal flow handles it. Not changed here.

## Out of scope

- Fuzzy or phonetic search (trigram, soundex).
- Changing any existing type's behaviour (guarded by the derived-set test).
- Retyping read-side `name_type` as an enum.
- An org `misspelling`.
- A visual treatment for misspelling rows in admin.
- The dormant #308 re-key ladder.

## Host steps (after merge)

Schema applies on restart: `bash scripts/pre-restart-check.sh && sudo systemctl
restart power-map`. Then: `\d person_names` shows `misspelling` in the `CHECK`
and `chk_person_canonical_name_type`; `GET /api/v1/person-name-types` lists 14.
