---
title: "#595 Address standardization provenance and the re-standardization sweep"
date: 2026-11-12
status: approved (operator, 2026-11-12)
---

# #595 Address standardization provenance and the re-standardization sweep

## Goal

Record, on every `addresses` row, **why** it has (or lacks) a standardized form. Then a sweep can retry only the rows saved while address-validator was down, and it never touches a curator's "Keep my input". This is prevention: today nothing needs repair.

## Prod measurement (2026-11-12, read-only, counts only)

Measured inside a read-only transaction that was rolled back.

- **116 `addresses` rows.** All are linked to organizations, and all were created on 2026-06-15. None has been created or updated since. There are 0 non-US rows, 0 orphan rows, 0 event-place references, and 0 rows shared by two links.
- **`field_confidence` coverage is exact.** There are 116 `address` rows, one per org, and each matches its `addresses` row on `(entity_id, sha256(COALESCE(standardized, raw_input)))`. 0 rows carry a `fallback` marker, because the import predates #589.
- **4 rows have `standardized IS NULL`:**
  - **3 local-parser fallbacks** (`provider=usaddress`, no marker). The neighbouring rows in the same run standardized. Pre-#589, an outage can't be told apart from a rejected input. One of the three has no line 1, city, region or postal code parsed at all.
  - **1 Google `not_found`**: the validator answered, with no standardized form.
- **112 rows are standardized.** Among them:
  - 1 is `usps` / `unavailable`: standardized, but never validated. It is the only re-validation candidate.
  - 5 are Google `not_found` / `invalid` that still carry a standardized form.
- **Duplicates:** 0 groups share an `(entity, type, COALESCE(standardized, raw_input))` key, and 0 entities hold a NULL row next to a standardized row with the same raw input.
- **#600 check:** all 40 DPV `D` rows carry `status=confirmed_bad_secondary`. Both `D` and `S` map to `validation_status='confirmed'`, so the column is right; only `validation_detail.status` is mislabelled. No row has `undetermined`.

So the 2026-09-30 counts still hold.

## Corrections to the coordinator brief

1. **Q6 is not moot.** Two public payloads serve `addresses.standardized`, and an `addresses` UPDATE moves neither one's ETag:
   - The role and assignment details (`RoleAddress`, `AssignmentAddress`) serve `raw_input` and `standardized`. Their ETags key on the parent's `updated_at`.
   - The org and person event lists serve `event_place_address.standardized`. That list ETag keys on `max(entity_events.updated_at)`.
2. **Admin-written rows have `raw_input = NULL`.** The three admin routers never set it. So `write_addresses`' dedup key, `COALESCE(standardized, raw_input)`, is NULL for an admin row saved unstandardized, and an observation can never match it. Prod has 0 admin rows. The sweep must compose its input from the address columns, the way `_maybe_confirm` does. The dedup gap is pre-existing and out of scope here; see "For a follow-up issue" below.

## Decisions (recommendations; the operator decides)

