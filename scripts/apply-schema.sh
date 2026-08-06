#!/usr/bin/env bash
# Apply schema.sql to a target database.
#
# The default target is MIGRATIONS_DATABASE_URL — the PRODUCTION database.
# Uses the migrations user (DDL privileges) rather than the app user (DML only).
# Idempotent — safe to re-run; all DDL uses IF NOT EXISTS guards.
#
# Usage (from the repo root):
#   bash scripts/apply-schema.sh              # PRODUCTION; main checkout only
#   bash scripts/apply-schema.sh --test       # TEST_DATABASE_URL; allowed anywhere
#   bash scripts/apply-schema.sh --yes        # PRODUCTION; skip the guards
#   bash scripts/apply-schema.sh --dry-run    # guards + target echo, then stop
#
# Called automatically by the systemd unit (ExecStartPre) on every restart.
#
# Guards (#398). A schema bug reached production because this was run by hand
# from a worktree, believing it targeted the test database. Every hard failure
# below must be untrippable by the systemd shape — main checkout, no TTY, no
# flags — because a non-zero ExecStartPre means the service does not start:
#
#   refuse (exit 2)  targeting production from a linked git worktree
#   refuse (exit 2)  targeting production interactively without confirmation
#   warn only        dirty checkout / branch other than main
#
# Exit codes: 0 applied (or dry run), 1 usage or configuration error,
# 2 guard refusal (nothing was applied).

set -euo pipefail

TARGET_VAR=MIGRATIONS_DATABASE_URL
TARGET_LABEL=PRODUCTION
ASSUME_YES=0
DRY_RUN=0
DEFAULT_BRANCH=main
ENV_FILE="${POWER_MAP_ENV_FILE:-/etc/power-map/.env}"

usage() {
    cat >&2 <<'EOF'
usage: bash scripts/apply-schema.sh [--test] [--yes] [--dry-run]

  (no flags)  apply to PRODUCTION (MIGRATIONS_DATABASE_URL); main checkout only
  --test      apply to the test database (TEST_DATABASE_URL); allowed anywhere
  --yes       skip the production guards (worktree refusal, confirmation)
  --dry-run   run the guards and echo the target, then stop without applying
EOF
}

while [ $# -gt 0 ]; do
    case "$1" in
        --test)    TARGET_VAR=TEST_DATABASE_URL; TARGET_LABEL=test ;;
        --yes|-y)  ASSUME_YES=1 ;;
        --dry-run) DRY_RUN=1 ;;
        -h|--help) usage; exit 0 ;;
        *)         echo "unknown option: $1" >&2; usage; exit 1 ;;
    esac
    shift
done

# ── Resolve the DSN ──────────────────────────────────────────────────────────
# The environment wins (systemd populates it from EnvironmentFile); a manual
# invocation falls back to reading the same file directly.
DSN="${!TARGET_VAR:-}"
if [ -z "$DSN" ] && [ -f "$ENV_FILE" ]; then
    DSN="$(grep -E "^${TARGET_VAR}=" "$ENV_FILE" | tail -n1 | cut -d= -f2- || true)"
fi
if [ -z "$DSN" ]; then
    echo "$TARGET_VAR not set — check $ENV_FILE" >&2
    exit 1
fi

# ── Echo the target (redacted — the DSN carries a password) ──────────────────
TARGET_DESC="$(
    python3 - "$DSN" <<'PY'
import sys
from urllib.parse import urlparse

u = urlparse(sys.argv[1])
user = f"{u.username}@" if u.username else ""
port = f":{u.port}" if u.port else ""
db = (u.path or "").lstrip("/") or "?"
print(f"{user}{u.hostname or '?'}{port}/{db}")
PY
)"
DBNAME="${TARGET_DESC##*/}"
echo "target: ${TARGET_DESC} (${TARGET_LABEL})" >&2

# ── Git context ──────────────────────────────────────────────────────────────
LINKED_WORKTREE=0
if git rev-parse --git-dir >/dev/null 2>&1; then
    git_dir="$(cd "$(git rev-parse --git-dir)" && pwd -P)"
    common_dir="$(cd "$(git rev-parse --git-common-dir)" && pwd -P)"
    # A linked worktree's git dir is <main>/.git/worktrees/<name>; the main
    # checkout's git dir *is* the common dir.
    [ "$git_dir" = "$common_dir" ] || LINKED_WORKTREE=1

    branch="$(git rev-parse --abbrev-ref HEAD)"
    echo "checkout: $(git rev-parse --show-toplevel) (branch=${branch} sha=$(git rev-parse --short HEAD))" >&2

    if [ "$TARGET_LABEL" = PRODUCTION ]; then
        if [ -n "$(git status --porcelain)" ]; then
            echo "WARNING: checkout has uncommitted changes — production schema should come from a clean tree" >&2
        fi
        if [ "$branch" != "$DEFAULT_BRANCH" ]; then
            echo "WARNING: on branch ${branch}, not ${DEFAULT_BRANCH} — production schema should come from ${DEFAULT_BRANCH}" >&2
        fi
    fi
else
    echo "WARNING: not a git checkout — the worktree guard is unavailable" >&2
fi

# ── Production guards ────────────────────────────────────────────────────────
if [ "$TARGET_LABEL" = PRODUCTION ] && [ "$ASSUME_YES" -eq 0 ]; then
    if [ "$LINKED_WORKTREE" -eq 1 ]; then
        cat >&2 <<EOF
refusing: this applies schema.sql to PRODUCTION (${TARGET_DESC}) and you are in
a linked git worktree. Nothing was applied.

  for schema work during development:  bash scripts/apply-schema.sh --test
  to apply to production anyway:       bash scripts/apply-schema.sh --yes
                                       (preferably from the main checkout, on ${DEFAULT_BRANCH})
EOF
        exit 2
    fi

    if [ -t 0 ]; then
        printf 'about to apply schema.sql to PRODUCTION — type the database name (%s) to continue: ' "$DBNAME" >&2
        reply=""
        read -r reply || reply=""
        if [ "$reply" != "$DBNAME" ]; then
            echo "aborted — nothing was applied" >&2
            exit 2
        fi
    fi
fi

if [ "$DRY_RUN" -eq 1 ]; then
    echo "dry run — nothing was applied" >&2
    exit 0
fi

POWER_MAP_TARGET_DSN="$DSN" uv run python - <<'PY'
import asyncio
import os
import sys

import asyncpg

from src.core.db import apply_schema

url = os.environ.get("POWER_MAP_TARGET_DSN")
if not url:
    sys.exit("target DSN missing — apply-schema.sh must set POWER_MAP_TARGET_DSN")


async def main():
    conn = await asyncpg.connect(url)
    try:
        await apply_schema(conn)
    finally:
        await conn.close()
    print("schema applied")


asyncio.run(main())
PY
