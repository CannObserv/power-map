#!/usr/bin/env bash
# Weekly a11y sweep (GH #369) — run by power-map-a11y.service.
#
# Runs both accessibility tiers against the dedicated test DB and surfaces the
# result for operator review:
#   - lxml rendered-DOM tier      (tests/api/admin/test_a11y_render.py  -m integration, #246)
#   - Playwright/axe browser tier (tests/api/admin/test_a11y_browser.py -m browser,    #300)
#
# Output: everything goes to stdout/stderr, which systemd captures to the journal
# (`journalctl -u power-map-a11y`). The full failing detail (axe violations,
# tracebacks) stays there, on the VM — it is NEVER posted to GitHub, because
# CannObserv/power-map is a PUBLIC repo (#369 CR).
#
# Surfacing (two layers, see docs/COMMANDS.md § weekly a11y sweep timer):
#   - Durable: on failure, open-or-update a single GitHub issue labelled
#     `a11y-regression` (dedup → one issue, not spam) carrying only a one-line
#     summary + a pointer to the journal; on recovery, comment + close it.
#     GitHub's own notification emails cover the "email me" need — no MTA here.
#   - Ambient: `systemctl --failed` (this unit) drives the SessionStart hook note.
#
# Exit codes: 0 = both tiers green; 1 = a tier failed; 2 = environment guard
# failed (Chromium missing → the browser tier would importorskip and pass
# vacuously, which must fail loudly instead). systemd marks the unit failed on
# any non-zero, so it shows in `systemctl --failed`.
#
# Test hatches:
#   A11Y_SWEEP_NO_GH=1      log the GitHub actions instead of performing them.
#   A11Y_SWEEP_FORCE_FAIL=1 skip guard+tiers and exercise the failure surfacing
#                           (synthetic — for verifying the open/close cycle).
set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

LABEL="a11y-regression"

log() { echo "[$(date -u +%FT%TZ)] $*"; }

env_args=()
[ -f /etc/power-map/.env ] && env_args+=(--env-file /etc/power-map/.env)
[ -f "$REPO_ROOT/.env" ] && env_args+=(--env-file "$REPO_ROOT/.env")

# --- GitHub surfacing ------------------------------------------------------
# gh_surface <fail|pass> [summary]
# On fail: open-or-update the labelled issue with SUMMARY ONLY (never raw output
# — public repo). On pass: close any open one. Best-effort — never aborts the run.
gh_surface() {
    local status="$1" summary="${2:-}" ts host existing body
    ts="$(date -u +%FT%TZ)"
    host="$(hostname)"

    if [ -n "${A11Y_SWEEP_NO_GH:-}" ]; then
        log "A11Y_SWEEP_NO_GH set — would surface status=$status (${summary:-n/a}) to label '$LABEL'"
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
        # Summary + journal pointer ONLY — no raw internals (public repo, #369 CR).
        body="$(printf '**Weekly a11y sweep FAILED** — %s (host `%s`).\n\n%s\n\nFull output is on the VM: `journalctl -u power-map-a11y` (deliberately not published — this is a public repo). Automated by `power-map-a11y.timer` (#369).' \
            "$ts" "$host" "${summary:-a11y sweep failed}")"
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

# --- Test hatch: exercise failure surfacing without breaking a tier ---------
# Skips the guard + tiers and surfaces a synthetic failure, so the open/update →
# recover/close cycle can be verified in seconds (#369 CR finding 3).
if [ -n "${A11Y_SWEEP_FORCE_FAIL:-}" ]; then
    log "A11Y_SWEEP_FORCE_FAIL set — exercising failure surfacing (synthetic, not a real regression)"
    gh_surface fail "synthetic failure (A11Y_SWEEP_FORCE_FAIL) — surfacing self-test, ignore."
    exit 1
fi

# --- Guard: Chromium must actually launch --------------------------------
# The browser tier importorskips when Playwright/Chromium is absent, which would
# report "skipped" (exit 0) and pass vacuously. Launch a real browser here so a
# missing install fails the run loudly (#369).
log "verifying Playwright Chromium is installed"
if ! uv run --group browser "${env_args[@]}" python - <<'PY'; then
from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    browser = p.chromium.launch()
    browser.close()
print("chromium OK")
PY
    log "FATAL: Playwright Chromium unavailable — run: uv run --group browser playwright install chromium"
    gh_surface fail "Chromium guard failed — Playwright browser unavailable on the VM."
    exit 2
fi

# --- Run both tiers (separate pytest sessions: distinct DB-isolation models) ---
# Output streams to stdout → journald; nothing is buffered to a file.
run_tier() {
    local name="$1"
    shift
    echo "===== ${name} ====="
    uv run --group browser "${env_args[@]}" pytest "$@"
    local rc=$?
    echo "${name} exit: ${rc}"
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
    gh_surface fail "a11y tiers failed — lxml exit ${lxml_rc}, browser exit ${browser_rc}. See the journal for the failing assertions."
else
    log "a11y sweep GREEN (lxml=$lxml_rc browser=$browser_rc)"
    gh_surface pass
fi

exit "$overall"
