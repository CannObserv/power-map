#!/usr/bin/env bash
# Weekly a11y sweep (GH #369) — run by power-map-a11y.service.
#
# Runs both accessibility tiers against the dedicated test DB and surfaces the
# result for operator review:
#   - lxml rendered-DOM tier   (tests/api/admin/test_a11y_render.py  -m integration, #246)
#   - Playwright/axe browser tier (tests/api/admin/test_a11y_browser.py -m browser,    #300)
#
# Surfacing (two layers, see docs/COMMANDS.md § Browser Testing):
#   - Durable: on failure, open-or-update a single GitHub issue labelled
#     `a11y-regression` (dedup → one issue, not spam); on recovery, comment + close
#     it. GitHub's own notification emails cover the "email me" need — no MTA here.
#   - Ambient: `systemctl --failed` (this unit) drives the SessionStart hook note.
#
# Exit codes: 0 = both tiers green; 1 = a tier failed; 2 = environment guard
# failed (Chromium missing → the browser tier would importorskip and pass
# vacuously, which must fail loudly instead). systemd marks the unit failed on
# any non-zero, so it shows in `systemctl --failed`.
#
# Testing: set A11Y_SWEEP_NO_GH=1 to log the GitHub actions instead of performing
# them (used when exercising the runner outside the timer).
set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

LABEL="a11y-regression"
LOG="$(mktemp -t a11y-sweep.XXXXXX.log)"
trap 'rm -f "$LOG"' EXIT

log() { echo "[$(date -u +%FT%TZ)] $*"; }

env_args=()
[ -f /etc/power-map/.env ] && env_args+=(--env-file /etc/power-map/.env)
[ -f "$REPO_ROOT/.env" ] && env_args+=(--env-file "$REPO_ROOT/.env")

# --- GitHub surfacing ------------------------------------------------------
# $1 = "fail" | "pass". On fail: open-or-update the labelled issue with a log
# tail. On pass: close any open one. Best-effort — never aborts the run.
gh_surface() {
    local status="$1" ts host body existing
    ts="$(date -u +%FT%TZ)"
    host="$(hostname)"

    if [ -n "${A11Y_SWEEP_NO_GH:-}" ]; then
        log "A11Y_SWEEP_NO_GH set — would surface status=$status to GitHub label '$LABEL'"
        return 0
    fi
    if ! command -v gh >/dev/null 2>&1; then
        log "gh not on PATH — skipping GitHub surfacing"
        return 0
    fi

    # Idempotent label create.
    if ! gh label list --limit 200 --json name -q '.[].name' 2>/dev/null | grep -qx "$LABEL"; then
        gh label create "$LABEL" --color B60205 \
            --description "Automated weekly a11y sweep regression (power-map-a11y.timer, #369)" \
            2>/dev/null || true
    fi
    existing="$(gh issue list --label "$LABEL" --state open --json number -q '.[0].number' 2>/dev/null || true)"

    if [ "$status" = "fail" ]; then
        body="$(printf '**Weekly a11y sweep FAILED** — %s (host \`%s\`)\n\nAutomated by `power-map-a11y.timer` (#369). Full run: `journalctl -u power-map-a11y`.\n\n```\n%s\n```' \
            "$ts" "$host" "$(tail -n 60 "$LOG")")"
        if [ -n "$existing" ]; then
            gh issue comment "$existing" --body "$body" >/dev/null && log "commented failure on #$existing"
        else
            gh issue create --title "a11y sweep regression (automated)" --label "$LABEL" \
                --body "$body" >/dev/null && log "opened a11y-regression issue"
        fi
    else
        if [ -n "$existing" ]; then
            gh issue comment "$existing" \
                --body "✅ **Recovered** — weekly a11y sweep GREEN as of $ts (host \`$host\`). Auto-closing." \
                >/dev/null
            gh issue close "$existing" >/dev/null && log "closed recovered issue #$existing"
        fi
    fi
}

# --- Guard: Chromium must actually launch --------------------------------
# The browser tier importorskips when Playwright/Chromium is absent, which would
# report "skipped" (exit 0) and pass vacuously. Launch a real browser here so a
# missing install fails the run loudly (#369).
log "verifying Playwright Chromium is installed"
if ! uv run --group browser "${env_args[@]}" python - <<'PY' >>"$LOG" 2>&1; then
from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    browser = p.chromium.launch()
    browser.close()
print("chromium OK")
PY
    log "FATAL: Playwright Chromium unavailable — run: uv run --group browser playwright install chromium"
    gh_surface fail
    exit 2
fi

# --- Run both tiers (separate pytest sessions: distinct DB-isolation models) ---
run_tier() {
    local name="$1"
    shift
    echo "===== ${name} =====" | tee -a "$LOG"
    uv run --group browser "${env_args[@]}" pytest "$@" >>"$LOG" 2>&1
    local rc=$?
    echo "${name} exit: ${rc}" | tee -a "$LOG"
    return "$rc"
}

log "running lxml render tier"
run_tier "lxml render tier" tests/api/admin/test_a11y_render.py -m integration --no-header -q -p no:cacheprovider
lxml_rc=$?

log "running browser axe tier"
run_tier "browser axe tier" tests/api/admin/test_a11y_browser.py -m browser --no-header -q -p no:cacheprovider
browser_rc=$?

overall=0
[ "$lxml_rc" -ne 0 ] && overall=1
[ "$browser_rc" -ne 0 ] && overall=1

if [ "$overall" -ne 0 ]; then
    log "a11y sweep FAILED (lxml=$lxml_rc browser=$browser_rc)"
    gh_surface fail
else
    log "a11y sweep GREEN (lxml=$lxml_rc browser=$browser_rc)"
    gh_surface pass
fi

exit "$overall"
