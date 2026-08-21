# power-map — Operational Runbooks

Data operations: the importer, the idempotent seeds, the role data-quality sweep
and the TTL prune. Every writer here is dry-run by default and needs `--execute`;
the resolver and target-echo rules that make that uniform are below, in
§"Operational scripts — dry run by default & target echo".

The recurring integrity audits live in `docs/AUDITS.md`, incident triage for an
unreachable database in `docs/RUNBOOK_DB_TRIAGE.md`, and the planned-cutover
checklist in `docs/RUNBOOK_DB_MIGRATION.md`.

---

## Import


```bash
# Build --env-file flags (see § Environment)
env_args=()
[ -f /etc/power-map/.env ] && env_args+=(--env-file /etc/power-map/.env)
[ -f .env ] && env_args+=(--env-file .env)

# Dry run (#402) — runs the real pipeline, rolls it back, prints the summary
uv run "${env_args[@]}" python scripts/import_cannabis_observer.py \
    --orgs   data/cannabis_observer/Organizations.csv \
    --people data/cannabis_observer/People.csv \
    --roles  data/cannabis_observer/Roles.csv

# Commit — the default DATABASE_URL is PRODUCTION, from any directory
uv run "${env_args[@]}" python scripts/import_cannabis_observer.py \
    --orgs   data/cannabis_observer/Organizations.csv \
    --people data/cannabis_observer/People.csv \
    --roles  data/cannabis_observer/Roles.csv \
    --execute

# Also run address validation (requires ADDRESS_VALIDATOR_API_KEY)
uv run "${env_args[@]}" python scripts/import_cannabis_observer.py \
    --orgs   data/cannabis_observer/Organizations.csv \
    --people data/cannabis_observer/People.csv \
    --roles  data/cannabis_observer/Roles.csv \
    --validate-addresses --execute

# Options
#   --execute                   Commit (default is a dry run, rolled back)
#   --database-url DSN          Target another database (default: DATABASE_URL)
#   --apply-schema              Apply schema.sql first — fresh DB only; requires
#                               --execute. Prefer scripts/apply-schema.sh (#398).
#   --source-reliability FLOAT  Source reliability score (0.0–1.0, default: 0.8)
#   --imported-by STRING        Importer label (default: cannabis-observer-csv-import)
#   --validate-addresses        Also call /validate for deliverability confirmation
```

The target database is echoed (redacted) to stderr before the connection, so a
run is attributable in scrollback. A dry run still calls the external
address-validator when `ADDRESS_VALIDATOR_API_KEY` is set — it is the same
pipeline. Applying schema DDL was implicit before #402; it is now opt-in, and
`scripts/apply-schema.sh` is the door that carries the production guards.

---

## Seed BCP 47 / ISO 15924 lookup tables (after schema apply, idempotent)


```bash
# Build --env-file flags (see § Environment)
env_args=()
[ -f /etc/power-map/.env ] && env_args+=(--env-file /etc/power-map/.env)
[ -f .env ] && env_args+=(--env-file .env)

# Populate bcp47_locales + iso15924_scripts from langcodes + pycountry.
# Idempotent — safe to re-run to pick up registry updates.
# Dry run (reports insert/update counts, writes nothing) without --execute:
uv run "${env_args[@]}" --group seed scripts/seed_locales_scripts.py

# Commit (#402 — the default DATABASE_URL is production):
uv run "${env_args[@]}" --group seed scripts/seed_locales_scripts.py --execute
```

To target the test database, use `scripts/sync-schema-to-do.sh` — it resolves
`TEST_DATABASE_URL` from `/etc/power-map/.env` itself and passes it through as
`--database-url`. `$TEST_DATABASE_URL` is **not** set in your shell by the
`--env-file` flags above; `uv run --env-file` populates the child process only.

Required after a fresh `apply_schema` on a brand-new DB. The FK on
`person_names.locale` / `.script` is active immediately, so any non-NULL
write fails until this script populates the lookup tables. `apply_schema`
logs a WARNING when either lookup table is empty.

---

## Seed jurisdictions from a pre-seed JSON file (idempotent)


Prerequisite: `apply_schema` must be run first so the `jurisdiction_relationship_types` seed
rows (including `is_fully_contained_by`) are present. Without it the script raises
`ValueError: Unknown relationship type`.

```bash
# Build --env-file flags (see § Environment)
env_args=()
[ -f /etc/power-map/.env ] && env_args+=(--env-file /etc/power-map/.env)
[ -f .env ] && env_args+=(--env-file .env)

# Dry run — show counts, no DB writes
uv run "${env_args[@]}" python -m scripts.seed_jurisdictions data/cannabis_observer/2026_06_07-usa_wa-jurisdictions.json

# Execute — upsert jurisdictions + relationships and commit
uv run "${env_args[@]}" python -m scripts.seed_jurisdictions data/cannabis_observer/2026_06_07-usa_wa-jurisdictions.json --execute
```

