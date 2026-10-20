#!/usr/bin/env bash
# Write DO managed-DB credentials to /etc/power-map/.env.
#
# Reads Terraform outputs, installs the cluster CA, URL-encodes passwords,
# applies schema-level grants via doadmin, smoke-tests the new DSNs, then writes
# DATABASE_URL / MIGRATIONS_DATABASE_URL / TEST_DATABASE_URL. Idempotent — safe
# to re-run after terraform apply.
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
CA_STAGED="$CA_FILE.new"
# The DSNs it writes name the live CA; its own connections name the staged one,
# so the new CA is proven before anything else reads it.
TLS_QUERY="sslmode=verify-full&sslrootcert=${CA_FILE}"
CONNECT_TLS="sslmode=verify-full&sslrootcert=${CA_STAGED}"

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

# ── 1b. Stage the cluster CA ──────────────────────────────────────────────────
# Every connection below verifies the server against the staged copy; it goes
# live (step 7) only once they all have. Not a secret, and the units run as
# exedev, so 0644 root:root. A missing or garbled output would fail every
# verify-full connection, so check it first.
if ! printf '%s\n' "$CA_PEM" | openssl x509 -noout 2>/dev/null; then
    echo "ERROR: terraform output db_ca_certificate is not a PEM certificate" >&2
    echo "  Refresh the outputs (terraform apply -refresh-only) and re-run." >&2
    exit 2
fi
echo "==> Staging $CA_STAGED"
printf '%s\n' "$CA_PEM" | sudo tee "$CA_STAGED" > /dev/null
sudo chmod 644 "$CA_STAGED"
sudo chown root:root "$CA_STAGED"

# ── 2. URL-encode passwords ───────────────────────────────────────────────────
# No password ever goes on a command line: /proc/*/cmdline is world-readable,
# /proc/*/environ is owner-only. Each one travels in the environment or on stdin.
url_encode() {
    PW="$1" python3 -c "import os, urllib.parse; print(urllib.parse.quote(os.environ['PW'], safe=''))"
}

PROD_PASS_ENC=$(url_encode "$PROD_PASS")
MIGS_PASS_ENC=$(url_encode "$MIGS_PASS")
TEST_PASS_ENC=$(url_encode "$TEST_PASS")

# ── 3. Construct DSNs ─────────────────────────────────────────────────────────
DATABASE_URL="postgresql://${PROD_USER}:${PROD_PASS_ENC}@${HOST}:${PORT}/co_pm_db_production?${TLS_QUERY}"
MIGRATIONS_DATABASE_URL="postgresql://${MIGS_USER}:${MIGS_PASS_ENC}@${HOST}:${PORT}/co_pm_db_production?${TLS_QUERY}"
TEST_DATABASE_URL="postgresql://${TEST_USER}:${TEST_PASS_ENC}@${HOST}:${PORT}/co_pm_db_test?${TLS_QUERY}"

# ── 4. Connections, password in PGPASSWORD ───────────────────────────────────
# doadmin's user and password come out of the cluster URI through the
# environment and stdout. Every connection is verify-full against the CA.
{ read -r DOADMIN_USER; read -r DOADMIN_PASS; } < <(
    DOADMIN_URI="$DOADMIN_URI" python3 -c '
import os
from urllib.parse import unquote, urlparse
u = urlparse(os.environ["DOADMIN_URI"])
print(unquote(u.username or ""))
print(unquote(u.password or ""))'
)

pg() {  # pg <user> <password> <database> [psql args…]
    local user=$1 pass=$2 db=$3
    shift 3
    PGPASSWORD="$pass" psql "postgresql://${user}@${HOST}:${PORT}/${db}?${CONNECT_TLS}" "$@"
}

# ── 5. Schema-level grants ────────────────────────────────────────────────────
# ON_ERROR_STOP: without it psql exits 0 on a failed statement, and a grant that
# did not apply would pass for one that did.
# Production DB: migrations user owns schema; app user gets DML only.
echo "==> Applying grants on co_pm_db_production"
pg "$DOADMIN_USER" "$DOADMIN_PASS" co_pm_db_production -v ON_ERROR_STOP=1 <<SQL
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
pg "$DOADMIN_USER" "$DOADMIN_PASS" co_pm_db_test -v ON_ERROR_STOP=1 <<SQL
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
GRANT USAGE, CREATE ON SCHEMA public TO ${TEST_USER};
SQL

# ── 6. Smoke-test the new DSNs, before they replace the old ones ─────────────
# A DSN that cannot connect (password, CA, hostname) must never reach the env
# file: the next restart's ExecStartPre would fail and :8000 stay down. Each is
# a hard failure — `psql … && echo` would not trip set -e. Each connects with the
# parts its DSN is built from, so no password reaches psql's argv.
smoke() {  # smoke <VAR> <user> <password> <database>
    if ! pg "$2" "$3" "$4" -c "SELECT 1" > /dev/null; then
        echo "ERROR: $1 cannot connect; $ENV_FILE left unchanged" >&2
        exit 1
    fi
    echo "    OK: $1"
}
echo "==> Verifying connections"
smoke DATABASE_URL            "$PROD_USER" "$PROD_PASS" co_pm_db_production
smoke MIGRATIONS_DATABASE_URL "$MIGS_USER" "$MIGS_PASS" co_pm_db_production
smoke TEST_DATABASE_URL       "$TEST_USER" "$TEST_PASS" co_pm_db_test

# ── 7. Make the CA live ───────────────────────────────────────────────────────
# Renamed, not rewritten: a re-run (credential rotation) races live connects,
# which must never read a half-written CA — nor one nothing has connected through.
echo "==> Installing $CA_FILE"
sudo mv -f "$CA_STAGED" "$CA_FILE"

# ── 8. Write /etc/power-map/.env ──────────────────────────────────────────────
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

echo "==> Done"
