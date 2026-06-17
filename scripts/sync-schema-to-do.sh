#!/usr/bin/env bash
# Install PostgreSQL extensions on the DO cluster (requires doadmin — regular
# users cannot CREATE EXTENSION) then apply schema.sql to the test database.
#
# Production schema is NOT applied here; sync-data-to-do.sh handles it via
# pg_restore (which includes schema + data from the local dump).
#
# Idempotent — safe to re-run.
#
# Usage (from repo root):
#   bash scripts/sync-schema-to-do.sh
#
# Requires: terraform, jq, psql, uv on PATH

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TF_DIR="$SCRIPT_DIR/../infra/terraform"

# ── 1. Read credentials ───────────────────────────────────────────────────────
echo "==> Reading Terraform outputs"
TF_JSON=$(terraform -chdir="$TF_DIR" output -json)
DOADMIN_URI=$(echo "$TF_JSON" | jq -r '.doadmin_uri.value')

TEST_DATABASE_URL=$(grep -E "^TEST_DATABASE_URL=" /etc/power-map/.env | cut -d= -f2-)
if [[ -z "$TEST_DATABASE_URL" ]]; then
    echo "ERROR: TEST_DATABASE_URL not found in /etc/power-map/.env — run write-db-secrets.sh first" >&2
    exit 1
fi

# ── 2. Build per-database doadmin URIs ───────────────────────────────────────
doadmin_uri_for() {
    python3 - "$DOADMIN_URI" "$1" <<'PYEOF'
import sys
from urllib.parse import urlparse, urlunparse
u = urlparse(sys.argv[1])
print(urlunparse(u._replace(path=f"/{sys.argv[2]}")))
PYEOF
}

DOADMIN_PROD_URI=$(doadmin_uri_for "co_pm_db_production")
DOADMIN_TEST_URI=$(doadmin_uri_for "co_pm_db_test")

# ── 3. Install extensions as doadmin ─────────────────────────────────────────
# CREATE EXTENSION requires superuser on DO managed PostgreSQL. Extensions must
# be pre-installed before schema.sql runs (which uses CREATE EXTENSION IF NOT
# EXISTS — those become no-ops once the extension exists).
echo "==> Installing extensions on both databases"
for uri in "$DOADMIN_PROD_URI" "$DOADMIN_TEST_URI"; do
    psql "$uri" <<SQL
CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS unaccent;
SQL
done

# ── 4. Apply schema to test database ─────────────────────────────────────────
# Production schema is handled by sync-data-to-do.sh (pg_restore). Test DB
# needs apply_schema so integration tests have a working empty schema.
echo "==> Applying schema to co_pm_db_test"

env_args=()
[ -f /etc/power-map/.env ] && env_args+=(--env-file /etc/power-map/.env)
[ -f .env ]                && env_args+=(--env-file .env)

uv run "${env_args[@]}" python -c "
import asyncio, asyncpg, os
from src.core.db import apply_schema

async def main():
    conn = await asyncpg.connect(os.environ['TEST_DATABASE_URL'])
    try:
        await apply_schema(conn)
    finally:
        await conn.close()

asyncio.run(main())
"

# ── 5. Seed lookup tables on test database ───────────────────────────────────
# bcp47_locales and iso15924_scripts must be populated for integration tests
# that write to person_names.locale / .script (FK-validated) and for tests
# that assert the "both seeded → no warning" branch in db.py.
echo "==> Seeding lookup tables on co_pm_db_test"
TEST_DATABASE_URL=$(grep -E "^TEST_DATABASE_URL=" /etc/power-map/.env | cut -d= -f2-)
DATABASE_URL="$TEST_DATABASE_URL" uv run --group seed scripts/seed_locales_scripts.py

echo "==> Done"
echo "    Production schema: run sync-data-to-do.sh next"
echo "    Seed lookup tables after cutover: uv run --group seed scripts/seed_locales_scripts.py"
