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
| `/etc/power-map/.env` (640, root:exedev) | `DATABASE_URL`, `MIGRATIONS_DATABASE_URL`, `TEST_DATABASE_URL`, `ADDRESS_VALIDATOR_API_KEY`, `ADDRESS_VALIDATOR_RUN_VALIDATION` (default false; true → `/validate`, false → `/standardize` only), `ADDRESS_VALIDATOR_BASE_URL` (optional; defaults to `https://address-validator.exe.xyz:8000`), `DB_POOL_MIN_SIZE` (default 2), `DB_POOL_MAX_SIZE` (default 5; tune per DO tier) |
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

## Tombstone cleanup (deleted_entities TTL)

Rows in `deleted_entities` older than 90 days are safe to purge — sibling services should have invalidated their caches by then. Run manually or via cron:

```bash
# Build --env-file flags (see § Environment)
env_args=()
[ -f /etc/power-map/.env ] && env_args+=(--env-file /etc/power-map/.env)
[ -f .env ] && env_args+=(--env-file .env)

# Dry run — count rows that would be deleted
uv run "${env_args[@]}" python -c "
import asyncio, asyncpg, os
async def main():
    conn = await asyncpg.connect(os.environ['DATABASE_URL'])
    n = await conn.fetchval(\"SELECT COUNT(*) FROM deleted_entities WHERE deleted_at < NOW() - INTERVAL '90 days'\")
    print(f'{n} rows eligible for cleanup')
    await conn.close()
asyncio.run(main())
"

# Execute — purge stale tombstones
uv run "${env_args[@]}" python -c "
import asyncio, asyncpg, os
async def main():
    conn = await asyncpg.connect(os.environ['DATABASE_URL'])
    result = await conn.execute(\"DELETE FROM deleted_entities WHERE deleted_at < NOW() - INTERVAL '90 days'\")
    print(result)
    await conn.close()
asyncio.run(main())
"
```

## Git Submodules

```bash
# Init after cloning
git submodule update --init --recursive

# Force-refresh vendor skills
git submodule update --remote --merge skills-vendor/gregoryfoster-skills skills-vendor/obra-superpowers
```
