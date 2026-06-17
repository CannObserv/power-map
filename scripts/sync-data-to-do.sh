#!/usr/bin/env bash
# Dump local PostgreSQL → restore into DO co_pm_db_production.
#
# Performs a full dump (schema + data) so the production DB on DO is an exact
# copy of local. Extensions must already exist on the DO cluster; run
# sync-schema-to-do.sh first.
#
# Run this BEFORE cutover while local postgres is still accessible.
# Idempotent: --clean --if-exists drops and recreates all objects on re-run.
#
# Usage (from repo root):
#   bash scripts/sync-data-to-do.sh [LOCAL_DSN]
#
# LOCAL_DSN: optional DSN for the local source database. When omitted, uses
#   peer authentication as the postgres system user (requires sudo).
#   Example: bash scripts/sync-data-to-do.sh postgresql://powermap:pw@localhost/powermap
#
# Requires: pg_dump, pg_restore, psql, sudo (if LOCAL_DSN omitted)

set -euo pipefail

LOCAL_DSN="${1:-}"
DUMP_FILE="/tmp/powermap_pre_cutover.fc"
LOCAL_DB_NAME="powermap"

MIGRATIONS_DATABASE_URL=$(grep -E "^MIGRATIONS_DATABASE_URL=" /etc/power-map/.env | cut -d= -f2-)
if [[ -z "$MIGRATIONS_DATABASE_URL" ]]; then
    echo "ERROR: MIGRATIONS_DATABASE_URL not found in /etc/power-map/.env — run write-db-secrets.sh first" >&2
    exit 1
fi

# ── 1. Dump local database ────────────────────────────────────────────────────
echo "==> Dumping local database ($LOCAL_DB_NAME)"
if [[ -n "$LOCAL_DSN" ]]; then
    pg_dump --no-owner --no-acl -Fc "$LOCAL_DSN" -f "$DUMP_FILE"
else
    sudo -u postgres pg_dump --no-owner --no-acl -Fc "$LOCAL_DB_NAME" -f "$DUMP_FILE"
    sudo chown "$(whoami)" "$DUMP_FILE"
fi
echo "    $(du -sh "$DUMP_FILE" | cut -f1) written to $DUMP_FILE"

# ── 2. Restore to DO ──────────────────────────────────────────────────────────
# --clean --if-exists: drop existing objects before recreating (idempotent).
# --no-owner --no-acl: skip ownership/ACL — DO roles differ from local roles.
# Extensions (pg_trgm, vector, unaccent) are pre-installed by doadmin via
# sync-schema-to-do.sh; CREATE EXTENSION IF NOT EXISTS in the dump is a no-op.
echo "==> Restoring to co_pm_db_production"
pg_restore \
    --no-owner \
    --no-acl \
    --clean \
    --if-exists \
    -d "$MIGRATIONS_DATABASE_URL" \
    "$DUMP_FILE"

# ── 3. Row count verification ─────────────────────────────────────────────────
echo "==> Verifying row counts"

row_counts_exact() {
    local conn="$1" sudo_prefix="${2:-}"
    # Build and execute a UNION ALL COUNT(*) query for all public user tables.
    # Two psql calls: one to generate the SQL, one to run it.
    local sql
    sql=$($sudo_prefix psql "$conn" -tA -c \
        "SELECT string_agg(
             'SELECT ' || quote_literal(tablename) || ', COUNT(*) FROM ' || quote_ident(tablename),
             ' UNION ALL '
             ORDER BY tablename
         ) FROM pg_tables WHERE schemaname = 'public';")
    $sudo_prefix psql "$conn" -tA -c "$sql" \
        | sed 's/|/=/' \
        | awk -F= '$2 > 0'
}

row_counts_local() {
    if [[ -n "$LOCAL_DSN" ]]; then
        row_counts_exact "$LOCAL_DSN"
    else
        row_counts_exact "$LOCAL_DB_NAME" "sudo -u postgres"
    fi
}

row_counts_do() {
    row_counts_exact "$MIGRATIONS_DATABASE_URL"
}

LOCAL_COUNTS=$(row_counts_local)
DO_COUNTS=$(row_counts_do)

PASS=true
while IFS='=' read -r table local_count; do
    do_count=$(echo "$DO_COUNTS" | grep -E "^${table}=" | cut -d= -f2 || echo "MISSING")
    if [[ "$local_count" != "$do_count" ]]; then
        echo "    MISMATCH: $table — local=$local_count DO=$do_count"
        PASS=false
    else
        echo "    OK: $table ($local_count rows)"
    fi
done <<< "$LOCAL_COUNTS"

rm -f "$DUMP_FILE"

if [[ "$PASS" != "true" ]]; then
    echo "ERROR: row count mismatches detected — do not proceed with cutover" >&2
    exit 1
fi

echo "==> All row counts match — ready for cutover"
