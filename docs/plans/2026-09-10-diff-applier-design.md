# Diff-applier: generic, dry-run by default, row- and column-scoped (#499)

Part of #490 (design § PM-side pipeline step 4, safeguards 2/4/5). Follows #497,
whose outputs are its inputs: `src/core/ingestion/mapping/manifest.yml`,
`data/desired_state/*.parquet`, and the seeded `producer_crosswalk` (#495).

## Goal

Turn the desired state into minimal writes against live Postgres — or, by
default, into a report of the writes it *would* make — without the Python ever
knowing what a Washington legislator is. The applier is the seam between the
mapping models and the database; every WA fact stays in the models.

## What the first dry run will see (measured 2026-09-10, production, read-only)

| Table | Desired | Equal to PM's canonical row | Differs | Of which present on another row of the type | Notes |
|---|---|---|---|---|---|
| `desired_person_names` | 3,113 anchored | 3,086 | 27 | 3 | the 24 absent are punctuation / nickname forms (`“Slim”` vs `"Slim"`, Mike vs Michael); 2 people hold 2 legal rows |
| `desired_people` creates | 17 | — | — | — | 13 share a legal name with a person PM already holds (Patty Murray, Jack Metcalf, …) |
| `desired_organization_names` | 219 | 153 | 66 | **66** | PM holds the short *and* the long form as legal rows on 72 orgs; the producer's `long_name` is always one of them |
| `desired_organization_acronyms` | 186 | 124 | 50 (+12 with no canonical) | 36 | 26 absent everywhere (`ETT` vs `EN`, `NRPR` vs `ENR`); 24 orgs hold >1 acronym row |
| `desired_organization_parents` | 206 | 202 | 4 | — | the 3 House Appropriations subcommittees + 1 Senate BH subcommittee: PM parents them under the parent committee (the 4 rows carrying `source_key_id`, i.e. usa-wa's own #334 reparent), the `agency` rule says the chamber — overlay rows in #501 |
| `desired_entity_events` | 152 | 152 | 0 | — | PM holds month/day on none |
| retractions (in scope, absent from snapshot) | 0 persons, 0 orgs | | | | |
| merges | 0 | | | | |

Two conclusions drove the design. A keyed child row cannot mean "the canonical
row" — that reading renames 66 org display names PM chose on purpose. And
creates cannot be allowed by default — 13 of 17 are almost certainly people PM
already holds under another producer's anchor.

## Approved approach: a manifest-driven engine with four binding shapes

`manifest.yml` (now `version: 2`) gains a `target:` block per table naming the
PM table and how a desired row binds to it. One engine, `src/core/ingestion/
applier.py`, interprets four shapes and nothing else:

| Shape | Binds a desired row to | Tables today | #500 |
|---|---|---|---|
| `entity` | a row in an entity table (`people`, `organizations`): identity, create, report-only retraction | `desired_people`, `desired_organizations` | roles, assignments (with `archive`) |
| `column` | one column on that row | `desired_organization_parents` → `organizations.parent_id` | — |
| `child` | a keyed child row of the entity, matched by `any_then_canonical` or `key` | names, acronyms, events | — |
| `merge` | nothing — every row is a report entry | both merges tables | — |

Python never names a PM table or column except through the manifest, so the
design's "schema-driven, domain-free" holds by construction and #500 is
manifest entries plus models.

Rejected: **one Python class per table** (nine now, more later; the manifest
decays into documentation); **diffing in SQL via temp tables** (puts a live-DB
dependency where the design kept files, and the engine's tests would need a
database — 10⁴ rows is nothing in Python).

## Decisions

**Row scope is the live crosswalk, read at run time** — `producer_crosswalk`
rows for the source with `resolution IN ('live','merged')` — never the `pm_id`
the desired row carries. A desired row whose `pm_id` disagrees with the live
crosswalk, or whose row left scope, is a `stale` entry: the desired state
predates a merge or an archive and must be rebuilt before anything is applied.

**Child rows: present anywhere satisfies; else update the canonical.** Among
the parent's rows of the key type, any row carrying the asserted value is a
`noop` (orgs: 0 entries, display untouched). Absent everywhere: the canonical
row of that type is the row in dispute and becomes an `update` entry for #501
to decide apply-or-overlay (persons: 24, acronyms: 26); with no canonical row
of the type, `insert`, canonical only when the parent has none (the writers'
own first-wins rule). Rejected: always-update-canonical (66 renames PM chose);
insert-if-absent (the 24 disagreements never surface, PM gains near-duplicate
legal rows).

**Events match on `(entity, type)` among unarchived rows** — none → `insert`,
one → compare the owned column, more than one → `conflict`. Only owned types
are ever read (`dissolved`), so PM's other 315 org events do not exist to the
applier.

**Parents bypass the #334 provenance gate by design.** Row scope is the gate;
the write is a plain `UPDATE` of the owned column and `trg_no_org_cycle` still
guards — a rejection rolls the whole run back.

**Merges are report-only in #499** (#514 acts on them, triggered by the first
real tombstone). **Creates are built and gated at 0** — a `create` mints the
entity row, its canonical legal name (public, canonical because new), its other
child rows and columns, and a `producer_crosswalk` row (`exported_pm_id = pm_id
=` the new ULID, `resolution = 'live'`, export fields null: the applier is the
exporter). Each create entry carries a hint — existing rows of the child table
whose value equals the asserted name — for #501's link-or-allow decision. No
`identifiers` row: `usa_wa_person` is not seeded yet (#513).

**Thresholds and the execute gate.** `manifest.yml` top-level `thresholds:`
(defaults `creates: 0`, `merges: 0`, `conflicts: 0`, `stale: 0`, `updates:
unlimited`) and `streak: 3`. A dry run always completes and records a verdict:
`clean`, `blocked` (a threshold exceeded), `stale`. It exits 0 for clean or
blocked — seventeen pending creates must not fail the timer every night — and
3 for stale or a load error, which needs a person. `--execute` refuses (exit 1,
before any transaction) unless the ledger's last `streak` dry runs are all
`clean` with the **same diff digest** and the current run reproduces it: the
diff must be stable, not merely small. Writes are one transaction; the
in-transaction re-diff must be empty of actionable entries or everything rolls
back. Exceeding a threshold therefore aborts before the first write, never
partway. CLI overrides for the flip: `--allow-creates N`, `--max-updates N`,
`--streak N`.

**Outbox attribution.** Writes go through plain SQL so `updated_at`, the touch
triggers and `fn_record_entity_change` fire as they do for any writer; the
applier stamps no `source_key_id` (it is not an API key, and `/changes` has no
consumer — #502).

**The artifact.** `data/applier/<run-id>/diff.jsonl` — one entry per line,
`entry_id = <table>:<key>` stable across runs so a #501 decision can name it;
`summary.json` (counts, thresholds, verdict, diff digest, inputs);
`summary.md`. `data/applier/ledger.jsonl` — one line per run. **Provenance**
(round-2 finding 24, held for this issue): `build_desired_state` writes
`data/desired_state/BUILD.json` — dataset versions used, crosswalk export
digest, built-at — and the applier copies it into every summary and ledger
line.

**The nightly chain.** `infra/power-map-desired-state.service` (oneshot, three
`ExecStart=` lines: `export_pm_tables`, `build_desired_state`,
`apply_desired_state`, each `uv run --group mapping` because the service's own
`uv sync` prunes that group) + `.timer` at 09:30 UTC, after the 09:00 pull. A
failing step stops the chain and reaches `systemctl --failed`.

## Ordering inside an execute

All entity creates, then child rows, then columns — so a created parent exists
before a child names it and a created org exists before a parent claim. Each
statement names exactly the owned columns plus key/parent/constant columns and
the insert defaults the manifest declares; `updated_at` is the trigger's.

## Testing

- **Unit, no database.** The engine takes a `LiveStore` protocol; a fake serves
  rows from dicts and records every emitted statement. Every shape and match
  outcome, stale scope, thresholds, the streak gate over a fixture ledger,
  report and ledger formats, entry-id and digest stability — and the two
  acceptance proofs on emitted SQL: a column outside the owned set is never in
  an `UPDATE`; a row outside the crosswalk is never read or written.
- **Integration**, rollback connection: seed a small world; apply twice (second
  is a no-op); a gated create aborts with zero rows changed; `--execute` writes
  exactly the predicted entries and the outbox rows appear; `people.notes`
  survives a name update; a person outside the crosswalk survives a desired row
  naming it; a cyclic parent rolls the run back.
- **Operator check**, not a test: the runbook's first dry run against
  production — the table above is what it should say.

## Docs, version

`RUNBOOK_DESIRED_STATE.md` § Apply the desired state; `COMMANDS.md` scheduled-timers row;
`TESTING.md` applier tier; `SCHEMA.md` § Producer crosswalk (the applier writes
creates). Minor bump to 0.47.0.

## Out of scope

Acting on merges (#514); roles, assignments and archive retraction (#500); the
overlay write path (#498); triage tooling and link-to-existing decisions
(#501); the identifier collapse (#513).
