#!/usr/bin/env bash
# scripts/pre-ship.sh: this repo's ship gate (#740). SKILL.md Step 1 resolves it
# ahead of the vendored gate (skills#301). It runs both a11y tiers when the
# branch touches the admin surface, then hands off to the vendored gate, which
# runs everything else. It wraps that gate and copies none of it.
#
# The two tiers, as the retired weekly sweep (#369) ran them:
#   lxml render tier   tests/api/admin/test_a11y_render.py -m integration (#246)
#   browser tier       tests/api/admin/ -m browser (#300, #367, #368)
#
# When they run: a path listed in scripts/pre-ship-a11y-paths changed between
# merge-base(origin/main, HEAD) and the working tree (committed, staged,
# unstaged or untracked). When the wrapper can't tell (no merge base, the list
# missing or empty), it runs them anyway.
#
# Never vacuous. Without TEST_DATABASE_URL both tiers skip and pytest exits 0,
# and without Chromium the browser tier skips. So a preflight checks the
# variable, the DB and the browser first, and a tier that exits 0 with nothing
# passed is a failure too. An unreachable DB is reported as infrastructure, not
# as an a11y finding: the sweep's 11-01 run filed one as a regression (#744).
#
# One at a time. The browser tier truncates and seeds the shared test DB
# (#300), so the tiers run under a flock in the git common dir, shared by
# every worktree. The lock is held for the preflight and the tiers only.
#
# The env files (/etc/power-map/.env, then .env) go to the tier calls only.
# The vendored gate sees the environment it always did, so its suite and its
# pass stamp are unchanged. The a11y step isn't stamped: its result depends on
# the test DB and the browser, which no tree key sees.
#
# Exit codes, the vendored gate's table:
#   0  the a11y step passed or didn't apply, and the vendored gate passed
#   1  a tier found a failure (the vendored gate still runs), or the gate failed
#   2  infrastructure: no browser, no TEST_DATABASE_URL, DB unreachable, a tier
#      that errored without failing a test, a vacuous tier, the lock not
#      acquired, the vendored gate missing, a bad knob
#
# Knobs:
#   PRE_SHIP_A11Y=on            run the tiers whatever the diff
#   PRE_SHIP_A11Y_BASE=REF      the base the diff is taken against (origin/main)
#   PRE_SHIP_A11Y_LOCK_WAIT=N   seconds to wait for another run's lock (900)
#
# Usage: bash scripts/pre-ship.sh [--help]   (any argument goes to the vendored
# gate untouched, and runs no tier)
set -uo pipefail

PROJECT_ROOT=$(git rev-parse --show-toplevel 2>/dev/null) || {
    echo "ERROR: not inside a git repository" >&2
    exit 2
}
cd "$PROJECT_ROOT" || exit 2

DELEGATE="skills/shipping-work/scripts/pre-ship.sh"
SCOPE_FILE="scripts/pre-ship-a11y-paths"
BASE_REF="${PRE_SHIP_A11Y_BASE:-origin/main}"
LOCK_WAIT="${PRE_SHIP_A11Y_LOCK_WAIT:-900}"
UV_GROUPS=(--group browser --group seed)

say() { echo "a11y: $*"; }
die() {
    echo "a11y: $*" >&2
    exit 2
}

usage() {
    echo "scripts/pre-ship.sh: both a11y tiers on an admin diff, then the vendored gate (#740)."
    echo "  PRE_SHIP_A11Y=on       force the tiers"
    echo "  PRE_SHIP_A11Y_BASE     diff base (default origin/main)"
    echo "  PRE_SHIP_A11Y_LOCK_WAIT seconds to wait for the shared test-DB lock (default 900)"
    echo "  The admin surface: $SCOPE_FILE"
    echo ""
}

[[ -f "$DELEGATE" ]] || {
    echo "ERROR: vendored gate missing at $DELEGATE" >&2
    echo "       fix: git submodule update --init --recursive" >&2
    exit 2
}

