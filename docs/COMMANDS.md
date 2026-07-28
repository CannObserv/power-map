# Common Commands

## Setup

```bash
# Install Python dependencies (creates .venv automatically)
uv sync

# Install Node dependencies
npm install

# Install git pre-commit hooks (runs ruff, pytest, ESLint, Prettier, vitest on every commit)
uv run pre-commit install
```

Database is on DO managed PostgreSQL — see § Provisioning for first-time setup. (`scripts/setup-db.sh` provisions local postgres only; use it for offline dev or CI without DO access.)

The `/etc/power-map/.env` file is created by `bash scripts/write-db-secrets.sh` as part of the provisioning flow (§ Provisioning step 4).

## Environment

Two env files; both feed `uv run` via `--env-file`. uv has a proper dotenv parser and respects key/value quoting, unlike `cat … | xargs`. The flags are gated on existence because uv errors hard on a missing `--env-file`.

```bash
# Build --env-file flags once; reuse across uv run invocations below
env_args=()
[ -f /etc/power-map/.env ] && env_args+=(--env-file /etc/power-map/.env)
[ -f .env ] && env_args+=(--env-file .env)
```

Later files win on conflicting keys.

| File | Contents |
|---|---|
| `/etc/power-map/.env` (640, root:exedev) | `DATABASE_URL`, `MIGRATIONS_DATABASE_URL`, `TEST_DATABASE_URL`, `ADDRESS_VALIDATOR_API_KEY`, `ADDRESS_VALIDATOR_RUN_VALIDATION` (default false; true → `/validate`, false → `/standardize` only), `ADDRESS_VALIDATOR_BASE_URL` (optional; defaults to `https://address-validator.exe.xyz:8000`), `DB_POOL_MIN_SIZE` (default 2), `DB_POOL_MAX_SIZE` (default 5; tune per DO tier), `API_REQUEST_LOG_MAX_PENDING` (optional; default 50; soft cap on in-flight fire-and-forget `api_request_log` capture writes before shedding — #290; tune relative to `DB_POOL_MAX_SIZE`; `0` disables capture entirely), `RATE_LIMIT_READ_PER_S` / `RATE_LIMIT_READ_BURST` / `RATE_LIMIT_WRITE_PER_S` / `RATE_LIMIT_WRITE_BURST` (optional; defaults 2/120/1/60; per-key token buckets on the public API — #292; per-worker, so effective ceiling ≈ workers × rate; refill ≤ 0 disables that bucket), `API_KEY_LAST_USED_DEBOUNCE_S` (optional; default 60; min seconds between `api_keys.last_used_at` stamps per worker — #292; `0` stamps every request) |
| `.env` (repo, gitignored) | `GH_TOKEN` |

## Provisioning

One-time setup for the DO managed PostgreSQL cluster (`co-pm-db-1`, sfo3). State is stored in the `co-pm-spaces-1` DO Spaces bucket.

### Prerequisites

- `terraform` ≥1.9 installed (see <https://developer.hashicorp.com/terraform/install>)
- `infra/terraform/terraform.tfvars` — DO personal access token + IP allowlist (gitignored; see AGENTS.md § Environment Variables)
- `infra/terraform/backend.hcl` — DO Spaces access key + secret (gitignored)
- `jq`, `psql`, `python3` on PATH (used by `write-db-secrets.sh`)

### First-time provisioning

```bash
# 1. Initialise Terraform with the Spaces backend (run once per checkout)
terraform -chdir=infra/terraform init -backend-config=backend.hcl

# 2. Preview — confirm 8 resources: vpc, cluster, 2 databases, 3 users, firewall
terraform -chdir=infra/terraform plan

# 3. Apply (~5 min; cluster creation dominates)
terraform -chdir=infra/terraform apply

# 4. Write credentials to /etc/power-map/.env and apply schema-level grants
bash scripts/write-db-secrets.sh

# 5. Install extensions + apply schema to test DB
bash scripts/sync-schema-to-do.sh

# 6. Dump local postgres → restore production DB + verify row counts
#    Run before cutover while local postgres is still running
bash scripts/sync-data-to-do.sh

# 7. Seed BCP 47 / ISO 15924 lookup tables (once per fresh DB)
uv run --group seed scripts/seed_locales_scripts.py

# 8. Cutover — see docs/RUNBOOK_DB_MIGRATION.md for the maintenance window steps
```

### Re-running after infrastructure changes

```bash
terraform -chdir=infra/terraform apply
bash scripts/write-db-secrets.sh   # if credentials changed
```

### Credential files (gitignored)

| File | Contents |
|---|---|
| `infra/terraform/terraform.tfvars` | `do_token`, `allowed_external_ips` |
| `infra/terraform/backend.hcl` | `access_key`, `secret_key` (DO Spaces) |

## Service Management

Production runs on port 8000 under systemd.

```bash
# Status
sudo systemctl status power-map

# Restart after code changes
sudo systemctl restart power-map

# Tail logs
sudo journalctl -u power-map -f

# Install (first time or after updating infra/power-map.service)
sudo cp infra/power-map.service /etc/systemd/system/power-map.service
sudo systemctl daemon-reload
sudo systemctl enable --now power-map
```

## Deploy (after merging to main on the VM)

Schema is applied automatically on every `systemctl restart` via `ExecStartPre=bash scripts/apply-schema.sh`.
A bare restart is sufficient for both code-only and schema changes.

```bash
git pull                              # pull merged commits
sudo systemctl restart power-map     # applies schema then starts server
sudo journalctl -u power-map -f      # watch startup; schema errors surface here
```

If `infra/power-map.service` changed in the pull, reinstall the unit first (see § Service Management —
"Install (first time or after updating infra/power-map.service)") before restarting.

To apply schema without restarting (e.g. after a manual `git pull` mid-session):

```bash
bash scripts/apply-schema.sh
```

**Note:** `apply-schema.sh` uses `MIGRATIONS_DATABASE_URL` (DDL privileges). `systemctl restart`
loads this from `EnvironmentFile=/etc/power-map/.env` automatically; standalone invocation
requires `/etc/power-map/.env` to be present (the script loads it via `--env-file`).

## Development

Dev server runs on port 8001 with `--reload`. Always run from a git worktree — never the main checkout.
Accessible via exe.dev proxy at `https://power-map.exe.xyz:8001/`.

```bash
# Build --env-file flags (see § Environment)
env_args=()
[ -f /etc/power-map/.env ] && env_args+=(--env-file /etc/power-map/.env)
[ -f .env ] && env_args+=(--env-file .env)

# Kill any existing dev server on 8001, then start fresh from your worktree
fuser -k 8001/tcp 2>/dev/null; sleep 1
uv run "${env_args[@]}" uvicorn src.api.main:app --host 0.0.0.0 --port 8001 --reload

# Inject admin auth headers locally via mitmdump reverse proxy (port 3000 → 8001)
mitmdump \
  --mode reverse:http://localhost:8001 \
  --listen-port 3000 \
  --set modify_headers='/~q/X-Exedev-Email/admin@example.com' \
  --set modify_headers='/~q/X-Exedev-Userid/usr_local_dev'
```

## Testing

```bash
# Run all tests (excludes integration)
uv run pytest

# Run with coverage
uv run pytest --cov

# Run a specific file
uv run pytest tests/path/to/test_file.py --no-cov

# Run integration tests (hits live external services)
uv run pytest -m integration
```

## JS Testing

```bash
# Run JS tests (one-shot)
npm run test:js

# Run JS tests in watch mode
npm run test:js:watch
```

Note: Node ≥22 required. `npm install` first if `node_modules/` is absent.
Uses vitest v2 + happy-dom. happy-dom was chosen over jsdom historically
due to a CJS/ESM incompatibility in jsdom v29 on Node 18; kept on happy-dom
for speed.

## JS Linting & Formatting

```bash
# Lint JS (ESLint)
npm run lint:js

# Auto-fix lint issues
npm run lint:js:fix

# Format JS (Prettier)
npm run format:js

# Check formatting without writing
npm run format:js:check
```

ESLint config: `eslint.config.js` (flat config). Targets: `src/static/admin/` (browser globals, `no-eval` warn) and `tests/js/` (`no-eval` off — intentional in IIFE test harness). Requires ESLint ≤9 due to `eslint-plugin-vitest` peer constraint.

## Pre-commit hooks

Hooks run automatically on `git commit`. Covers: ruff, pytest (unit), ESLint, Prettier check, vitest.

```bash
# Install hooks (once per clone)
uv run pre-commit install

# Run all hooks manually against all files
uv run pre-commit run --all-files

# Run a single hook by id
uv run pre-commit run pytest --all-files
```

Hook ids: `ruff`, `pytest`, `eslint`, `prettier`, `vitest`

## Linting

```bash
# Check
uv run ruff check .

# Fix auto-fixable issues
uv run ruff check --fix .
```

## Import

```bash
# Build --env-file flags (see § Environment)
env_args=()
[ -f /etc/power-map/.env ] && env_args+=(--env-file /etc/power-map/.env)
[ -f .env ] && env_args+=(--env-file .env)

# Import Cannabis Observer CSV exports
uv run "${env_args[@]}" python scripts/import_cannabis_observer.py \
    --orgs   data/cannabis_observer/Organizations.csv \
    --people data/cannabis_observer/People.csv \
    --roles  data/cannabis_observer/Roles.csv

# Also run address validation (requires ADDRESS_VALIDATOR_API_KEY)
uv run "${env_args[@]}" python scripts/import_cannabis_observer.py \
    --orgs   data/cannabis_observer/Organizations.csv \
    --people data/cannabis_observer/People.csv \
    --roles  data/cannabis_observer/Roles.csv \
    --validate-addresses

# Options
#   --source-reliability FLOAT  Source reliability score (0.0–1.0, default: 0.8)
#   --imported-by STRING        Importer label (default: cannabis-observer-csv-import)
#   --validate-addresses        Also call /validate for deliverability confirmation
```

## Deduplication (one-time fix)

```bash
# Build --env-file flags (see § Environment)
env_args=()
[ -f /etc/power-map/.env ] && env_args+=(--env-file /etc/power-map/.env)
[ -f .env ] && env_args+=(--env-file .env)

# Dry run — report what would be removed (no DB changes)
uv run "${env_args[@]}" python -m scripts.deduplicate_roles

# Execute — apply deduplication and commit changes
uv run "${env_args[@]}" python -m scripts.deduplicate_roles --execute
```

Run before re-applying schema on a dirty DB (see bootstrap sequence in AGENTS.md).

## Seed BCP 47 / ISO 15924 lookup tables (after schema apply, idempotent)

```bash
# Build --env-file flags (see § Environment)
env_args=()
[ -f /etc/power-map/.env ] && env_args+=(--env-file /etc/power-map/.env)
[ -f .env ] && env_args+=(--env-file .env)

# Populate bcp47_locales + iso15924_scripts from langcodes + pycountry.
# Idempotent — safe to re-run to pick up registry updates.
uv run "${env_args[@]}" --group seed scripts/seed_locales_scripts.py
```

Required after a fresh `apply_schema` on a brand-new DB. The FK on
`person_names.locale` / `.script` is active immediately, so any non-NULL
write fails until this script populates the lookup tables. `apply_schema`
logs a WARNING when either lookup table is empty.

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

## Seed WA legislative roles (idempotent, #263)

Creates the 147 canonical legislative roles (49 Senate + 98 House Position 1/2) against
the already-seeded `legislative_district` jurisdictions. Prerequisites: `apply_schema`
(role_types seeded), the jurisdictions seed above (LD jurisdictions present), and the WA
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

## Archive legacy legislator roles (idempotent, #265)

Validates each active legacy (`role_type_id IS NULL`) legislator assignment on the WA
House / Senate / Legislature orgs against the enriched seat-assignments, migrates its
ancillary data (links + contacts + field_confidence → the matched typed assignment;
`role_wa_pdc` URLs → person-level `person_wa_pdc_filer` identifier + `wa_pdc` link),
then archives the assignment; a legacy role is archived once it has no active
assignments left. Coverage-gated: staff/leadership titles are excluded (→ #266) and
unmatched rows are kept for a later re-run (e.g. after upstream backfills more history).

```bash
# Build --env-file flags (see § Environment)
env_args=()
[ -f /etc/power-map/.env ] && env_args+=(--env-file /etc/power-map/.env)
[ -f .env ] && env_args+=(--env-file .env)

# Dry run — read-only; per-row statuses + full ancillary accounting
uv run "${env_args[@]}" python -m scripts.archive_legacy_legislator_roles

# Execute — migrate ancillary data + archive, in one transaction
uv run "${env_args[@]}" python -m scripts.archive_legacy_legislator_roles --execute
```

## Role-type vocabulary migration + classification (issue #266)

Two one-off migrations, **run in this order**, that move legacy free-text roles onto
the #266 role-type vocabulary. Both are idempotent, dry-run by default, and commit in
a single transaction under `--execute`. Governance rules for the vocabulary itself →
`docs/CONVENTIONS.md` §"Role-type vocabulary — governance".

`scripts/migrate_member_role_type.py` splits the retired coarse `member` classifier
into `committee_member` / `party_member` by **structural org identifier**
(`org_wa_legislature_committee_id` vs `org_wa_party`) — never display names. An org
with neither is reported `skipped` and left untouched. Once no rows reference
`member`, `apply_schema` drops it from the catalog; the script then no-ops.

`scripts/classify_legislative_roles.py` types WA committee / chamber / legislative-staff
roles in four phases: curate title collisions (two spellings of one office are **merged**,
assignments re-pointed not deleted; collision-free variants renamed), classify committee
officeholders (`committee_*`) and committee staff (`legislature_staff`), classify the
legislative staff offices, then apply the enumerated chamber backlog (retitle, re-home,
principal→notes). Titles are preserved wherever normalizing would erase a real
distinction (`Acting Chair` keeps its title *and* takes `committee_chair`). Backlog rules
are org-scoped to the WA chambers — an unscoped title match would sweep in unrelated orgs.
Federal legislative roles and caucus/floor-leadership vocab are deliberately out of scope.

```bash
# Build --env-file flags (see § Environment)
env_args=()
[ -f /etc/power-map/.env ] && env_args+=(--env-file /etc/power-map/.env)
[ -f .env ] && env_args+=(--env-file .env)

# Dry runs — read-only; every planned mutation is listed per row
uv run "${env_args[@]}" python -m scripts.migrate_member_role_type
uv run "${env_args[@]}" python -m scripts.classify_legislative_roles

# Execute, in order, then re-apply schema to drop the emptied `member` type
uv run "${env_args[@]}" python -m scripts.migrate_member_role_type --execute
uv run "${env_args[@]}" python -m scripts.classify_legislative_roles --execute
bash scripts/apply-schema.sh
```

**Dates are never invented (#307):** the classifier moves a tenure embedded in a title
(e.g. `Speaker of the House (2021-23)`) into role notes and logs a WARNING — a human sets
the assignment's dates and currency afterward.

## Speaker Designate / Speaker split (issue #314)

`scripts/split_speaker_designate.py` resolves the human date call the classifier deferred
above for Laurie Jinkins. It splits the single dateless `Speaker of the House` tenure into
two distinct `chamber_leader` roles on WA House — mirroring the COG `Acting Chair` / `Chair`
pattern (the coarse type aggregates, the free-text title distinguishes):

- **Speaker Designate** (new role) — assignment 2019-07-31 → 2020-01-12, `is_current=FALSE`.
- **Speaker of the House** (existing role/assignment) — start 2020-01-13, open end, still
  `is_current=TRUE`; the stale `2021-23` breadcrumb on the role is cleared.

Dates come from the WA House Democrats caucus record, cited in each assignment's `notes`
(not invented, #307). A fail-loud identity guard aborts if the hardcoded prod IDs no longer
resolve to *(Jinkins, Speaker role, WA House)*. Idempotent, dry-run by default, single
transaction under `--execute`.

```bash
env_args=()
[ -f /etc/power-map/.env ] && env_args+=(--env-file /etc/power-map/.env)
[ -f .env ] && env_args+=(--env-file .env)

uv run "${env_args[@]}" python -m scripts.split_speaker_designate            # dry run
uv run "${env_args[@]}" python -m scripts.split_speaker_designate --execute  # commit
```

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

## Person canonical-name backfill (issue #308)

`scripts/backfill_person_canonical_names.py` promotes one eligible public name
for every person that has names but no canonical one — those people render
blank, since `v_person_display_names` only surfaces canonical rows. One-off
repair for drift produced before #308b gave `write_names` first-wins
auto-promotion on the person branch.

Each repairable person gets the one name PM would display, chosen by the
priority ladder shared with the observation-path heal via
`name_type_priority_sql()` (see `NO_AUTO_CANONICAL_NAME_TYPES` for what is
never eligible). People carrying several eligible names are resolved, not
deferred — the heal pass would promote the same row on their next observation
anyway. `multi_name` reports how many were decided that way.

There is no `blocked` bucket any more (#308 Option A): `uq_person_canonical_name`
is keyed on `(person_id)` alone and `chk_person_canonical_is_public` guarantees a
canonical row is public, so a non-public row can no longer occupy a person's
display slot. A person whose only names are ineligible (deadname/mrz-only, or
nothing public) is simply not a candidate and stays deliberately blank until a
human adds a displayable name.

```bash
env_args=()
[ -f /etc/power-map/.env ] && env_args+=(--env-file /etc/power-map/.env)
[ -f .env ] && env_args+=(--env-file .env)

uv run "${env_args[@]}" python -m scripts.backfill_person_canonical_names
uv run "${env_args[@]}" python -m scripts.backfill_person_canonical_names --execute
```

Idempotent — a second run finds nothing. Each promotion touches `person_names` →
`trg_touch_person_on_name_change` → an `entity_changes` `'updated'` row, so
subscribers re-fetch and pick up the newly-visible name. Run **after**
`bash scripts/apply-schema.sh`, so the #308a view change is live first.

## Org-lifecycle assignment audit (issue #307)

`scripts/audit_org_lifecycle_assignments.py` checks every non-archived
assignment against its org's lifespan (`v_org_lifespan.ended_on`, derived from
`dissolved`/`merged_with` entity events — see `docs/CONVENTIONS.md`
§ "Org lifespan bounds on assignments"). Categories:

- `current_on_ended` — auto-fixable; `--execute` closes at `ended_on`
  (`is_current=FALSE`, provenance note appended to `notes`)
- `end_after_ended` / `start_after_ended` — dated contradictions, report-only
- `unknown_end_on_ended` — unknown end left open, report-only
- `missing_end_event` — inactive/archived org with open assignments but no end
  event; record a `dissolved`/`merged_with` event in admin, then re-run

```bash
env_args=()
[ -f /etc/power-map/.env ] && env_args+=(--env-file /etc/power-map/.env)
[ -f .env ] && env_args+=(--env-file .env)

uv run "${env_args[@]}" python -m scripts.audit_org_lifecycle_assignments            # report
uv run "${env_args[@]}" python -m scripts.audit_org_lifecycle_assignments --execute  # close
```

Idempotent — a compliant DB yields no findings and `--execute` is a no-op.

## Org end-event backfill (issue #313)

`scripts/backfill_313_org_end_events.py` resolves the nine `missing_end_event`
orgs the #307 audit surfaced, using human-researched end dates (see issue #313).
For five defunct orgs it records a `dissolved` event and closes **all** their
open assignments at `ended_on` — including the `unknown_end_on_ended` rows the
audit's `--execute` deliberately leaves open (a human-authorized close, not an
invented end). `start_after_ended` rows are still left open (closing would
invert the window) and logged as a WARNING. Kalytera (renamed to Claritas) is
reactivated rather than dissolved; the name swap is done by hand in admin.

Org ids and dates are baked into the module (`END_EVENTS`, `KALYTERA_ID`); an
id that doesn't resolve is skipped with a WARNING, never an orphan event.

```bash
env_args=()
[ -f /etc/power-map/.env ] && env_args+=(--env-file /etc/power-map/.env)
[ -f .env ] && env_args+=(--env-file .env)

uv run "${env_args[@]}" python -m scripts.backfill_313_org_end_events            # report
uv run "${env_args[@]}" python -m scripts.backfill_313_org_end_events --execute  # apply
```

Idempotent — skips orgs that already carry a lifespan event; re-closing an
already-closed assignment is a no-op. Re-run the #307 audit afterward to confirm.

## Duplicate-assignment audit (issue #311)

`scripts/audit_assignment_duplicates.py` finds overlapping active assignment
pairs for the same `(person, role)` — the duplicates minted when a producer's
start_date correction missed the match key pre-#311 (see `docs/CONVENTIONS.md`
§ "Assignment observations — update semantics & provenance"). Categories:

- `deepened_start` — wider (earlier-start) row created later: the producer-
  correction signature; auto-merged by `--execute`
- `subsumed` — wider row provably covers the narrower; auto-merged
- `overlapping_review` — coverage unprovable (e.g. unknown end), report-only

Merge = links/contact methods/addresses/identifiers move to the survivor
(would-be duplicates stay on the orphan), notes concatenate, orphan is
**archived** with a provenance note (never deleted). The archive UPDATE hits
the `entity_changes` outbox so subscribed producers drop stale anchors.
Undated tenures and disjoint terms (returning legislators) are never flagged.

```bash
env_args=()
[ -f /etc/power-map/.env ] && env_args+=(--env-file /etc/power-map/.env)
[ -f .env ] && env_args+=(--env-file .env)

uv run "${env_args[@]}" python -m scripts.audit_assignment_duplicates            # report
uv run "${env_args[@]}" python -m scripts.audit_assignment_duplicates --execute  # merge
```

Idempotent — merged pairs leave the audit's scope (archived rows are ignored).

## Per-key API anomaly check (issue #294)

`scripts/check_api_anomalies.py` queries `api_request_log` for the trailing hour,
grouped per API key, and logs a journal `WARNING` for every key at/above the
threshold (default 5000/hr; env `API_ANOMALY_HOURLY_THRESHOLD`; `<= 0` disables).
Exits 3 when anomalous — distinct from argparse usage errors (exit 2) — so the
systemd unit shows failed (`systemctl --failed`; future
`OnFailure=` hook). The threshold is deliberately **below** the rate-limit
ceiling (2 workers × 2/s ≈ 14.4k/hr) — the 2026-07-11 runaway ran at ~17.5k/hr,
so a "well above ceiling" threshold would have missed it. Human-facing layer:
Admin → Activity → API Requests per-key panel.

Manual run:

```bash
# Build --env-file flags (see § Environment)
env_args=()
[ -f /etc/power-map/.env ] && env_args+=(--env-file /etc/power-map/.env)
[ -f .env ] && env_args+=(--env-file .env)

uv run "${env_args[@]}" python -m scripts.check_api_anomalies
uv run "${env_args[@]}" python -m scripts.check_api_anomalies --threshold 1000
```

Scheduled (production): an hourly systemd timer. Install / update:

```bash
sudo cp infra/power-map-anomaly.service infra/power-map-anomaly.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now power-map-anomaly.timer

# Inspect
systemctl list-timers power-map-anomaly.timer    # next/last run
sudo systemctl start power-map-anomaly.service   # run once, now
sudo journalctl -u power-map-anomaly -f          # WARNINGs per anomalous key
```

## Schema-parity audit (issues #315, #331)

`scripts/audit_schema_constraint_parity.py` snapshots every **constraint**
(`pg_get_constraintdef`), **function** (`pg_get_functiondef`), and **trigger**
(`pg_get_triggerdef`) on a reference DB (`--reference-url`, default
`PARITY_REFERENCE_URL` → `TEST_DATABASE_URL`) and on prod (`--target-url`,
default `DATABASE_URL`), and exits 3 when prod is missing or disagrees on any
reference object — the `CREATE TABLE IF NOT EXISTS` inline-constraint drift class
(#307/#312 CHECKs, #315's FK `ON DELETE` action) plus the `CREATE OR REPLACE`
function/trigger body-drift window (#331; the change-feed `touch_parent_*` /
`trg_touch_entity_*` surface). Compares the **full def**, not just presence, so
FK actions, CHECK bodies, and function/trigger bodies are in scope. The
per-kind report namespaces objects `constraint.*` / `function.*` / `trigger.*`.
Read-only; catches drift from any source (manual DDL, partial migration, a
hand-applied hotfix, a deploy whose `apply_schema` no-op'd a new inline
constraint).

Function/trigger defs are PG-version-formatted, so on a **PG major mismatch**
between reference and target those two kinds are skipped (loud WARNING) rather
than misreported as drift; constraints are version-stable and always diff. Keep
the reference on prod's major (point `PARITY_REFERENCE_URL` at a same-major DB).
See `docs/CONVENTIONS.md` §"Unique Indexes" for why a fresh-DB-only unit guard
can't replace it.

Manual run:

```bash
# Build --env-file flags (see § Environment)
env_args=()
[ -f /etc/power-map/.env ] && env_args+=(--env-file /etc/power-map/.env)
[ -f .env ] && env_args+=(--env-file .env)

uv run "${env_args[@]}" python -m scripts.audit_schema_constraint_parity
# Gold-standard reference: a scratch DB freshly built from empty via apply_schema
uv run "${env_args[@]}" python -m scripts.audit_schema_constraint_parity --reference-url "$SCRATCH_URL"
```

Scheduled (production): a daily systemd timer. Install / update:

```bash
sudo cp infra/power-map-schema-parity.service infra/power-map-schema-parity.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now power-map-schema-parity.timer

# Inspect
systemctl list-timers power-map-schema-parity.timer    # next/last run
sudo systemctl start power-map-schema-parity.service   # run once, now
sudo journalctl -u power-map-schema-parity -f          # drift report on failure
```

## role / role_assignment ancillary orphan audit & cleanup (issues #324, #326)

Polymorphic ancillary keyed on `(entity_type, entity_id)` with no FK — for
`role_assignment` (`links` / `contact_methods` / `field_confidence` /
`identifiers`, #324) and for `role` (`links` / `contact_methods`, #326) — could be
orphaned when a merge or delete drops the parent. The merge/delete paths now
re-home (or drop) before deleting; `scripts/audit_ancillary_orphans.py` is the
continuous guard over **both** scopes (breakdown namespaced `role.*` /
`role_assignment.*`). The one-time recovery script stays role_assignment-only
(its heuristics are assignment-specific; role orphans should not occur now that
the write paths are fixed, so any that appear go to manual triage). See
`docs/CONVENTIONS.md` §"Merge dedup — role_assignment ancillary re-homing".

```bash
# Build --env-file flags (see § Environment)
env_args=()
[ -f /etc/power-map/.env ] && env_args+=(--env-file /etc/power-map/.env)
[ -f .env ] && env_args+=(--env-file .env)

# Guard: count orphans (exit 3 if any) — read-only
uv run "${env_args[@]}" python -m scripts.audit_ancillary_orphans

# Cleanup: heuristic re-home + redundant-link purge; manual rows reported only
uv run "${env_args[@]}" python -m scripts.cleanup_role_assignment_ancillary_orphans            # dry run
uv run "${env_args[@]}" python -m scripts.cleanup_role_assignment_ancillary_orphans --execute  # supervised
```

Scheduled (production): a daily audit timer. Install / update:

```bash
sudo cp infra/power-map-ancillary-orphans.service infra/power-map-ancillary-orphans.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now power-map-ancillary-orphans.timer

# Inspect
systemctl list-timers power-map-ancillary-orphans.timer    # next/last run
sudo systemctl start power-map-ancillary-orphans.service   # run once, now
sudo journalctl -u power-map-ancillary-orphans -f          # orphan breakdown on failure
```

## Git Submodules

```bash
# Init after cloning
git submodule update --init --recursive

# Force-refresh vendor skills
git submodule update --remote --merge skills-vendor/gregoryfoster-skills skills-vendor/obra-superpowers
```