Safe to re-run; upserts are idempotent.

---

## Seed WA legislative roles (idempotent, #263)


Creates the 147 canonical legislative roles (49 Senate + 98 House Position 1/2) against
the already-seeded `legislative_district` jurisdictions. Prerequisites: `apply_schema`
(role_types seeded), § Seed jurisdictions from a pre-seed JSON file (LD jurisdictions
present), and the WA
chamber orgs carrying the `org_wa_legislature_chamber` identifier (`usa_wa_house` /
`usa_wa_senate`). The role seed file is a local, gitignored artifact under
`data/cannabis_observer/` — regenerate it from the jurisdictions seed if absent.

```bash
# Build --env-file flags (see § Environment)
env_args=()
[ -f /etc/power-map/.env ] && env_args+=(--env-file /etc/power-map/.env)
[ -f .env ] && env_args+=(--env-file .env)

# 1. Generate the role seed JSON from the jurisdictions seed (deterministic, no DB)
uv run "${env_args[@]}" python -m scripts.generate_wa_roles \
    data/cannabis_observer/2026_06_07-usa_wa-jurisdictions.json \
    -o data/cannabis_observer/2026_07_03-usa_wa-legislative-roles.json

# 2. Dry run — read-only; reports would-create / already-exist / unresolved counts
uv run "${env_args[@]}" python -m scripts.seed_roles data/cannabis_observer/2026_07_03-usa_wa-legislative-roles.json

# 3. Execute — create-or-attach the roles and commit
uv run "${env_args[@]}" python -m scripts.seed_roles data/cannabis_observer/2026_07_03-usa_wa-legislative-roles.json --execute
```

