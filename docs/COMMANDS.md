# Common Commands

Everyday commands: setup, env files, provisioning, deploy, the dev loop, linting, and
the scheduled timers. Test commands are in `docs/TESTING.md`; one-off seeds, backfills,
migrations in `docs/RUNBOOKS.md` and the scheduled audits in `docs/AUDITS.md`.

---

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

---

## Environment


Two env files; both feed `uv run` via `--env-file`. uv has a proper dotenv parser and respects key/value quoting, unlike `cat … | xargs`. The flags are gated on existence because uv errors hard on a missing `--env-file`.

```bash
# Build --env-file flags once; reuse across uv run invocations below
env_args=()
[ -f /etc/power-map/.env ] && env_args+=(--env-file /etc/power-map/.env)
[ -f .env ] && env_args+=(--env-file .env)
uv run "${env_args[@]}" <cmd>
```

Later files win on conflicting keys.

### Environment Variables

| File | Owner | Contents |
|---|---|---|
| `/etc/power-map/.env` | root:exedev (640) | `DATABASE_URL`, `MIGRATIONS_DATABASE_URL`, `TEST_DATABASE_URL`, `ADDRESS_VALIDATOR_API_KEY`, `ADDRESS_VALIDATOR_RUN_VALIDATION` (default false; true → calls `/validate` for validation status, false → `/standardize` only), `ADDRESS_VALIDATOR_BASE_URL` (optional; defaults to `https://address-validator.exe.xyz:8000`; override to point at dev server on port 8001), `DB_POOL_MIN_SIZE` (default 2), `DB_POOL_MAX_SIZE` (default 5; tune per DO tier connection limit), `API_REQUEST_LOG_MAX_PENDING` (optional; default 50; soft cap on in-flight fire-and-forget `api_request_log` capture writes before shedding — #290; tune relative to `DB_POOL_MAX_SIZE`; `0` disables capture entirely), `RATE_LIMIT_READ_PER_S` / `RATE_LIMIT_READ_BURST` / `RATE_LIMIT_WRITE_PER_S` / `RATE_LIMIT_WRITE_BURST` (optional; defaults 2/120/1/60; per-key token buckets on the public API — #292; read = GET/HEAD + read-semantic POSTs (`identify`, `verify`, `verify-batch`, `embeddings/presence`) — routes declare `openapi_extra={BUCKET_EXTRA_KEY: "read"}` and `src.api.main` installs the derived path set at app build (#310); per-worker, so effective ceiling ≈ workers × rate; refill ≤ 0 disables that bucket), `API_KEY_LAST_USED_DEBOUNCE_S` (optional; default 60; min seconds between `api_keys.last_used_at` stamps per worker — #292; `0` stamps every request), `API_ANOMALY_HOURLY_THRESHOLD` (optional; default 5000; requests per key per hour at/above which the hourly anomaly check WARNs — #294; deliberately below the ~14.4k/hr rate-limit ceiling so near-ceiling runaways are caught; `<= 0` disables the check and the admin hot-row highlighting), `PARITY_REFERENCE_URL` (optional; reference DB for the daily schema-parity audit — #315/#331; falls back to `TEST_DATABASE_URL`; point at a scratch DB freshly built from empty for a gold-standard run — and keep it on prod's PG major, else the function/trigger diff self-skips) |
| `.env` (repo, gitignored) | developer | `GH_TOKEN` |

`POWER_MAP_ENV_FILE` (optional, **test-only**, #398) redirects where `apply-schema.sh` reads its
DSN fallback from, so the guard tests never see the real `/etc/power-map/.env`. Never set it in
production.

---

## Provisioning


One-time setup for the DO managed PostgreSQL cluster (`co-pm-db-1`, sfo3). State is stored in the `co-pm-spaces-1` DO Spaces bucket.

### Prerequisites

- `terraform` ≥1.9 installed (see <https://developer.hashicorp.com/terraform/install>)
- `infra/terraform/terraform.tfvars` and `infra/terraform/backend.hcl` — both gitignored, both **rebuilt from `/etc/power-map/.env`** by `uv run python -m scripts.write_terraform_credentials` (#409). Never hand-write them.
- `jq`, `psql`, `python3` on PATH (used by `write-db-secrets.sh`)

### First-time provisioning

```bash
# 0. Rebuild the two gitignored credential files from /etc/power-map/.env (#409).
#    Reads the live Trusted Sources from the DO API, so allowed_external_ips
#    matches reality rather than a guess. Writes 0600; logs paths, never values.
uv run python -m scripts.write_terraform_credentials

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
#    Writes; omit --execute to preview first (#402)
uv run --group seed scripts/seed_locales_scripts.py --execute

# 8. Cutover — see docs/RUNBOOK_DB_MIGRATION.md for the maintenance window steps
```

### Re-running after infrastructure changes

```bash
terraform -chdir=infra/terraform apply
bash scripts/write-db-secrets.sh   # if credentials changed
```

### Credential custody (#409)

The two terraform files are **derived**, never authored. Their source of truth is
`/etc/power-map/.env` (root-owned, `0640`, group `exedev`), beside the database
credentials:

| Secret in `/etc/power-map/.env` | Lands in | As |
|---|---|---|
| `DO_API_TOKEN` | `infra/terraform/terraform.tfvars` | `do_token` |
| *(read from the DO API)* | `infra/terraform/terraform.tfvars` | `allowed_external_ips` |
| `DO_SPACES_KEY` | `infra/terraform/backend.hcl` | `access_key` |
| `DO_SPACES_VALUE` | `infra/terraform/backend.hcl` | `secret_key` |

Rebuild both at any time — idempotent, `0600`, secret values never logged:

```bash
uv run python -m scripts.write_terraform_credentials              # live allowlist from the DO API
uv run python -m scripts.write_terraform_credentials --allowed-ips 1.2.3.4,5.6.7.8   # explicit
```

`scripts/write-db-secrets.sh` preflights all three artefacts and exits 2 with this
pointer rather than failing inside `terraform output`. **Remote state lives in the
`co-pm-spaces-1` Spaces bucket and is not at risk from losing these files** — they
are only the keys to reach it. Losing them in 2026-08 cost console-only allowlist
edits (which then drift) and a blocked credential rotation, not data.

---

## Service Management


Production runs on port 8000 under systemd.

```bash
# Status
sudo systemctl status power-map

# Restart after code changes
sudo systemctl restart power-map

# Tail logs
sudo journalctl -u power-map -f

# Quick health check (#343) — liveness (process up) + readiness (DB pool);
# exit non-zero on failure via -f. /ready 503 body carries a reason slug:
# no_pool | pool_timeout | db_error.
curl -fsS localhost:8000/health && curl -fsS localhost:8000/ready

# Install (first time or after updating infra/power-map.service)
sudo cp infra/power-map.service /etc/systemd/system/power-map.service
sudo systemctl daemon-reload
sudo systemctl enable --now power-map
```

---

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

To apply schema without restarting (e.g. after a manual `git pull` mid-session) — **from the
main checkout, on `main`**:

```bash
bash scripts/apply-schema.sh
```

**Note:** `apply-schema.sh` uses `MIGRATIONS_DATABASE_URL` (DDL privileges). `systemctl restart`
loads this from `EnvironmentFile=/etc/power-map/.env` automatically; a standalone invocation
reads the same file directly, so `/etc/power-map/.env` must be present.

### Guards (#398)

The bare invocation writes to **production**. Guards, all skipped by `--yes`:

| Shape | Behaviour |
|---|---|
| Linked git worktree | **refuses**, exit 2 — nothing applied; points at `--test` |
| Interactive (TTY) | prompts for the database name before applying (or the word `production`, when the DSN yields no database name) |
| Tracked modifications / branch ≠ `main` | WARNING only — never blocks a restart (untracked files are ignored) |

Every run echoes its target first — `target: user@host:port/db (PRODUCTION)` plus the checkout,
branch and SHA — so a mistaken run is visible in scrollback and in the journal. The echo is
best-effort by design: a DSN that is not a parseable URL is reported as
`(unparsed DSN — cannot redact)` and never printed, since it would carry the password, and a
missing `python3` degrades the same way rather than failing the run.

The guards read the checkout that owns the script, not the caller's cwd — the script `cd`s to its
own repo root first, so the tree it reports is the tree whose `schema.sql` it applies. A git that
cannot report its worktree layout (or a directory that is not a checkout) announces the guard as
unavailable and proceeds, rather than ending a restart on an environmental quirk.

| Flag | Effect |
|---|---|
| `--test` | target `TEST_DATABASE_URL` instead; allowed anywhere, never prompts |
| `--yes`, `-y` | skip the production guards. `ExecStartPre` never passes this — the unit's invocation satisfies the guards instead of skipping them |
| `--dry-run` | run the guards, echo the target, stop without applying |
| `--help`, `-h` | usage on stdout, exit 0 |

Exit codes: `0` applied (or dry run), `1` usage/configuration error, `2` guard refusal.

Never add a guard that the systemd shape (main checkout, no TTY, no flags) can trip:
`apply-schema.sh` is `ExecStartPre`, so a non-zero exit means the service does not start. That
covers the diagnostics too — the target echo must degrade rather than abort.

`scripts/sync-schema-to-do.sh` delegates its test-database apply to `apply-schema.sh --test`.

---

## Development


Dev server runs on port 8001 with `--reload`. Always run from a git worktree — never the main checkout.
Accessible via exe.dev proxy at `https://power-map.exe.xyz:8001/`.

### Worktree setup (run once, right after `worktree-create.sh`)

```bash
bash scripts/worktree-setup.sh <worktree-path>   # default: current directory
```

Gives the worktree **its own** `.venv` (`uv sync --group browser --group seed --group mapping`)
and **its own** `node_modules` (`npm ci`, skipped when `node_modules/.bin` is already there), initialises
the `skills-vendor/` submodules, and symlinks the gitignored `.env` and
`data/cannabis_observer` from the main checkout. Refuses (exit 2) against the main checkout.

The rest exist so a worktree's first test run matches the main checkout's (#482).
`git worktree add` populates tracked files only: the submodule directories arrive empty, so
`tests/test_vendor_skills.py`'s vendored-driver guards fail, and `data/cannabis_observer`
is absent, so `test_seed_jurisdictions.py::test_load_seed_file_actual_wa_file` skips — a
pass fewer than main on an identical tree. All three are non-fatal warnings when the source
is unreachable, because a briefed baseline count is only useful if the provisioning is not
the variable.

`node_modules` is the JS half of the same argument, and it fails harder (#554): `vitest`,
`bats`, `eslint` and `prettier` are all devDependencies resolved through
`node_modules/.bin`, so an unprovisioned worktree does not report a skip — its first
`git commit` is **refused**, `vitest: not found`, exit 127, after the work is done and the
suite is green. (`eslint` and `prettier` are gated on `\.js$`, so a JS-touching commit is
refused before `vitest` is reached at all.) A worktree under
`<main>/.worktrees/` can look exempt because `npm run` prepends `node_modules/.bin` for
every *ancestor* directory and borrows the main checkout's; that is the shared-mutable-
environment trap below in another costume, and it disappears the moment `WORKTREE_ROOT`
points outside the repo. `npm ci` rather than `npm install` for the reason `uv sync` beats
`uv run`: lockfile-exact, not re-resolved.

`worktree-create.sh` (vendored skill) links a new worktree's `.venv` at the main checkout's —
and the main checkout is production's working directory. Nine `power-map*` units run `uv run`
there, `power-map-ready` every two minutes; `uv run` reinstalls the project, so a shared venv
gets its version metadata restamped to main's mid-suite, and `power-map.service`'s
`ExecStartPre=uv sync` prunes the opt-in groups outright (#450). With a warm uv cache the
per-worktree sync takes under a second and hardlinks into `~/.cache/uv`, so the link never
paid for the shared mutable state. It also means a worktree's dependency changes can no
longer mutate the environment the live uvicorn workers import from.

```bash
# Build --env-file flags (see § Environment)
env_args=()
[ -f /etc/power-map/.env ] && env_args+=(--env-file /etc/power-map/.env)
[ -f .env ] && env_args+=(--env-file .env)

# Kill any existing dev server on 8001, then start fresh from your worktree
fuser -k 8001/tcp 2>/dev/null; sleep 1
uv run "${env_args[@]}" uvicorn src.api.main:app --host 0.0.0.0 --port 8001 --reload --log-config src/core/log_config.json

# Inject admin auth headers locally via mitmdump reverse proxy (port 3000 → 8001)
mitmdump \
  --mode reverse:http://localhost:8001 \
  --listen-port 3000 \
  --set modify_headers='/~q/X-Exedev-Email/admin@example.com' \
  --set modify_headers='/~q/X-Exedev-Userid/usr_local_dev'
```

### Mapping project (dbt-duckdb, #497)

The `mapping` group is synced by `worktree-setup.sh`. The models' tests build the
project against fixtures and run in the unit tier; the real-snapshot check needs
the store provisioned in that checkout:

```bash
uv run --group mapping pytest tests/core/ingestion/mapping        # fixture tier (~20 builds, ~60s)
uv run "${env_args[@]}" python -m scripts.pull_datasets            # provision the store …
uv run --group mapping "${env_args[@]}" python -m scripts.export_pm_tables   # … and the export
uv run --group mapping "${env_args[@]}" pytest tests/core/ingestion/mapping/test_real_snapshot.py -m integration
uv run --group mapping "${env_args[@]}" python -m scripts.build_desired_state # the operator run
uv run --group mapping "${env_args[@]}" python -m scripts.apply_desired_state # the dry run (#499); --execute is gated
```

`export_pm_tables` is read-only but connects (DSN echoed, `--test` available);
`build_desired_state` never opens a database; `apply_desired_state` reads live
Postgres and, only under `--execute` after a clean streak, writes. Runbook:
`docs/RUNBOOK_DESIRED_STATE.md`.

### Applying a schema change during development

From a worktree, apply to the **test** database — the bare command targets production and
refuses to run here (#398):

```bash
bash scripts/apply-schema.sh --test
```

Uses `TEST_DATABASE_URL`, the same DB the integration suite applies the schema to
(`tests/conftest.py`). Production picks the change up on the next `systemctl restart` after merge.

---

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

---

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

Hook ids: `ruff`, `pytest`, `eslint`, `prettier`, `vitest`, `bats`, `shellcheck`

Ship gate: `bash skills/shipping-work-python-fastapi/scripts/pre-ship.sh` — the
vendored gate, given `--group seed` by `.skills/pre-ship-uv-args` (#549) →
[TESTING.md](TESTING.md) § Ship gate.

---

## Linting


```bash
# Check
uv run ruff check .

# Fix auto-fixable issues
uv run ruff check --fix .
```

---

## Operational script targets (#399)

Every script in `scripts/` that opens a connection takes the same two flags and
echoes a labelled target to stderr before connecting:

```bash
uv run python -m scripts.<name>                      # DATABASE_URL — production
uv run python -m scripts.<name> --test               # TEST_DATABASE_URL
uv run python -m scripts.<name> --database-url DSN   # somewhere else
```

```
target: co_pm_db_production_user@co-pm-db-1-….ondigitalocean.com:25060/co_pm_db_production (production)
```

`--test` **hard-errors** when `TEST_DATABASE_URL` is unset rather than falling
back to production, and choosing *where* is never choosing *whether* — a script
that writes still needs `--execute`. The label's keying, the remaining rules and
the sweep that enforces them → [RUNBOOKS.md](RUNBOOKS.md) §"Operational scripts".

---

## Git Submodules


```bash
# Init after cloning
git submodule update --init --recursive

# Force-refresh vendor skills
git submodule update --remote --merge skills-vendor/gregoryfoster-skills skills-vendor/obra-superpowers
```

---

## Claude Code on this host (#542)

One runtime for every client: `/usr/local/bin/claude`, a root-owned link into `/usr/local/lib/claude/versions/<v>`. `~/.local/bin/claude` and each VS Code extension's bundled `native-binary/claude` link to it. Updates are manual and need root. The native installer never moves a `~/.local/bin/claude` that doesn't already point into its own `versions/`, so the user auto-updater is switched off (`DISABLE_AUTOUPDATER`).

```bash
bash scripts/claude-system.sh                             # dry run: layout + plan
sudo bash scripts/claude-system.sh --execute              # install latest, relink everything
sudo bash scripts/claude-system.sh --execute --version stable   # or a pinned X.Y.Z
```

The SessionStart hook `.claude/hooks/claude-canonical-check.sh` warns when any link drifts (an extension update unpacks a fresh bundled binary) or when the system version is over 14 days old. It never repairs. `--execute` refuses a downgrade (exit 1) and keeps the current and previous version: to roll back, `sudo ln -sfn /usr/local/lib/claude/versions/<prev> /usr/local/bin/claude`.

---

## Scheduled timers

Every unit below reports failure through `systemctl --failed`, and each one that
can alert opens a GitHub issue and closes it on recovery — the alert state lives
in the issue, never in a local file. **The per-script detail is in the doc each
row names**; this is a roster, not a reference.

| Timer | Cadence | Runs | Detail |
|---|---|---|---|
| `power-map-ready` | 2 min | `check_ready.py` — probes `/ready`; exit 3 only when two attempts fail | [AUDITS.md](AUDITS.md) |
| `power-map-egress-ip` | 5 min | `check_egress_ip.py` — egress IP vs the cluster's live Trusted Sources | [AUDITS.md](AUDITS.md) |
| `power-map-anomaly` | hourly | `check_api_anomalies.py` — per-key request-rate threshold | [AUDITS.md](AUDITS.md) |
| `power-map-datasets-pull` | 09:00 UTC | `pull_datasets.py` — lands usa-wa snapshots; writes no DB rows | [RUNBOOKS.md](RUNBOOKS.md) |
| `power-map-desired-state` | 09:30 UTC | export → build → apply, the applier **dry run**; a failed step stops the chain | [RUNBOOK_DESIRED_STATE.md](RUNBOOK_DESIRED_STATE.md) |
| `power-map-prune` | daily | `prune_outbox.py --execute` — outbox/tombstone TTL | [RUNBOOKS.md](RUNBOOKS.md) |
| `power-map-schema-parity` | daily | `audit_schema_constraint_parity.py` — prod vs reference DDL | [AUDITS.md](AUDITS.md) |
| `power-map-ancillary-orphans` | daily | `audit_ancillary_orphans.py` — no-FK polymorphic orphans | [AUDITS.md](AUDITS.md) |
| `power-map-assignment-rel-windows` | daily | `audit_assignment_relationship_windows.py` — drifted RA→RA edge windows | [AUDITS.md](AUDITS.md) |
| `power-map-a11y` | Sun 04:00 UTC | `run-a11y-sweep.sh` — both a11y tiers against the test DB | [TESTING.md](TESTING.md) |

An audit timer exits **3** on a finding, which is what puts it in
`systemctl --failed`; exit 1 is a real error. Installing or inspecting any of
them takes the same shape:

```bash
sudo cp infra/<unit>.service infra/<unit>.timer /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now <unit>.timer

systemctl list-timers <unit>.timer     # next / last run
sudo systemctl start <unit>.service    # run once, now
sudo journalctl -u <unit> -f           # findings
```

---

## Operational script safety

Scripts that write are dry run by default and gate the write behind `--execute`;
every script that connects echoes a labelled target first. The rules, the
resolver and the no-allowlist AST sweep that enforces them →
[RUNBOOKS.md](RUNBOOKS.md) §"Operational scripts". `apply-schema.sh`'s own
guards are in § Deploy → Guards (#398) above.
