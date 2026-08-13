#!/usr/bin/env bash
# Apply schema.sql to a target database.
#
# The default target is MIGRATIONS_DATABASE_URL — the PRODUCTION database.
# Uses the migrations user (DDL privileges) rather than the app user (DML only).
# Idempotent — safe to re-run; all DDL uses IF NOT EXISTS guards.
#
# Usage (any cwd — the script works from the checkout that owns it):
#   bash scripts/apply-schema.sh              # PRODUCTION; main checkout only
#   bash scripts/apply-schema.sh --test       # TEST_DATABASE_URL; allowed anywhere
#   bash scripts/apply-schema.sh --yes|-y     # PRODUCTION; skip the guards
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
#   warn only        tracked modifications / branch other than main
#   degrade          the target echo, when the DSN or python3 will not cooperate
#
# That last rule is the same rule: diagnostics on this path may not abort a
# restart, and a DSN that is not a parseable URL is never echoed at all.
#
# Exit codes: 0 applied (or dry run), 1 usage or configuration error,
# 2 guard refusal (nothing was applied).

set -euo pipefail

TARGET_VAR=MIGRATIONS_DATABASE_URL
TARGET_LABEL=PRODUCTION
IS_PROD=1
ASSUME_YES=0
DRY_RUN=0
DEFAULT_BRANCH=main
# POWER_MAP_ENV_FILE is a test seam — it redirects where the DSN fallback reads
# credentials from. Production never sets it.
ENV_FILE="${POWER_MAP_ENV_FILE:-/etc/power-map/.env}"

usage() {
    cat <<'EOF'
usage: bash scripts/apply-schema.sh [--test] [--yes|-y] [--dry-run]

  (no flags)  apply to PRODUCTION (MIGRATIONS_DATABASE_URL); main checkout only
  --test      apply to the test database (TEST_DATABASE_URL); allowed anywhere
  --yes, -y   skip the production guards (worktree refusal, confirmation)
  --dry-run   run the guards and echo the target, then stop without applying
EOF
}

while [ $# -gt 0 ]; do
    case "$1" in
        --test)    TARGET_VAR="TEST_DATABASE_URL"; TARGET_LABEL="test"; IS_PROD=0 ;;
        --yes|-y)  ASSUME_YES=1 ;;
        --dry-run) DRY_RUN=1 ;;
        -h|--help) usage; exit 0 ;;
        *)         echo "unknown option: $1" >&2; usage >&2; exit 1 ;;
    esac
    shift
done