if [[ $# -gt 0 ]]; then
    for arg in "$@"; do
        [[ "$arg" == --help ]] && usage
    done
    exec bash "$DELEGATE" "$@"
fi

# --- Should the tiers run? -----------------------------------------------------

SCOPE=()
read_scope() {
    local line
    [[ -r "$SCOPE_FILE" ]] || return 1
    while IFS= read -r line || [[ -n "$line" ]]; do
        line="${line#"${line%%[![:space:]]*}"}"
        line="${line%"${line##*[![:space:]]}"}"
        case "$line" in '' | \#*) continue ;; esac
        SCOPE+=("$line")
    done <"$SCOPE_FILE"
    [[ ${#SCOPE[@]} -gt 0 ]]
}

# An entry ending in / is a directory prefix; any other is an exact path.
in_scope() {
    local path="$1" entry
    for entry in "${SCOPE[@]}"; do
        case "$entry" in
            */) [[ "$path" == "$entry"* ]] && return 0 ;;
            *) [[ "$path" == "$entry" ]] && return 0 ;;
        esac
    done
    return 1
}

# Both sides of a rename or a deletion count (--no-renames), and so does the
# working tree, which a ship can still carry. NUL-delimited (-z): without it
# git C-quotes a non-ASCII path, which then matches no prefix (CR 1).
changed_paths() {
    local base
    base=$(git merge-base "$BASE_REF" HEAD 2>/dev/null) || return 1
    git diff -z --name-only --no-renames "$base" HEAD -- || return 1
    git diff -z --name-only --no-renames HEAD -- || return 1
    git ls-files -z --others --exclude-standard || return 1
}

# Sets REASON. Exit 0: run the tiers.
decide() {
    local paths_file path hits=()
    if ! read_scope; then
        REASON="cannot tell what changed ($SCOPE_FILE missing or empty), so both tiers run"
        return 0
    fi
    paths_file=$(mktemp) || {
        REASON="cannot tell what changed (mktemp failed), so both tiers run"
        return 0
    }
    if ! changed_paths >"$paths_file"; then
        rm -f "$paths_file"
        REASON="cannot tell what changed (no merge base with $BASE_REF), so both tiers run"
        return 0
    fi
    while IFS= read -r -d '' path; do
        [[ -n "$path" ]] && in_scope "$path" && hits+=("$path")
    done < <(sort -zu "$paths_file")
    rm -f "$paths_file"
    if [[ ${#hits[@]} -eq 0 ]]; then
        REASON="not run: nothing in $SCOPE_FILE changed since $BASE_REF"
        return 1
    fi
    REASON="the admin surface changed (${#hits[@]} paths, first ${hits[0]}), so both tiers run"
    return 0
}

case "${PRE_SHIP_A11Y:-}" in
    '')
        if decide; then RUN=1; else RUN=0; fi
        ;;
    on)
        RUN=1
        REASON="PRE_SHIP_A11Y=on, so both tiers run"
        ;;
    *)
        die "PRE_SHIP_A11Y='${PRE_SHIP_A11Y}' is not on or unset"
        ;;
esac

say "$REASON"
[[ $RUN -eq 1 ]] || exec bash "$DELEGATE"

# --- One run at a time on the shared test DB ------------------------------------

command -v flock >/dev/null 2>&1 || die "flock not found (util-linux); the tiers can't be serialized"
[[ "$LOCK_WAIT" =~ ^[0-9]+$ ]] || die "PRE_SHIP_A11Y_LOCK_WAIT='$LOCK_WAIT' is not a whole number of seconds"
COMMON_DIR=$(git rev-parse --path-format=absolute --git-common-dir) || die "can't find the git common dir"
LOCK="$COMMON_DIR/pre-ship-a11y.lock"
exec 9>>"$LOCK" || die "can't open $LOCK"
if ! flock -n 9; then
    say "waiting up to ${LOCK_WAIT}s for another a11y run to release $LOCK"
    flock -w "$LOCK_WAIT" 9 ||
        die "another a11y run still holds $LOCK after ${LOCK_WAIT}s; nothing ran. Retry, or raise PRE_SHIP_A11Y_LOCK_WAIT."
fi

TMP=$(mktemp -d) || die "mktemp failed"
trap 'rm -rf "$TMP"' EXIT

env_args=()
ETC_ENV=/etc/power-map/.env
if [[ -r "$ETC_ENV" ]]; then
    env_args+=(--env-file "$ETC_ENV")
elif [[ -e "$ETC_ENV" ]]; then
    echo "a11y: WARN: $ETC_ENV exists but isn't readable" >&2
fi
LOCAL_ENV="$PROJECT_ROOT/.env"
[[ -r "$LOCAL_ENV" ]] || LOCAL_ENV="$(dirname "$COMMON_DIR")/.env"
[[ -r "$LOCAL_ENV" ]] && env_args+=(--env-file "$LOCAL_ENV")

# --- Preflight: everything a tier could skip on -----------------------------------

say "preflight: TEST_DATABASE_URL, the test DB, Chromium"
uv run "${UV_GROUPS[@]}" "${env_args[@]}" python - 9>&- <<'PY'
import asyncio
import os
import sys

dsn = os.environ.get("TEST_DATABASE_URL")
if not dsn:
    sys.exit(3)

import asyncpg


async def ping() -> None:
    conn = await asyncpg.connect(dsn, timeout=15)
    try:
        await conn.fetchval("SELECT 1")
    finally:
        await conn.close()


try:
    asyncio.run(ping())
except Exception as exc:  # noqa: BLE001 - any failure here is "unreachable"
    print(f"test DB: {type(exc).__name__}: {exc}", file=sys.stderr)
    sys.exit(4)

try:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        p.chromium.launch().close()
except Exception as exc:  # noqa: BLE001 - ImportError or a launch failure
    print(f"Chromium: {type(exc).__name__}: {exc}", file=sys.stderr)
    sys.exit(5)
print("preflight OK")
PY
rc=$?
case "$rc" in
    0) ;;
    3) die "TEST_DATABASE_URL is unset (it lives in $ETC_ENV). Both tiers would skip and pass vacuously, so nothing ran." ;;
    4) die "the test DB is unreachable. This is infrastructure, not an a11y finding: see docs/RUNBOOK_DB_TRIAGE.md." ;;
    5) die "Playwright Chromium unavailable; the browser tier would skip and pass vacuously. Fix: uv run --group browser playwright install chromium" ;;
    *) die "preflight failed (exit $rc)" ;;
