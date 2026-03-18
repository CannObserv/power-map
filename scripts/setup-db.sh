#!/usr/bin/env bash
# scripts/setup-db.sh — provision local PostgreSQL for power-map
# Idempotent: safe to re-run.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
SCHEMA="${REPO_ROOT}/src/core/schema.sql"
ENV_FILE="${REPO_ROOT}/env"

DB_USER="powermap"
DB_DEV="powermap"
DB_TEST="powermap_test"

# ---------------------------------------------------------------------------
# 1. Install PostgreSQL
# ---------------------------------------------------------------------------

if ! command -v psql &>/dev/null; then
    echo "Installing PostgreSQL..."
    sudo apt-get update -qq
    sudo apt-get install -y postgresql
fi

if ! sudo service postgresql status &>/dev/null; then
    echo "Starting PostgreSQL..."
    sudo service postgresql start
fi

# ---------------------------------------------------------------------------
# 2. Resolve password
#    Re-use the password already in env (if any) so re-runs stay consistent.
# ---------------------------------------------------------------------------

existing_url="$(grep -E '^DATABASE_URL=' "${ENV_FILE}" 2>/dev/null | head -1 | cut -d= -f2- || true)"
if [[ -n "${existing_url}" ]]; then
    DB_PASS="$(echo "${existing_url}" | sed -E 's|postgresql://[^:]+:([^@]+)@.*|\1|')"
else
    DB_PASS="$(openssl rand -base64 24 | tr -d '=/+' | head -c 32)"
fi

# ---------------------------------------------------------------------------
# 3. Create role (idempotent)
# ---------------------------------------------------------------------------

sudo -u postgres psql -v ON_ERROR_STOP=1 <<SQL
DO \$\$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '${DB_USER}') THEN
        CREATE ROLE ${DB_USER} LOGIN PASSWORD '${DB_PASS}';
        RAISE NOTICE 'created role ${DB_USER}';
    ELSE
        ALTER ROLE ${DB_USER} PASSWORD '${DB_PASS}';
        RAISE NOTICE 'role ${DB_USER} already exists — password synced';
    END IF;
END;
\$\$;
SQL

# ---------------------------------------------------------------------------
# 4. Create databases (idempotent)
# ---------------------------------------------------------------------------

for db in "${DB_DEV}" "${DB_TEST}"; do
    if sudo -u postgres psql -lqt | cut -d'|' -f1 | grep -qw "${db}"; then
        echo "Database already exists: ${db}"
    else
        sudo -u postgres createdb -O "${DB_USER}" "${db}"
        echo "Created database: ${db}"
    fi
done

# ---------------------------------------------------------------------------
# 5. Apply schema
# ---------------------------------------------------------------------------

for db in "${DB_DEV}" "${DB_TEST}"; do
    echo "Applying schema to ${db}..."
    PGPASSWORD="${DB_PASS}" psql -h localhost -U "${DB_USER}" -d "${db}" -f "${SCHEMA}"
done

# ---------------------------------------------------------------------------
# 6. Write env vars (additive — never overwrites existing values)
# ---------------------------------------------------------------------------

touch "${ENV_FILE}"

append_if_absent() {
    local key="$1" val="$2"
    if grep -qE "^${key}=" "${ENV_FILE}" 2>/dev/null; then
        echo "${key} already set in env — skipping"
    else
        echo "${key}=${val}" >> "${ENV_FILE}"
        echo "Added ${key} to env"
    fi
}

DEV_URL="postgresql://${DB_USER}:${DB_PASS}@localhost/${DB_DEV}"
TEST_URL="postgresql://${DB_USER}:${DB_PASS}@localhost/${DB_TEST}"

append_if_absent "DATABASE_URL"      "${DEV_URL}"
append_if_absent "TEST_DATABASE_URL" "${TEST_URL}"

echo ""
echo "Setup complete."
echo "  Dev DB:  ${DEV_URL}"
echo "  Test DB: ${TEST_URL}"
