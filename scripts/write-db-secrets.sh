#!/usr/bin/env bash
# Write DO managed-DB credentials to /etc/power-map/.env.
#
# Reads Terraform outputs, URL-encodes passwords, applies schema-level grants
# via doadmin, then writes DATABASE_URL / MIGRATIONS_DATABASE_URL /
# TEST_DATABASE_URL. Idempotent — safe to re-run after terraform apply.
#
# Usage (from repo root):
#   bash scripts/write-db-secrets.sh
#
# Requires: terraform, jq, psql, python3, sudo access to write /etc/power-map/.env

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TF_DIR="$SCRIPT_DIR/../infra/terraform"
ENV_FILE="/etc/power-map/.env"

# ── 1. Read Terraform outputs ─────────────────────────────────────────────────
echo "==> Reading Terraform outputs"
TF_JSON=$(terraform -chdir="$TF_DIR" output -json)

HOST=$(echo "$TF_JSON"       | jq -r '.db_host.value')
PORT=$(echo "$TF_JSON"       | jq -r '.db_port.value')
PROD_USER=$(echo "$TF_JSON"  | jq -r '.production_user_name.value')
PROD_PASS=$(echo "$TF_JSON"  | jq -r '.production_user_password.value')
MIGS_USER=$(echo "$TF_JSON"  | jq -r '.production_migrations_name.value')
MIGS_PASS=$(echo "$TF_JSON"  | jq -r '.production_migrations_password.value')
TEST_USER=$(echo "$TF_JSON"  | jq -r '.test_user_name.value')
TEST_PASS=$(echo "$TF_JSON"  | jq -r '.test_user_password.value')
DOADMIN_URI=$(echo "$TF_JSON" | jq -r '.doadmin_uri.value')

# ── 2. URL-encode passwords ───────────────────────────────────────────────────
url_encode() {
    python3 -c "import urllib.parse, sys; print(urllib.parse.quote(sys.argv[1], safe=''))" "$1"
}

PROD_PASS_ENC=$(url_encode "$PROD_PASS")
MIGS_PASS_ENC=$(url_encode "$MIGS_PASS")
TEST_PASS_ENC=$(url_encode "$TEST_PASS")

# ── 3. Construct DSNs ─────────────────────────────────────────────────────────
DATABASE_URL="postgresql://${PROD_USER}:${PROD_PASS_ENC}@${HOST}:${PORT}/co_pm_db_production?sslmode=require"
MIGRATIONS_DATABASE_URL="postgresql://${MIGS_USER}:${MIGS_PASS_ENC}@${HOST}:${PORT}/co_pm_db_production?sslmode=require"
TEST_DATABASE_URL="postgresql://${TEST_USER}:${TEST_PASS_ENC}@${HOST}:${PORT}/co_pm_db_test?sslmode=require"

# ── 4. Build per-database doadmin URIs ───────────────────────────────────────
# The cluster URI targets defaultdb; substitute the database name for grants.
make_doadmin_uri() {
    python3 - "$DOADMIN_URI" "$1" <<'PYEOF'
import sys
from urllib.parse import urlparse, urlunparse
u = urlparse(sys.argv[1])
print(urlunparse(u._replace(path=f"/{sys.argv[2]}")))
PYEOF
}

DOADMIN_PROD_URI=$(make_doadmin_uri "co_pm_db_production")
DOADMIN_TEST_URI=$(make_doadmin_uri "co_pm_db_test")

# ── 5. Schema-level grants ────────────────────────────────────────────────────
# Production DB: migrations user owns schema; app user gets DML only.
echo "==> Applying grants on co_pm_db_production"
psql "$DOADMIN_PROD_URI" <<SQL
REVOKE CREATE ON SCHEMA public FROM PUBLIC;

GRANT USAGE, CREATE ON SCHEMA public TO ${MIGS_USER};

GRANT USAGE ON SCHEMA public TO ${PROD_USER};

ALTER DEFAULT PRIVILEGES FOR ROLE ${MIGS_USER} IN SCHEMA public
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO ${PROD_USER};
ALTER DEFAULT PRIVILEGES FOR ROLE ${MIGS_USER} IN SCHEMA public
    GRANT USAGE, SELECT ON SEQUENCES TO ${PROD_USER};
SQL

# Test DB: test user owns schema; full DDL required for apply_schema + TRUNCATE.
echo "==> Applying grants on co_pm_db_test"
psql "$DOADMIN_TEST_URI" <<SQL
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
GRANT USAGE, CREATE ON SCHEMA public TO ${TEST_USER};
SQL

# ── 6. Write /etc/power-map/.env ──────────────────────────────────────────────
echo "==> Writing $ENV_FILE"
sudo tee "$ENV_FILE" > /dev/null <<EOF
DATABASE_URL=${DATABASE_URL}
MIGRATIONS_DATABASE_URL=${MIGRATIONS_DATABASE_URL}
TEST_DATABASE_URL=${TEST_DATABASE_URL}
EOF
sudo chmod 640 "$ENV_FILE"
sudo chown root:exedev "$ENV_FILE"

echo "==> Done"
echo "    Verify: sudo cat $ENV_FILE"