# ── Work from the checkout that owns this script ─────────────────────────────
# The applied schema.sql, the git context the guards read, and the uv project
# must all describe one tree — otherwise the "checkout:" line below reports a
# different tree than the file the caller invoked. Resolved without `dirname`
# so the guards survive a threadbare PATH — which also means symlinks are not
# followed: invoking a symlink to this script from another tree is unsupported.
script_dir="${BASH_SOURCE[0]}"
case "$script_dir" in
    */*) script_dir="${script_dir%/*}" ;;
    *)   script_dir="." ;;
esac
cd "$script_dir/.." || {
    echo "cannot reach the repo root from ${script_dir}" >&2
    exit 1
}

# ── Resolve the DSN ──────────────────────────────────────────────────────────
# The environment wins (systemd populates it from EnvironmentFile); a manual
# invocation falls back to reading the same file directly.
DSN="${!TARGET_VAR:-}"
if [ -z "$DSN" ] && [ -f "$ENV_FILE" ]; then
    # systemd's EnvironmentFile parser tolerates `export ` prefixes, quoting and
    # CRLF; this fallback reads the same file, so it must tolerate them too.
    DSN="$(grep -E "^(export[[:space:]]+)?${TARGET_VAR}=" "$ENV_FILE" | tail -n1 | cut -d= -f2- || true)"
    DSN="${DSN%$'\r'}"
    case "$DSN" in
        \"*\") DSN="${DSN#\"}"; DSN="${DSN%\"}" ;;
        \'*\') DSN="${DSN#\'}"; DSN="${DSN%\'}" ;;
    esac
fi
if [ -z "$DSN" ]; then
    echo "$TARGET_VAR not set — check $ENV_FILE" >&2
    exit 1
fi

# ── Echo the target (redacted — the DSN carries a password) ──────────────────
# Best-effort by construction: this is diagnostic output on the ExecStartPre
# path, so neither a missing python3 nor an unparseable DSN may abort the run.
# A DSN that is not a real URL is never echoed at all — urlparse would hand
# back the credentials as the "path" and we would print them into the journal.
# Two lines come back: the database name first (it is the one that can be
# empty, and command substitution eats trailing newlines), the description
# second. Stderr is suppressed because a traceback would echo argv — i.e. the
# DSN — so the tests below are what keep the parsing path honest.
TARGET_DESC=""
TARGET_DB=""
if redacted="$(
    python3 - "$DSN" <<'PY' 2>/dev/null
import sys
from urllib.parse import urlparse

u = urlparse(sys.argv[1])
if not u.scheme or not u.hostname:
    raise SystemExit(1)
try:
    port = u.port
except ValueError:
    raise SystemExit(1)

user = u.username + "@" if u.username else ""
tail = ":" + str(port) if port else ""
db = (u.path or "").lstrip("/")
print(db)
print(user + u.hostname + tail + "/" + (db or "?"))
PY
)"; then
    TARGET_DB="${redacted%%$'\n'*}"
    TARGET_DESC="${redacted##*$'\n'}"
fi
if [ -z "$TARGET_DESC" ]; then
    TARGET_DESC="(unparsed DSN — cannot redact)"
    TARGET_DB=""
fi
echo "target: ${TARGET_DESC} (${TARGET_LABEL})" >&2

# ── Git context ──────────────────────────────────────────────────────────────
# Every git call here degrades rather than aborting: an unusable git must not
# end a restart, and it must not be *inferred* from either (a blank
# --git-common-dir would make `cd ""` succeed and mimic a linked worktree).
# When the layout cannot be read the guard is announced as unavailable and the
# run proceeds — same as a non-git directory, and the other guards still apply.
LINKED_WORKTREE=0
if git rev-parse --git-dir >/dev/null 2>&1; then
    git_dir="$(git rev-parse --git-dir 2>/dev/null || true)"
    common_dir="$(git rev-parse --git-common-dir 2>/dev/null || true)"
    [ -z "$git_dir" ] || git_dir="$(cd "$git_dir" 2>/dev/null && pwd -P || true)"
    [ -z "$common_dir" ] || common_dir="$(cd "$common_dir" 2>/dev/null && pwd -P || true)"

    if [ -n "$git_dir" ] && [ -n "$common_dir" ]; then
        # A linked worktree's git dir is <main>/.git/worktrees/<name>; the main
        # checkout's git dir *is* the common dir.
        [ "$git_dir" = "$common_dir" ] || LINKED_WORKTREE=1
    else
        echo "WARNING: git did not report its layout — the worktree guard is unavailable" >&2
    fi

    branch="$(git rev-parse --abbrev-ref HEAD 2>/dev/null || true)"
    sha="$(git rev-parse --short HEAD 2>/dev/null || true)"
    toplevel="$(git rev-parse --show-toplevel 2>/dev/null || true)"
    echo "checkout: ${toplevel:-?} (branch=${branch:-?} sha=${sha:-?})" >&2

    if [ "$IS_PROD" -eq 1 ]; then
        # Tracked modifications only: untracked files cannot change schema.sql,
        # and a warning that fires on every restart stops being read.
        if [ -n "$(git status --porcelain --untracked-files=no 2>/dev/null)" ]; then
            echo "WARNING: checkout has uncommitted changes — production schema should come from a clean tree" >&2
        fi
        if [ -n "$branch" ] && [ "$branch" != "$DEFAULT_BRANCH" ]; then
            echo "WARNING: on branch ${branch}, not ${DEFAULT_BRANCH} — production schema should come from ${DEFAULT_BRANCH}" >&2
        fi
    fi
else
    echo "WARNING: not a git checkout — the worktree guard is unavailable" >&2
fi

# ── Production guards ────────────────────────────────────────────────────────
if [ "$IS_PROD" -eq 1 ] && [ "$ASSUME_YES" -eq 0 ]; then
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
        # Falls back to a fixed word when the DSN could not be parsed — there is
        # no database name to quote in that case.
        confirm_token="${TARGET_DB:-production}"
        printf 'about to apply schema.sql to PRODUCTION — type the database name (%s) to continue: ' "$confirm_token" >&2
        reply=""
        read -r reply || reply=""
        if [ "$reply" != "$confirm_token" ]; then
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
