---
title: "#671 usa-wa 2.1.0 seat model: role capacity periods, membership_type, the D7 occupancy audit"
date: 2026-10-24
status: draft
---

# #671 seat model (PR B)

## Problem

usa-wa 2.1.0 (usa-wa#501; spec `docs/specs/2026-10-17-release-2-1-0-seat-model-design.md`
in usa-wa) is the release #506's seat-occupancy ratchet was waiting for. It
publishes per-role, per-period seat capacity (`role_periods` 1.0.0), and it
marks substitutes apart from principals (`assignments.membership_type`, 1.8.0).
The release reached the catalog on 2026-10-22 at 21:13Z
(`v20261022T211327Z-a82710`). PR A (#681) re-pinned `persons` and `assignments`;
it was deployed and executed on 2026-10-23 (17 re-keys).

PR B lands both in PM and moves `scripts/audit_seat_occupancy.py` onto usa-wa's
shared predicate (spec D7). Today the ratchet counts pairs on `single`-type
roles. It cannot tell the lawful multi-member 1889 Senate seats, or the military
substitutes, from producer defects.

## Audit (2026-10-23/24, prod read-only)

- **Ratchet:** 48 pairs after PR A's execute, against BASELINE 55. This matches
  usa-wa's "48 once re-pinned on current code".
- **usa-wa's prediction:** 45 once principals alone are counted, and **25** once
  capacity is read too. That is exactly usa-wa's may tier; its 25 span_key pairs
  are posted on #671 (comment 6351465632) for the cross-check.
- **`role_periods` 1.0.0:** 317 rows, dense across all 312 roles. Five periods
  have `max_holders > 1`, all in 1889–90 (LD-2 3, LD-5 3, LD-8 2, LD-18 3,
  LD-19 5). Seat periods are otherwise 1, and party and committee periods null.
  `valid_to` is the inclusive last day; null means open.
- **`membership_type`:** 3 `substitute` rows (Kreidler LD-22 1991, Wyss LD-6
  2005, Braun LD-20 2017); every other row is `principal`.
- **Role lifespan is unused:** all 2,171 live roles have `established_on` and
  `abolished_on` null, the 312 usa-wa roles included. Capacity is a separate axis
  (§1).
- **Role types of the anchored roles:** `state_senator` (49) and
  `state_representative` (98) are `single`; `committee_member` (157) and
  `party_member` (8) are `shared`. 1,814 legacy roles carry no type.
- **Applier fit:** a `child` binding with `match: key` inserts, updates (nulls
  included) and conflicts as needed. Two gaps:
  1. A child archive acts only on `owned_event_types`.
  2. `overlay: none` is reserved for #592's org link types.
- **Seat cardinality was deliberately left unmodelled.** `docs/SCHEMA_INDEXES.md`
  says it "belongs as a nullable `roles.seat_count`" if ever needed. This design
  supersedes that note: the count varies by era, so neither the role type nor
  one column on a role can hold it.

## Decisions

| # | Question | Decision |
|---|---|---|
| S1 | Where capacity lives | A new table, `role_capacity_periods`, a child of `roles`. It is not on `roles` (no time axis) and not on `role_types` (jurisdiction-wide). |
| S2 | How it lands | The applier's `child` binding, generalised to own a whole table (`owns_rows: all`), with archive retraction and `overlay: none`. The alternative, a new crosswalk kind with entity bindings, touches the crosswalk, tombstones, export and merges, far more than 317 rows need. |
| S3 | `max_holders_basis` | Not stored. Nothing in PM reads it, and it can be added later without a contract change. |
| S4 | `membership_type` | A column on `role_assignments`: NOT NULL, default `principal`, a CHECK on the closed enum. A `column` binding owns it, and a curator can pin it (overlay field `membership_type`). |
| S5 | `/api/v1` | Expose `membership_type` read-only on assignment reads. It is additive and non-breaking. Observations do not accept it; rows they create are `principal`. Capacity is not exposed. |
| S6 | Admin | A read-only badge when `membership_type` is not `principal`. No editor, and no capacity UI. |
| S7 | The audit | usa-wa's D7 may tier, clause for clause, over PM tables. Where no period covers `t`, capacity falls back to the `role_types.occupancy` default (`single` → 1, otherwise no limit). `role_types.occupancy` stays. |
| S8 | BASELINE | 55 → **48** in PR B, today's measured count, which the new predicate reproduces before any data lands. A follow-up PR lowers it to the count measured after the execute (expected 25). That leaves no night where the 00:01Z audit can fail. |
| S9 | Release | Minor bump to v0.68.0, a tag, and a regenerated client. |

## Design

### §1 Four date axes, kept apart

| Axis | Columns | Meaning | Convention |
|---|---|---|---|
| Role lifespan | `roles.established_on` / `abolished_on` | When the office exists | Unused today; written on first observation only |
| Org lifespan | `v_org_lifespan.ended_on` (from `dissolved` / `merged_with` / dated `succeeded_by` events) | When the org ends; bounds assignment windows (#307) | Latest date the precision allows |
| Service | `role_assignments.start_date` / `end_date` / `is_current` | One person's tenure | `end_date` is a handoff date, exclusive in overlap tests |
| **Capacity** | `role_capacity_periods.valid_from` / `valid_to` / `max_holders` | How many principals the seat holds at once | `valid_to` is the **inclusive** last day |

A capacity period never writes or derives a role's lifespan, and a lifespan
never clips a period. A role's first period starts at its earliest *published*
tenure (usa-wa accepted limit 4), and its last stays open even for an abolished
office. Only the audit reads periods. `membership_type` qualifies a tenure: a
substitute's tenure sits inside the principal's, which is not cut for the leave.

### §2 Schema (`src/core/schema.sql`)

```sql
CREATE TABLE IF NOT EXISTS role_capacity_periods (
    id          TEXT        PRIMARY KEY,
    role_id     TEXT        NOT NULL REFERENCES roles(id) ON DELETE CASCADE,
    valid_from  DATE        NOT NULL,
    valid_to    DATE,                       -- last day, INCLUSIVE; NULL = open
    max_holders INTEGER,                    -- NULL = no limit
    archived_at TIMESTAMPTZ,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT chk_capacity_period_range CHECK (valid_to IS NULL OR valid_from <= valid_to),
    CONSTRAINT chk_capacity_period_max   CHECK (max_holders IS NULL OR max_holders > 0)
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_role_capacity_period_start
    ON role_capacity_periods (role_id, valid_from) WHERE archived_at IS NULL;
```

- The table gets a `set_updated_at` trigger and a `COMMENT ON` stating both date
  conventions.
- `ON DELETE CASCADE` follows the role: the admin role merge hard-deletes the
  loser, and the next applier run re-inserts periods on whichever role the
  anchor names.
- `role_assignments.membership_type TEXT NOT NULL DEFAULT 'principal'` is added
  by `ADD COLUMN IF NOT EXISTS`. The CHECK `chk_ra_membership_type`, on
  `principal | substitute | ex_officio | acting`, is reconciled by an idempotent
  `DO` block placed before the table's `set_updated_at` trigger (#307/#312).
- `docs/SCHEMA.md` gains the table and the column. `docs/SCHEMA_INDEXES.md`'s
  seat-cardinality note is replaced by a pointer to this table and the reason
  (Audit, last bullet).

### §3 Mapping project

- **`sources.yml`:** pin `role_periods` with `schema_major: 1` and contract
  `sha256:e5837f6d9251173c742544119e0aed9eaf03d49739fb60cbc5445d6c7a21e828`.
  It is wired like `org_lineage` (#592): `PM_SRC_ROLE_PERIODS`, plus an entry in
  `USA_WA_SOURCES`. Pinning it is the subscription; the next pull lands it.
- **`stg_usa_wa__role_periods.sql`:** trims, casts `valid_from` / `valid_to` to
  dates and `max_holders` to integer. `schema.yml` tests a unique non-null key
  `(role_key, valid_from)`.
- **`stg_usa_wa__assignments.sql`** selects `membership_type`.
- **`desired_role_capacity_periods`:** `producer_id` (the role's entity id),
  `pm_id`, `valid_from`, `valid_to`, `max_holders`, joined through the roles
  mapping on `role_key`. A period whose role does not resolve is named by the
  warn test `unresolved_role_capacity_periods` and claims nothing.
- **`desired_role_assignment_membership`:** `producer_id` (span_key), `pm_id`,
  `membership_type`. An active pin wins by presence (#498), as in
  `desired_role_assignment_dates`. `membership_type` joins the assignment fields
  `overlay_field_unmapped` accepts.
- **`manifest.yml`:** both tables declare `types` (#569). The bindings are in §4.

### §4 Applier

```yaml
desired_role_capacity_periods:
  entity: role
  key: [producer_id, valid_from]
  pm_key: pm_id
  retraction: archive          # a re-tiled period archives, gated as an archive
  owns_rows: all               # every live row under an in-scope role is the producer's
  owned_columns: [valid_to, max_holders]
  overlay: none                # dataset-owned table: no PM write door exists
  target:
    shape: child
    table: role_capacity_periods
    parent: role_id
    columns: {valid_from: valid_from, valid_to: valid_to, max_holders: max_holders}
    match: key
    key_columns: [valid_from]
    archived: archived_at
desired_role_assignment_membership:
  entity: assignment
  key: [producer_id]
  pm_key: pm_id
  retraction: none
  owned_columns: [membership_type]
  overlay: {membership_type: membership_type}
  target:
    shape: column
    table: role_assignments
    columns: {membership_type: membership_type}
```

- **`manifest.py`:**
  - `owns_rows` takes `all`, and only on a child binding.
  - A child `archive` is valid with `owned_event_types` **or** `owns_rows: all`.
  - `overlay: none` is valid for a child binding owning only the locked link
    types (#592) **or** over a table in `DATASET_OWNED_TABLES`.
- **`src/core/dataset_ownership.py`:** gains `DATASET_OWNED_TABLES =
  frozenset({"role_capacity_periods"})`, and `test_manifest.py` holds it to the
  manifest, as it does the link types.
- **`applier.py`:** `_absent_children` skips the event-type filter under
  `owns_rows: all`. Its entry rebuilds the row from the key columns, as now.
- **Behaviour:**
  - First run: 317 `insert`s, which no threshold gates.
  - A re-tiled period archives the old `valid_from` (gated as an archive) and
    inserts the new one.
  - A role this run creates gets its periods the same run.
  - Only rows under in-scope roles are read or written.
  - `membership_type`: 3 `update`s on the first run.

### §5 The audit (`scripts/audit_seat_occupancy.py`)

The predicate mirrors usa-wa's `seat_overlap_pairs('may')`:

- **Principals:** unarchived assignments on live roles with `membership_type =
  'principal'`, a known `start_date`, and either an `end_date` or `is_current`.
  Each covers `[start_date, end_date)`. An open current tenure covers every
  later instant; that branch is spelled out as an explicit null test.
- **Instants:** each principal's start, plus each live period's start on a role
  with principals. Capacity can drop with no one starting (usa-wa correction 7).
- **Capacity at `t`:** a live period covering `t` (`valid_from ≤ t ≤
  coalesce(valid_to, ∞)`) supplies its `max_holders`, null meaning no limit.
  With no covering period, the role type's `occupancy` decides: `single` → 1;
  `shared`, or no type → no limit.
- **Finding:** at each instant where more distinct persons hold the role than its
  capacity, every pair of those holders' assignments (`a.person_id <>
  b.person_id`, unordered by id). The count is the number of distinct pairs.
  Each pair still logs one `seat overlap:` line, so a journal diff names a
  pair a night added.
- **Ratchet:** unchanged. Exit 3 only above BASELINE; a WARNING below it.
  BASELINE = 48 (S8).
- **The divergence from usa-wa** is intentional: they read a missing period as
  one holder, and PM reads it as the type default (their spec D7, PM paragraph).
  It covers PM's legacy and admin rows.

### §6 API and admin

- **`AssignmentListItem`:** gains `membership_type: Literal[...]`, which
  `AssignmentDetail` inherits. Every query feeding assignment reads selects the
  column.
- `AssignmentObservationRequest` is unchanged. `docs/API_ASSIGNMENTS.md` calls
  the field producer-owned and read-only.
- A write bumps `updated_at` through the trigger, so the ETag moves (#392)
  without an `etag.py` change.
- Run `uv run python -m scripts.regenerate_client` (#618) and bump `pyproject.toml`,
  `package.json`, the lock and `clients/python` to 0.68.0.
- **Admin:** a `Substitute`, `Ex officio` or `Acting` badge (existing badge
  classes) next to Current/Former/Archived in
  `people/partials/_assignment_row.html`, `roles/partials/_assignment_row.html`
  and the assignment detail header. The value is producer-owned, so there is no
  editor and no overlay slot.

## Testing

- **Schema (integration, test DB):**
  - Both period CHECKs and the membership CHECK refuse bad values.
  - The default is `principal`.
  - The partial unique index refuses a second live period on one start.
  - A role delete cascades to its periods.
  - Applying the reconciliation block twice changes nothing.
- **Mapping (fixture tier):**
  - Staging casts and keys.
  - Both marts' shapes and `types`.
  - An unresolved role is named and claims nothing.
  - A `membership_type` pin wins.
  - `test_real_snapshot.py`: 317 periods staged and desired; 3 `substitute`.
- **Applier:**
  - Manifest validation: `owns_rows: all` is accepted on a child, refused
    elsewhere, and refused for archive without it; `overlay: none` is refused
    for a table outside `DATASET_OWNED_TABLES`.
  - A dropped period archives.
  - A period on an out-of-scope role is untouched.
  - An update writes null.
  - A fixture plan comes out as N inserts plus 3 updates.
- **Audit (unit + integration):**
  - An m=1 overlap counts; a same-day handoff does not.
  - An open current tenure conflicts.
  - A substitute does not count.
  - Three holders under m=3 pass and four fail.
  - A capacity drop at a period start is caught with no one starting.
  - With no period, `shared` means no limit.
  - The same person twice is not a pair.
- **API and admin:**
  - List and detail return the field, and the client drift test passes.
  - The badge renders on a substitute row and is absent on a principal.
- **Pre-merge prod evidence (read-only, posted on #671):**
  1. The new SQL, with no periods and every row `principal`, reads **48**, the
     same pairs as today's audit.
  2. With the five published multi-member periods and the three substitute
     span_keys fed inline, it predicts **25**, the same pairs as usa-wa's list.

## Rollout (each step approved by the operator)

1. Merge PR B, then pull into main.
2. `sudo systemctl restart power-map`, which applies the table and the column.
3. `sudo systemctl start power-map-datasets-pull.service`: `role_periods` 1.0.0
   lands.
4. `sudo systemctl start power-map-desired-state.service`: the dry run should
   show 317 inserts, 3 updates and nothing else.
5. Supervised execute with `--streak 1`. No `--allow-*` should be needed,
   because inserts and updates are not thresholded.
6. Run the audit read-only and expect 25. Then the cross-check: map usa-wa's 25
   span_key pairs to assignment ids through the crosswalk, diff them against
   the audit's pair log, and post the result on usa-wa#501. A PM overlay (#639)
   is a legitimate cause of difference.
7. A follow-up PR lowers BASELINE 48 → the measured count.
8. Tag v0.68.0.

The schema change and the code ship together, so the 00:01Z parity run sees no
drift.

## Accepted limitations

- Capacity covers only the roles usa-wa publishes. Every other role is judged
  by its type default.
- `max_holders_basis` is not stored (S3).
- A role merge drops the loser's periods until the next run re-inserts them on
  the anchored role.
- The interval tiers (`valid_from_latest` / `valid_to_earliest`) are not
  stored, so PM applies the may tier only, by design (usa-wa spec D7).

## Out of scope

- `persons.name_own` (no mapping decided).
- `start_basis` / `end_basis` and the interval columns.
- Capacity in `/api/v1` or the admin.
- An admin editor for `membership_type`.
- `substitutes_for` edges (#301 is where they would go if usa-wa ever publishes
  them).
- `district_plan` (usa-wa D3; it arrives with its first population).
