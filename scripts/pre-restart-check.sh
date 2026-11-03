#!/usr/bin/env bash
# Check the database is reachable before restarting power-map (#748).
#
# Usage — run it ahead of every restart or start of the service:
#   bash scripts/pre-restart-check.sh && sudo systemctl restart power-map
#   bash scripts/pre-restart-check.sh --force    # report, then proceed anyway
#
# Why. ExecStartPre=apply-schema.sh connects before the server starts. While
# the database is unreachable (egress-IP drift, #410) that connect hangs for
# asyncpg's 60 s default, so each attempt takes ~65 s and StartLimitBurst=10 in
# 600 s never trips: the unit loops in start-pre until the database returns,
# then applies any pending schema with nobody watching. A degraded API (up,
# /ready 503) becomes a down one. On 2026-11-01 that lasted ~16 h.
#
# The verdict comes from `apply-schema.sh --probe`: the DSN that script
# resolves, so the connect the restart will make. /ready is not the gate — it
# probes the app pool, the restart connects as the migrations user, and on
# docs/RUNBOOK_DB_TRIAGE.md's credential-rotation path /ready is failing while
# the restart is the fix. It is reported as context only, so a stopped or
# stuck service (:8000 down) is no obstacle to the check.
#
# Read-only. Prints no DSN beyond apply-schema.sh's redacted target line.
#
# Exit codes: 0 go (or --force), 1 usage error or the probe could not run,
# 3 refuse: the database cannot be reached.

set -euo pipefail

FORCE=0
# POWER_MAP_READY_URL is a test seam. Production never sets it.
READY_URL="${POWER_MAP_READY_URL:-http://localhost:8000/ready}"
TRIAGE=docs/RUNBOOK_DB_TRIAGE.md

usage() {
    cat <<'EOF'
usage: bash scripts/pre-restart-check.sh [--force]

  (no flags)  probe the database the restart will connect to; exit 3 when it
              cannot be reached, so `&& sudo systemctl restart power-map` stops
  --force     report the same, then exit 0 so the restart proceeds anyway
EOF
}

while [ $# -gt 0 ]; do
    case "$1" in
        --force)   FORCE=1 ;;
        -h|--help) usage; exit 0 ;;
        *)         echo "unknown option: $1" >&2; usage >&2; exit 1 ;;
    esac
    shift
done

script_dir="${BASH_SOURCE[0]}"
case "$script_dir" in
    */*) script_dir="${script_dir%/*}" ;;
    *)   script_dir="." ;;
esac

# ── Context: what the running service says ───────────────────────────────────
ready_body="$(curl -sS -m 3 -w '\n%{http_code}' "$READY_URL" 2>/dev/null || true)"
ready_code="${ready_body##*$'\n'}"
case "$ready_code" in
    200) echo "service: /ready ok" >&2 ;;
    ""|000)
        echo "service: not answering on ${READY_URL} (stopped, or stuck in start-pre)" >&2 ;;
    *)
        reason="$(printf '%s' "$ready_body" | grep -oE '"reason" *: *"[a-z_]+"' | grep -oE '[a-z_]+"$' | tr -d '"' || true)"
        echo "service: /ready ${ready_code} (${reason:-no reason given})" >&2 ;;
esac

# ── Verdict: can the restart's own connect succeed? ──────────────────────────
rc=0
bash "$script_dir/apply-schema.sh" --probe || rc=$?

case "$rc" in
    0)
        echo "go: the database is reachable — the restart's schema apply can connect" >&2
        exit 0 ;;
    3)
        why="the database is unreachable"
        code=3 ;;
    *)
        why="the reachability probe could not run (exit ${rc})"
        code=1 ;;
esac

if [ "$FORCE" -eq 1 ]; then
    cat >&2 <<EOF
override: --force given although ${why}. A restart now loops in start-pre
until the database returns, then applies any pending schema unwatched (#748).
EOF
    exit 0
fi

cat >&2 <<EOF
refusing: ${why}. A restart now would loop in start-pre until the database
returns, then apply any pending schema unwatched (#748). Leave the service up:
a degraded API is better than a down one.

  triage:          ${TRIAGE}
  restart anyway:  bash scripts/pre-restart-check.sh --force && sudo systemctl restart power-map
EOF
exit "$code"