| # | Question | Recommendation |
|---|---|---|
| Q1 | Where and what to record | `addresses.standardization_status` (CHECK, 7 values, NULL = unknown), plus `addresses.standardization_checked_at`. |
| Q2 | Existing NULL rows | Backfill from `field_confidence` in the schema's reconciliation block. Mark the 3 pre-#589 fallbacks `unavailable`, so the first sweep classifies them. |
| Q3 | Store validation status? | **No**, not now. Map `undetermined` → `unconfirmed` in `_STATUS_MAP`. A re-validation sweep needs its own issue, and only if wanted. |
| Q4 | How to trigger | A `scripts/` sweep, run by hand: dry run by default, `--execute` to write. **No timer.** |
| Q5 | Collision merge | A new address-level primitive re-points the links (window-exact, #256), then deletes the emptied row. `orgs_merge` is not reused. |
| Q6 | Change feed | **Yes:** a DB touch trigger on `addresses`, keyed to its served columns. |
| Q7 | Audit | The sweep's dry run is the audit: it exits 3 when an `unavailable` row is older than 3 days. It runs on demand, with no timer until addresses flow again. |

### Q1: Record provenance on `addresses`

**Why `addresses`, not `entity_addresses` or `field_confidence`:**
- The standardized form belongs to the address row.
- Several links can share one row (`write_addresses` reuses a row across validity windows), and event places point at rows directly.
- `field_confidence` doesn't fit. Only the CSV import writes it, it is append-only, it is assessed per field per entity, and it reaches the row only through a hash.

**Columns:**

```sql
standardization_status     TEXT NULL CHECK (standardization_status IN
    ('standardized', 'declined', 'unavailable', 'rejected', 'unsupported', 'no_result', 'local')),
standardization_checked_at TIMESTAMPTZ NULL
```

Both columns are nullable: NULL means unknown, i.e. written before #595. After the backfill, prod has none. The CHECK ships with its idempotent reconciliation `DO` block, placed before the table's `set_updated_at()` trigger. Every writer sets both columns.

**Values**, from one pure function, `standardization_status(result)` in `src/core/normalizers/address.py`, which every writer calls:

| Value | Meaning | Source | Sweep retries? |
|---|---|---|---|
| `standardized` | A standardized form was stored | `result.value["standardized"]`, or the admin's Accept | — |
| `declined` | A curator chose "Keep my input" | admin `mode=save` with a blank `standardized` | **never** |
| `unavailable` | Transient: outage, timeout, 5xx, 429 given up, bad key | `validation_detail["fallback"]` | **yes** (default) |
| `rejected` | 400/422: the validator refused the input | `fallback` | no |
| `unsupported` | 422 `country_not_supported` | `fallback` | opt-in (`--status unsupported`), once a country gains support |
| `no_result` | 200 with no standardized form (e.g. `not_found`) | external answered, no `standardized` | no |
| `local` | Deliberately offline: `local_only` (#402), or no API key | config-less normalizer (no `fallback` marker) | opt-in (`--status local`) |

**Writers:**
- **Admin:** the org, person and jurisdiction create/edit routes.
  - "Keep my input" → `declined`.
  - Accept, and the non-HTMX `ConfirmPersist` → `standardized`.
  - `NothingToConfirm` → the function's value.
  - The #589 `keep_stored` edit path (a label- or date-only edit during an outage) → unchanged.
- **`write_addresses`:** the public observation API.
- **The CSV pipeline:** both `INSERT INTO addresses` sites.

### Q2: Backfill existing rows

An idempotent `DO` block runs after the `ALTER`s, and only `WHERE standardization_status IS NULL`:

1. `standardized IS NOT NULL` → `standardized` (112 rows).
2. Otherwise, match the row's `field_confidence` `address` row: same entity through `entity_addresses`, with `value_hash = encode(sha256(convert_to(raw_input, 'UTF8')), 'hex')`.
   - A `fallback` marker → that marker (0 rows today).
   - `provider = 'usaddress'` with no marker → `unavailable` (3 rows). A pre-#589 fallback is ambiguous, so it is marked retryable. The first sweep then calls the validator once each: a rejected input becomes `rejected`, and a recovered one becomes `standardized`.
   - Any other provider → `no_result` (1 row).
3. No match → left NULL (0 rows today).

`standardization_checked_at` takes the matched `assessed_at`, or else `created_at`.

The block bumps `addresses.updated_at` once per row; nothing serves that column. The Q6 trigger ignores the status columns, so the backfill touches no parent.

**Alternative:** leave NULL as "unknown" and never retry it. That is simpler, but the 3 ambiguous rows would then never be classified.

### Q3: Validation status: don't store it now

Re-validating a standardized form is a different sweep:
- nothing collides;
- the address-validator call differs (`/validate` on an already-standardized form);
- the admin path throws validation away, and only the CSV import records it.

Prod has one candidate. A column with one consumer that doesn't exist yet isn't worth the schema change.

**In the build:**
- Map `undetermined` → `unconfirmed` in `_STATUS_MAP`. A provider answered but made no determination, so `not_attempted` was wrong. No prod row has it.
- Leave `unavailable` → `not_attempted`. `field_confidence`'s CHECK offers nothing closer.

**Constraint for any future re-validation work:** read `dpv_match_code`, not `status` (#600). All 40 prod `D` rows are mislabelled.

### Q4: A manual sweep, no timer

`scripts/restandardize_addresses.py`:
- uses `add_dsn_args` / `resolve_dsn` and echoes its target (#399);
- runs as a dry run by default, and `--execute` writes (#402).

**Selection:**
- `--status`, repeatable. Default `unavailable`; it also accepts `local` and `unsupported`.
- The parser has no choice for `declined`, `rejected`, `no_result` or `standardized`, and a test guards that `declined` can never be selected.
- `--limit` defaults to 100.
- Order: oldest `standardization_checked_at` first.

**Each candidate:**
- **Input:** `raw_input`, or else the columns joined the way `_maybe_confirm` joins them, sent with the row's `country`.
- **Call:** the import-tier normalizer (the raised Retry-After cap, #597).
- **The dry run makes the calls too.** address-validator is read-only, so the dry run can report the real outcome per status, plus the collisions it would merge. Only DB writes wait for `--execute`.
- **Outcomes under `--execute`,** one transaction per address:
  - `standardized` → write it with Accept semantics: the lines, `standardized`, lat/long, `components`, status and `checked_at`. `raw_input` is kept. Then the Q5 merge runs.
  - still `unavailable` → write nothing. *Amended 2026-11-13 (#797): this line said "bump `checked_at` only", which let every `--execute` reset the audit's 3-day clock during an outage. An unchanged status of any kind now writes nothing, so `checked_at` records when the status was decided.*
  - `rejected`, `unsupported` or `no_result` → set the status and `checked_at`.
- **What the sweep writes:** only address-validator output, at the same trust level as `write_addresses`. It writes no `field_confidence` rows.

**Why no timer:**
- The repair set is empty after the first run.
- Nothing has written an address since 2026-06-15.
- A timer costs:
  - a co-status monitor, which is a cross-repo filing (status#48);
  - an `infra/installed.toml` row (#560);
  - the `test_infra_alerting.py` count.
- Revisit when a producer starts sending addresses, or the admin starts writing them. A run inside address-validator's 00–06 UTC maintenance window is harmless: those rows stay `unavailable`.

### Q5: Merge on collision

**The trigger:** row A re-standardizes to S, and the same entity and address type already hold row B whose `COALESCE(standardized, raw_input) = S`. Several Bs → the first by `(created_at, id)`, the same order `write_addresses` reuses.

**For each link `L = (E, T, A, window)`:**
- If `(E, T, B, window)` already exists (the exact `entity_addresses_entity_addr_uniq` key) → delete L. This is a true duplicate.
- Otherwise → `UPDATE L SET address_id = B`. The window is kept exactly. Different windows on B are history, not duplicates (#181/#256); windows are never merged.

**Afterwards:** delete A only once no link and no `entity_events.event_place_address_id` still references it, the same guard as `delete_entity_ancillary`. The FK is `ON DELETE SET NULL`, so deleting A while an event still points at it would silently blank that event's place.

**Why not `orgs_merge` / `ancillary_migrate`:** those re-home entity→entity at the FK level. This merges address→address within one entity. Only the exact-duplicate DELETE idiom is shared.

**The primitive:** a new module, `src/core/address_repair.py`, with unit and integration tests.

**Deferred:** a write-time upgrade, where `write_addresses` would standardize an `unavailable` row in place when the same raw input arrives again. It would prevent the duplicate at the source, but it is a second code path, and today there is no traffic to justify it.

### Q6: A touch trigger on `addresses`

`trg_touch_parents_on_address_change` fires `AFTER UPDATE OF` the served or meaningful columns:
- `raw_input`, `standardized`;
- `address_line_1`, `address_line_2`, `city`, `region`, `postal_code`, `country`;
- `latitude`, `longitude`, `components`, `precision`.

A `WHEN` clause makes it fire only on a real change (`IS DISTINCT FROM`). When it fires, it:
- touches every linked parent through `entity_addresses`, with the same `entity_type` dispatch as `touch_parent_on_entity_address_change`;
- bumps `updated_at` on every `entity_events` row whose `event_place_address_id` is the address.

The status columns are excluded, so a retry that is still `unavailable`, and the backfill, touch nothing.

**Side effect:** this closes a pre-existing gap. An admin edit of a place address never moved the event-list ETag. (The admin routes already touch the entity through their `entity_addresses` UPDATE.)

**Coverage:** the daily parity audit covers the new function and trigger (#331).

### Q7: Audit

The sweep's dry run is the audit:
- It prints counts per status and the age of the oldest `unavailable` row.
- It **exits 3** when any `unavailable` row is older than 3 days, a span that outlasts a maintenance window plus a weekend.

It goes into `docs/AUDITS.md` as an on-demand audit (like #311), with its runbook in `docs/RUNBOOKS.md`. A timer waits for address traffic (Q4).

## Build shape (after approval)

**No separate plan:** one subsystem and one session, so straight to TDD in a new worktree. Red first for each of these:
1. `standardization_status()` and its 7 values. `_STATUS_MAP` `undetermined`.
2. Schema: the columns, the CHECK reconciliation block, the backfill block, and the Q6 function and trigger. Integration tests against the test DB with `--test`:
   - the backfill on seeded `field_confidence` fixtures;
   - the trigger touching role, assignment and event parents, and not firing on status-only updates.
3. The writers: admin ×3 (declined / standardized / fallback / keep_stored), `write_addresses`, and the pipeline.
4. `src/core/address_repair.py`: the collision merge, covering window-exact re-point, the exact-duplicate delete, the event-referenced row kept, and the orphan deleted.
5. `scripts/restandardize_addresses.py`:
   - dry run vs `--execute`;
   - the `--status` choices, with `declined` unselectable;
   - the exit-3 age rule;
   - the target echo, under the existing #399 AST sweep.
6. Docs: `SCHEMA.md` (the columns and trigger), `RUNBOOKS.md` and `AUDITS.md`. Check each against its token budget, and split a doc first if needed. `TESTING.md` is at its limit and is not touched.
7. A minor bump to **0.70.0** in `pyproject.toml` and `package.json`, then `regenerate_client` (the client should be unchanged, because no public schema changes). The `v0.70.0` tag goes on the deployed merge commit.

**Deploy:** `apply-schema.sh --test` and the prod restart happen on the same day. After the operator merges: the pull, `pre-restart-check.sh`, the restart (which applies the schema), the tag, then the first `restandardize_addresses.py` dry run on prod (3 candidates), then `--execute` with the operator's go-ahead.

## Out of scope

- An address validation-status column, and a re-validation sweep (Q3).
- A sweep timer and its co-status monitor (Q4/Q7).
- The write-time in-place upgrade in `write_addresses` (Q5).
- An admin badge showing a row's `standardization_status`.
- Retrying `unsupported` before address-validator adds a country.
- Sending pre-parsed `components` for unsupported countries (the 2026-10-01 comment).

## Accepted limitations

- A non-HTMX client that posts `mode=save` with a blank `standardized` is recorded as `declined`. It explicitly chose to save the address unstandardized.
- The rows backfilled `unavailable` (3) cost one validator call each before they are classified.

## For a follow-up issue

- **The admin rows' `raw_input` is NULL** (correction 2). An observation re-sending an address that a curator typed in unstandardized can't dedup against it. The fix is for the admin routers to set `raw_input`, or for `write_addresses` to key on the composed columns. Prod has 0 admin rows.
