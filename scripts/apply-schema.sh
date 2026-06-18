#!/usr/bin/env bash
# Apply schema.sql to the production database using MIGRATIONS_DATABASE_URL.
#
# Uses the migrations user (DDL privileges) rather than the app user (DML only).
# Idempotent — safe to re-run; all DDL uses IF NOT EXISTS guards.
#
# Usage (from repo root):
#   bash scripts/apply-schema.sh
#
# Called automatically by the systemd unit (ExecStartPre) on every restart.

set -euo pipefail

: "${MIGRATIONS_DATABASE_URL:?MIGRATIONS_DATABASE_URL not set — source /etc/power-map/.env}"

env_args=()
[ -f /etc/power-map/.env ] && env_args+=(--env-file /etc/power-map/.env)
[ -f .env ]                && env_args+=(--env-file .env)

uv run "${env_args[@]}" python - <<'PY'
import asyncio, asyncpg, os
from src.core.db import apply_schema

async def main():
    conn = await asyncpg.connect(os.environ["MIGRATIONS_DATABASE_URL"])
    try:
        await apply_schema(conn)
    finally:
        await conn.close()
    print("schema applied")

asyncio.run(main())
PY
