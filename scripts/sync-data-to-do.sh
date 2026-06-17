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

row_counts_local() {
    if [[ -n "$LOCAL_DSN" ]]; then
        psql "$LOCAL_DSN" -tA -c \
            "SELECT relname || '=' || n_live_tup
             FROM pg_stat_user_tables
             WHERE n_live_tup > 0
             ORDER BY relname;"
    else
        sudo -u postgres psql -d "$LOCAL_DB_NAME" -tA -c \
            "SELECT relname || '=' || n_live_tup
             FROM pg_stat_user_tables
             WHERE n_live_tup > 0
             ORDER BY relname;"
    fi
}

row_counts_do() {
    psql "$MIGRATIONS_DATABASE_URL" -tA -c \
        "SELECT relname || '=' || n_live_tup
         FROM pg_stat_user_tables
         WHERE n_live_tup > 0
         ORDER BY relname;"
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