esac

# --- The tiers ------------------------------------------------------------------------

# Exit 0 pass, 1 an a11y failure, 2 infrastructure (and stop).
run_tier() {
    local name="$1" log="$TMP/$1.log" rc
    shift
    say "running the $name tier"
    uv run "${UV_GROUPS[@]}" "${env_args[@]}" pytest "$@" --no-header -q -p no:cacheprovider 9>&- 2>&1 |
        tee "$log"
    rc=${PIPESTATUS[0]}
    case "$rc" in
        0)
            grep -Eq '[0-9]+ passed' "$log" ||
                die "the $name tier passed nothing: a vacuous pass, not a green one"
            return 0
            ;;
        1)
            # Errors with no failed test never reached an assertion: a DB that
            # dropped after the preflight looks like this (#744's 215 errors).
            if grep -Eq '[0-9]+ errors?\b' "$log" && ! grep -Eq '[0-9]+ failed' "$log"; then
                die "the $name tier errored in setup without failing a test. That is infrastructure or a fixture, not an a11y finding: see docs/RUNBOOK_DB_TRIAGE.md if the errors are connection timeouts."
            fi
            return 1
            ;;
        *) die "the $name tier did not run to a verdict (pytest exit $rc)" ;;
    esac
}

run_tier lxml tests/api/admin/test_a11y_render.py -m integration
lxml_rc=$?
run_tier browser tests/api/admin/ -m browser
browser_rc=$?

exec 9>&-

if [[ $lxml_rc -eq 0 && $browser_rc -eq 0 ]]; then
    say "both tiers green"
    exec bash "$DELEGATE"
fi

say "FAILED (lxml exit $lxml_rc, browser exit $browser_rc). The vendored gate runs anyway, so one run shows everything."
bash "$DELEGATE"
gate_rc=$?
say "FAILED: the a11y tiers above failed (vendored gate exit $gate_rc)"
[[ $gate_rc -le 1 ]] && exit 1
exit "$gate_rc"
