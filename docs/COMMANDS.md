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

- `--test` **hard-errors** when `TEST_DATABASE_URL` is unset — it never falls
  back to `DATABASE_URL`.
- `--test` together with `--database-url` is a usage error.
- The label keys on `(host, port, dbname)`, so the migrations DSN labels
  `production` too. An unrecognised target reads `unknown — assume production`.
- A script that writes still needs `--execute`; the flags above only choose
  *where*, never *whether*.

Enforced by `tests/scripts/test_dsn_sweep.py` (AST, no allowlist). Full rules →
`docs/CONVENTIONS.md` §"Operational scripts — dry run by default & target echo".

---

## Git Submodules


```bash
# Init after cloning
git submodule update --init --recursive

# Force-refresh vendor skills
git submodule update --remote --merge skills-vendor/gregoryfoster-skills skills-vendor/obra-superpowers
```

---

## Scheduled timers


Every unit below reports failure through `systemctl --failed`; the per-script
detail is in the section named in each row.

| Situation | Action |
|---|---|
| Outbox/tombstone TTL prune | daily `power-map-prune.timer` runs `scripts/prune_outbox.py --execute` (90-day window, `entity_changes` + `deleted_entities`); see `docs/RUNBOOKS.md` |
| Per-key API anomaly check | hourly `power-map-anomaly.timer` runs `scripts/check_api_anomalies.py` — journal WARNING + exit 3 per key ≥ `API_ANOMALY_HOURLY_THRESHOLD` req/hr (#294); human layer = Admin → Activity → API Requests per-key panel; see `docs/AUDITS.md` |
| Schema-parity audit | daily `power-map-schema-parity.timer` runs `scripts/audit_schema_constraint_parity.py` — snapshots full `pg_get_constraintdef` + `pg_get_functiondef` + `pg_get_triggerdef` on reference (`PARITY_REFERENCE_URL`, default `TEST_DATABASE_URL`) vs prod, exit 3 on any missing/different object, per-kind breakdown `constraint.*`/`function.*`/`trigger.*` (#315 constraints + #331 functions/triggers; `CREATE TABLE IF NOT EXISTS` inline-drift + `CREATE OR REPLACE` body-drift; extension-owned/internal excluded; function/trigger diff skipped on a PG-major mismatch); see `docs/AUDITS.md` |
| role / role_assignment / citation ancillary orphan audit | daily `power-map-ancillary-orphans.timer` runs `scripts/audit_ancillary_orphans.py` — anti-join count of no-FK polymorphic ancillary keyed on a non-existent parent, over **three** scopes: `role_assignment` (`links`/`contact_methods`/`field_confidence`/`identifiers`, #324), `role` (`links`/`contact_methods`, #326), and `citation` (all 7 citable entity types, #319); exit 3 on any orphan, breakdown namespaced `role.*`/`role_assignment.*`/`citation.*`. Recovery: `scripts/cleanup_role_assignment_ancillary_orphans.py` (heuristic re-home, dry-run → `--execute`; role_assignment-only — role/citation orphans go to manual triage); see `docs/AUDITS.md` |
| assignment-relationship window audit | daily `power-map-assignment-rel-windows.timer` runs `scripts/audit_assignment_relationship_windows.py` — report-only reconcile of active `role_assignment_relationships` edges whose window drifted outside the intersection of both endpoint assignment windows (or whose endpoint archived); shares the `cascade_assignment_relationships` clamp rule (#301). Categories `clamp`/`inverted`/`archived_endpoint`; **exit 3 on any finding** (#363) so a drifted run surfaces in `systemctl --failed`. `--execute` clamps/archives and always exits 0 (supervised); see `docs/AUDITS.md` |
| Readiness uptime guard | every 2 min `power-map-ready.timer` runs `scripts/check_ready.py` — probes `GET localhost:8000/ready`, retries once after 10s, exits 3 only when **both** attempts fail (a lone blip stays quiet). Journal WARNING carries the reason slug (`no_pool`/`pool_timeout`/`db_error`/`unreachable`/`probe_timeout`/`http_<code>`), which is most of the triage. On failure it opens a single `ready-regression` GH issue (summary + journal pointer only — public repo) and **stays quiet while that issue is open**, since a comment per run would be ~30/hour; recovery comments once and closes it, so no local state file is needed. Exists because `/ready` was correct and unread during the 2026-08-09 outage (#347). Hatches: `READY_CHECK_NO_GH=1`, `READY_CHECK_FORCE_FAIL=1`; overrides `READY_PROBE_URL`/`_TIMEOUT`/`_ATTEMPTS`/`_RETRY_DELAY` |
| Egress-IP drift guard | every 5 min `power-map-egress-ip.timer` runs `scripts/check_egress_ip.py` — compares this host's public egress IP against the cluster's **live Trusted Sources** read from the DO API (`DO_API_TOKEN`), falling back to `EGRESS_EXPECTED_IPS` when there is no token or the API is unreachable; exit 3 + `egress-ip-drift` GH issue carrying the **new address** on mismatch. The API source also catches our rule being *removed*, which a hand-maintained copy cannot see. An empty Trusted Sources list means DO is applying no IP restriction at all — reported, never drift. The DO cluster gates on source IP and the exe.dev egress IP is NAT'd and unpinned, so a rotation kills every DB-backed route (2026-08-09, #410). Losing every lookup service is **not** drift — WARNING, exit 0. Since #409 the allowlist is read live, so there is nothing to keep in sync. Triage → `docs/RUNBOOK_DB_TRIAGE.md`. Hatches: `EGRESS_CHECK_NO_GH=1`, `EGRESS_CHECK_FORCE_FAIL=1` |
| Weekly a11y sweep | weekly `power-map-a11y.timer` (Sun 04:00 UTC) runs `scripts/run-a11y-sweep.sh` — both a11y tiers (lxml `test_a11y_render.py -m integration` + Playwright/axe `test_a11y_browser.py -m browser`, #369) against the test DB, own uvicorn on an ephemeral port. **Chromium guard** exits 2 if Playwright's browser is absent (else the tier importorskips to a vacuous pass); one-time `uv sync --group browser && playwright install chromium`. Non-zero exit → `systemctl --failed`; on failure the runner opens-or-updates the `a11y-regression` GH issue (closes on recovery), and the `SessionStart` hook `.claude/hooks/a11y-status-reminder.sh` surfaces status when you open Claude on the VM. See `docs/TESTING.md` |

---

## Operational script safety


**Operational scripts — dry run by default (#402):** `DATABASE_URL` resolves to **production** from any directory, so a `scripts/` writer gates the write behind `--execute` and calls `echo_target()` (`scripts/_dsn.py`) before connecting — the gate stops an unintended write, the echo makes an intended one attributable. `redact_dsn()` returns `None` for a non-URL DSN and callers **never** fall back to the raw string (a libpq keyword/value DSN puts the password in `urlparse`'s `path`). Dry runs come in two shapes — read-only preview, or real work rolled back; the second must account for side effects that do *not* roll back (the importer parses addresses locally rather than spending validator quota). DDL is never implicit: `--apply-schema` is opt-in and requires `--execute`. `apply-schema.sh` keeps a duplicate copy of the redaction on purpose (`ExecStartPre` — an import failure = failed prod restart), pinned by a parity test. **#399** made this uniform across all 37 scripts: `add_dsn_args(parser)` + `resolve_dsn(args, parser)` give every script `--database-url` and `--test` (which **hard-errors** when `TEST_DATABASE_URL` is unset rather than falling back to production), and the echo carries a `(production|test|unknown — assume production)` label keyed on `(host, port, dbname)` — not the DSN string, since production is reached as two users. `tests/scripts/test_dsn_sweep.py` enforces all three rules by AST with **no allowlist**. Full rules → `docs/CONVENTIONS.md` §"Operational scripts — dry run by default & target echo".

**apply-schema guards (#398):** `apply-schema.sh` writes to **production** on the bare invocation and is `ExecStartPre` on the unit — so every guard it grows must be untrippable by the systemd shape (main checkout, no TTY, no flags), else a guard bug becomes a failed prod restart; that covers the diagnostics too (the target echo degrades to `(unparsed DSN — cannot redact)` rather than aborting, and never echoes a non-URL DSN — it would carry the password). Hard-fail (exit 2): linked worktree, declined TTY confirmation. Warn only: tracked modifications, branch ≠ `main`. The script `cd`s to its own repo root first, so the checkout it reports is the tree whose `schema.sql` it applies. `--test` targets `TEST_DATABASE_URL` and is the door for worktree schema work (`sync-schema-to-do.sh` delegates to it); `--yes` skips the guards; `--dry-run` stops after the target echo. Full rules → `docs/COMMANDS.md` § Deploy.