Idempotent: roles match on identity (org + role_type + jurisdiction + qualifier), so re-runs
attach rather than duplicate. Seeder, not updater — it does not revise existing roles'
titles/attributes. Merging existing (idiosyncratic) legislator Roles onto these roles is
separate (#265).

---

## Role data-quality sweep (idempotent, #304)


`scripts/sweep_role_data_quality.py` — follow-on to #266, data-only. Operates **only**
on plain free-text roles (`role_type_id IS NULL AND jurisdiction_id IS NULL`, whose
match key is `(org, lower(title))` = `uq_role_org_title`):

- **Archive non-role artifacts** — `Guest` / `Visitor or Guest`: attendance noise that
  leaks into membership queries. Archives active assignments, then the role (never
  hard-deleted).
- **Normalize typo'd titles** — `Principle` → `Principal`: a misspelling orphans the
  `(org, lower(title))` match key. Renames in place; when the same org already carries
  the canonical role (would collide on `uq_role_org_title`), instead **merges** the typo
  role in — assignments re-pointed with `(person, start_date)` dedup, loser notes +
  role-level ancillary preserved onto the survivor (#324/#326), loser hard-deleted
  (mirrors admin `role_merge`).

The `Participant` disposition, bare `Chairman` normalization, and `Ranking Democratic
Member` typed-fold are deliberately **out of scope** — vocabulary judgment calls for
#266, not a mechanical sweep. Idempotent: canonical titles aren't typo keys and artifact
roles archive once, so re-runs no-op. The dry run distinguishes `would_rename` vs the
destructive `would_merge` per row.

```bash
# Build --env-file flags (see § Environment)
env_args=()
[ -f /etc/power-map/.env ] && env_args+=(--env-file /etc/power-map/.env)
[ -f .env ] && env_args+=(--env-file .env)

uv run "${env_args[@]}" python -m scripts.sweep_role_data_quality            # dry run
uv run "${env_args[@]}" python -m scripts.sweep_role_data_quality --execute  # commit
```

---

## Outbox + tombstone TTL prune (issue #204)


`scripts/prune_outbox.py` deletes rows past the retention window (default 90 days)
from **three** append-only tables: `entity_changes` (the change-feed outbox),
`deleted_entities` (deletion tombstones), and `api_request_log` (the public-API
request log, issue #260). All three TTLs stay aligned so the public change feed,
the 404-fallback signal, and the request-observability window expire together.
Sibling services must poll at least once per window or full-reconcile (see
`docs/PUBLIC_API.md` § change feed).

Manual run:

```bash
# Build --env-file flags (see § Environment)
env_args=()
[ -f /etc/power-map/.env ] && env_args+=(--env-file /etc/power-map/.env)
[ -f .env ] && env_args+=(--env-file .env)

# Dry run — count eligible rows in both tables
uv run "${env_args[@]}" python -m scripts.prune_outbox

# Execute — delete (optionally override the window)
uv run "${env_args[@]}" python -m scripts.prune_outbox --execute
uv run "${env_args[@]}" python -m scripts.prune_outbox --execute --retention-days 90
```

Scheduled (production): a daily systemd timer runs `--execute`. Install / update:

```bash
sudo cp infra/power-map-prune.service infra/power-map-prune.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now power-map-prune.timer

# Inspect
systemctl list-timers power-map-prune.timer   # next/last run
sudo systemctl start power-map-prune.service   # run once, now
sudo journalctl -u power-map-prune -f          # logs (rows pruned per run)
```

## Operational scripts — dry run by default & target echo (#402)


`DATABASE_URL` comes from `/etc/power-map/.env` and resolves to **production**
from any directory — main checkout, worktree, anywhere on the VM. Nothing about
a `scripts/…` invocation signals that. Two rules follow, and they are separate
concerns: the gate stops an unintended write, the echo makes an intended one
attributable afterwards.

**Every script that writes gates the write behind `--execute`.** The bare
invocation is read-only and reports what would change. #398 fixed
`apply-schema.sh`, the one script that wrote unconditionally; #402 found two
more (`import_cannabis_observer.py`, `seed_locales_scripts.py`) and closed
them. The convention was believed universal before that and was enforced by
nothing — #399's AST sweep is what makes it stop depending on memory.

**Every script echoes its target before connecting**, via `add_dsn_args()` +
`resolve_dsn()` from `scripts/_dsn.py`:

```
target: co_pm_db_production_user@co-pm-db-1-….ondigitalocean.com:25060/co_pm_db_production (production)
```

The label is derived by matching `(host, port, dbname)` — **not** the DSN
string. Production is reached as two different users (`DATABASE_URL` as the app
user, `MIGRATIONS_DATABASE_URL` as the migrations user); string equality would
label a migrations DSN `unknown`. Anything unmatched is
`unknown — assume production`, never `test`: the consequence of guessing wrong
runs one way.

The uniform flags (#399):

| Flag | Effect |
|---|---|
| *(none)* | `DATABASE_URL` — production |
| `--database-url DSN` | that DSN |
| `--test` | `TEST_DATABASE_URL`; **hard-errors when unset** — never falls back to `DATABASE_URL`, which would be a production write dressed as a test write |

**Resolve last.** The echo means "about to connect", so `resolve_dsn` goes
*after* any input validation that can abort the run — otherwise the journal
records a database the run never opened, which is the false attribution the
echo exists to prevent. `check_api_anomalies` extends this to its
`threshold <= 0` short-circuit: a disabled run resolves nothing.

Passing `--test` and `--database-url` together is an error, not a precedence
rule. A script whose target flags are domain-named (`audit_schema_constraint_parity`
takes `--target-url` / `--reference-url`) uses `default_dsn()` for the default
and calls `echo_target(..., role=…)` per connection, so each gets its own line.

`redact_dsn()` drops the password *and* the query string, and returns `None`
for anything that is not a parseable URL. **Callers never fall back to printing
the raw string**: `urlparse` hands back a libpq keyword/value DSN
(`host=… password=…`) with the credentials in `path`, so a "best effort" echo
would put the password in the journal. An absent database name renders `?`.

Two shapes of dry run, both legitimate:

| Shape | Used by | Note |
|---|---|---|
| Read-only preview | `seed_locales_scripts.py`, the `audit_*` scripts | Classify against current state; write nothing |
| Real work, rolled back | `import_cannabis_observer.py` | The summary printed is the summary `--execute` produces |

The rolled-back shape has one trap: **side effects outside the transaction do
not roll back.** The importer parses addresses locally on a dry run
(`ImportConfig.local_addresses_only`) rather than spending the rate-limited
external validator's quota on a run that changes nothing — standardization
fires whenever `ADDRESS_VALIDATOR_API_KEY` is set, independent of
`--validate-addresses`, so that flag is the only lever. The cost is that
address fields in a preview may differ from a committed run; the dry-run notice
says so.

Schema DDL is never implicit. `scripts/apply-schema.sh` owns applying
`schema.sql` and carries the #398 production guards; the importer's
`--apply-schema` is opt-in and requires `--execute`, because DDL inside a run
about to be rolled back would be a lie.

All three rules are enforced by `tests/scripts/test_dsn_sweep.py`, an AST sweep
over every `scripts/*.py`: it connects ⇒ goes through `_dsn.py`; nobody reads
`DATABASE_URL` directly; write SQL ⇒ declares `--execute`. **It has no
allowlist** — an exemption set is a place for a live script to hide. If a new
script genuinely cannot comply, change the sweep with a reason in the diff.

`apply-schema.sh` deliberately keeps its **own copy** of the redaction logic
rather than importing `_dsn.py` — it runs as `ExecStartPre` on the systemd
unit, where an import failure would mean a failed production restart. The two
copies are pinned in agreement by
`tests/scripts/test_dsn.py::test_redaction_matches_apply_schema_sh`.

---

## Completed runbooks

One-off migrations and backfills already run against production are archived in
`docs/archive/RUNBOOKS_COMPLETED.md` — kept verbatim for provenance, excluded from
the live context surface. Nothing there is pending.
