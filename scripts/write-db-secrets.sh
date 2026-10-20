#!/usr/bin/env bash
# Write DO managed-DB credentials to /etc/power-map/.env.
#
# Reads Terraform outputs, installs the cluster CA, URL-encodes passwords,
# applies schema-level grants via doadmin, then writes DATABASE_URL /
# MIGRATIONS_DATABASE_URL / TEST_DATABASE_URL. Idempotent — safe to re-run after
# terraform apply.
#
# Every DSN it writes, and every connection it makes, is sslmode=verify-full
# against the cluster CA (#667): Trusted Sources admits an exe.dev egress IP other
# VMs may share, so credentials and TLS are the boundary, not the allowlist.
#
# Usage (from repo root):
#   bash scripts/write-db-secrets.sh
#
# Requires: terraform, jq, psql, python3, openssl, sudo access to write /etc/power-map/.env

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TF_DIR="$SCRIPT_DIR/../infra/terraform"
ENV_FILE="/etc/power-map/.env"
CA_FILE="/etc/power-map/do-ca.crt"
TLS_QUERY="sslmode=verify-full&sslrootcert=${CA_FILE}"

# ── 0. Preflight: terraform must be able to reach state ───────────────────────
# On 2026-08-09 both gitignored credential files turned out to be absent from
# the VM, and this script's first act — `terraform output -json` — failed with a
# backend error that named none of that. Fail here instead, pointing at the
# rebuild (#409). Custody: /etc/power-map/.env holds DO_API_TOKEN,
# DO_SPACES_KEY and DO_SPACES_VALUE.
missing=()
[ -f "$TF_DIR/terraform.tfvars" ] || missing+=("terraform.tfvars")
[ -f "$TF_DIR/backend.hcl" ]      || missing+=("backend.hcl")
[ -d "$TF_DIR/.terraform" ]       || missing+=(".terraform/ (not initialised)")
if [ ${#missing[@]} -gt 0 ]; then
    echo "ERROR: terraform is not usable — missing: ${missing[*]}" >&2
    echo "  Rebuild the credentials, then initialise:" >&2
    echo "    uv run python -m scripts.write_terraform_credentials" >&2
    echo "    terraform -chdir=infra/terraform init -backend-config=backend.hcl" >&2
    echo "  See docs/COMMANDS.md § Provisioning." >&2
    exit 2
fi

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
CA_PEM=$(echo "$TF_JSON"     | jq -r '.db_ca_certificate.value')

# ── 1b. Install the cluster CA ────────────────────────────────────────────────
# Before any connection: every one below verifies the server against it. Not a
# secret, and the units run as exedev, so 0644 root:root. A missing or garbled
# output would fail every verify-full connection, so check it first.
if ! printf '%s\n' "$CA_PEM" | openssl x509 -noout 2>/dev/null; then
    echo "ERROR: terraform output db_ca_certificate is not a PEM certificate" >&2
    echo "  Refresh the outputs (terraform apply -refresh-only) and re-run." >&2
    exit 2
fi
echo "==> Installing $CA_FILE"
printf '%s\n' "$CA_PEM" | sudo tee "$CA_FILE" > /dev/null
sudo chmod 644 "$CA_FILE"
sudo chown root:root "$CA_FILE"

# ── 2. URL-encode passwords ───────────────────────────────────────────────────
url_encode() {
    python3 -c "import urllib.parse, sys; print(urllib.parse.quote(sys.argv[1], safe=''))" "$1"
}

PROD_PASS_ENC=$(url_encode "$PROD_PASS")
MIGS_PASS_ENC=$(url_encode "$MIGS_PASS")
TEST_PASS_ENC=$(url_encode "$TEST_PASS")

# ── 3. Construct DSNs ─────────────────────────────────────────────────────────
DATABASE_URL="postgresql://${PROD_USER}:${PROD_PASS_ENC}@${HOST}:${PORT}/co_pm_db_production?${TLS_QUERY}"
MIGRATIONS_DATABASE_URL="postgresql://${MIGS_USER}:${MIGS_PASS_ENC}@${HOST}:${PORT}/co_pm_db_production?${TLS_QUERY}"
TEST_DATABASE_URL="postgresql://${TEST_USER}:${TEST_PASS_ENC}@${HOST}:${PORT}/co_pm_db_test?${TLS_QUERY}"

# ── 4. Build per-database doadmin URIs ───────────────────────────────────────
# The cluster URI targets defaultdb; substitute the database name for grants,
# and its TLS query for ours — verify-full, like every other connection here.
make_doadmin_uri() {
    python3 - "$DOADMIN_URI" "$1" "$TLS_QUERY" <<'PYEOF'
import sys
from urllib.parse import urlparse, urlunparse
u = urlparse(sys.argv[1])
print(urlunparse(u._replace(path=f"/{sys.argv[2]}", query=sys.argv[3])))
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
# Preserve non-DSN keys (ADDRESS_VALIDATOR_*, etc.) from the existing file so
# a re-run after terraform apply doesn't silently drop credentials this script
# doesn't own.
echo "==> Writing $ENV_FILE"
PRESERVED=""
if sudo test -f "$ENV_FILE"; then
    PRESERVED=$(sudo grep -v -E "^(DATABASE_URL|MIGRATIONS_DATABASE_URL|TEST_DATABASE_URL)=" "$ENV_FILE" || true)
fi
{
    printf 'DATABASE_URL=%s\n' "${DATABASE_URL}"
    printf 'MIGRATIONS_DATABASE_URL=%s\n' "${MIGRATIONS_DATABASE_URL}"
    printf 'TEST_DATABASE_URL=%s\n' "${TEST_DATABASE_URL}"
    if [[ -n "$PRESERVED" ]]; then printf '%s\n' "$PRESERVED"; fi
} | sudo tee "$ENV_FILE" > /dev/null
sudo chmod 640 "$ENV_FILE"
sudo chown root:exedev "$ENV_FILE"


# ── 7. Smoke-test the written DSNs ────────────────────────────────────────────
echo "==> Verifying connections"
psql "$DATABASE_URL"            -c "SELECT 1" > /dev/null && echo "    OK: DATABASE_URL"
psql "$MIGRATIONS_DATABASE_URL" -c "SELECT 1" > /dev/null && echo "    OK: MIGRATIONS_DATABASE_URL"
psql "$TEST_DATABASE_URL"       -c "SELECT 1" > /dev/null && echo "    OK: TEST_DATABASE_URL"

echo "==> Done"
