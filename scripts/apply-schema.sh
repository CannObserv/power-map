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

env_args=()
[ -f /etc/power-map/.env ] && env_args+=(--env-file /etc/power-map/.env)
[ -f .env ]                && env_args+=(--env-file .env)

uv run "${env_args[@]}" python - <<'PY'
import asyncio, asyncpg, os, sys
from src.core.db import apply_schema

url = os.environ.get("MIGRATIONS_DATABASE_URL")
if not url:
    sys.exit("MIGRATIONS_DATABASE_URL not set — check /etc/power-map/.env")

async def main():
    conn = await asyncpg.connect(url)
    try:
        await apply_schema(conn)
    finally:
        await conn.close()
    print("schema applied")

asyncio.run(main())
PY
