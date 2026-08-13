#!/usr/bin/env bats
# Tests for scripts/run-a11y-sweep.sh (#373, entrypoint from #369).
#
# All external commands are PATH shims (tests/sh/stubs/) — no real uv, gh,
# pytest, network, or DB. See helpers.bash for the knobs.

load helpers

setup() {
    setup_sweep_stubs
}

# --- exit-code matrix -------------------------------------------------------

@test "both tiers green → exit 0, GREEN summary, gh pass path" {
    run_sweep
    [ "$status" -eq 0 ]
    [[ "$output" == *"a11y sweep GREEN (lxml=0 browser=0)"* ]]
}

@test "lxml tier failure → exit 1, summary names the lxml tier's code" {
    export STUB_UV_LXML_RC=3
    run_sweep
    [ "$status" -eq 1 ]
    [[ "$output" == *"a11y sweep FAILED (lxml=3 browser=0)"* ]]
    # Body summary names which tier failed, with exit codes.
    grep -q "lxml exit 3, browser exit 0" "$GH_STUB_BODY_LOG"
}

@test "browser tier failure → exit 1, summary names the browser tier's code" {
    export STUB_UV_BROWSER_RC=2
    run_sweep
    [ "$status" -eq 1 ]
    [[ "$output" == *"a11y sweep FAILED (lxml=0 browser=2)"* ]]
    grep -q "lxml exit 0, browser exit 2" "$GH_STUB_BODY_LOG"
}

@test "both tiers failing → exit 1 (not 2)" {
    export STUB_UV_LXML_RC=1
    export STUB_UV_BROWSER_RC=1
    run_sweep
    [ "$status" -eq 1 ]
}

@test "Chromium guard failure → exit 2, surfaces guard summary, skips tiers" {
    export STUB_UV_GUARD_RC=1
    run_sweep
    [ "$status" -eq 2 ]
    [[ "$output" == *"FATAL: Playwright Chromium unavailable"* ]]
    grep -q "Chromium guard failed" "$GH_STUB_BODY_LOG"
    # Neither pytest tier ran.
    [ "$(call_count "$STUB_UV_CALL_LOG" pytest)" -eq 0 ]
}

@test "a failing tier does not stop the other tier from running" {
    export STUB_UV_LXML_RC=1
    run_sweep
    [ "$status" -eq 1 ]
    [ "$(call_count "$STUB_UV_CALL_LOG" test_a11y_render.py)" -eq 1 ]
    [ "$(call_count "$STUB_UV_CALL_LOG" test_a11y_browser.py)" -eq 1 ]
}

# --- gh_surface open/update/close dedup -------------------------------------

@test "first failure with no open issue → opens exactly one issue" {
    export STUB_UV_LXML_RC=1
    export GH_STUB_OPEN_ISSUE=""
    run_sweep
    [ "$status" -eq 1 ]
    [ "$(call_count "$GH_STUB_CALL_LOG" '^gh issue create')" -eq 1 ]
    [ "$(call_count "$GH_STUB_CALL_LOG" '^gh issue comment')" -eq 0 ]
    [[ "$output" == *"opened a11y-regression issue"* ]]
}

@test "failure with an open issue → comments on it, does NOT open a duplicate" {
    export STUB_UV_LXML_RC=1
    export GH_STUB_OPEN_ISSUE=42
    run_sweep
    [ "$status" -eq 1 ]
    [ "$(call_count "$GH_STUB_CALL_LOG" '^gh issue create')" -eq 0 ]
    [ "$(call_count "$GH_STUB_CALL_LOG" '^gh issue comment 42')" -eq 1 ]
    [[ "$output" == *"commented failure on #42"* ]]
}

@test "gh returns literal null for no open issue → treated as none (create, not comment)" {
    export STUB_UV_LXML_RC=1
    export GH_STUB_OPEN_ISSUE=null
    run_sweep
    [ "$status" -eq 1 ]
    [ "$(call_count "$GH_STUB_CALL_LOG" '^gh issue create')" -eq 1 ]
    [ "$(call_count "$GH_STUB_CALL_LOG" '^gh issue comment')" -eq 0 ]
}

@test "pass with an open issue → comments recovery and closes it" {
    export GH_STUB_OPEN_ISSUE=42
    run_sweep
    [ "$status" -eq 0 ]
    [ "$(call_count "$GH_STUB_CALL_LOG" '^gh issue comment 42')" -eq 1 ]
    [ "$(call_count "$GH_STUB_CALL_LOG" '^gh issue close 42')" -eq 1 ]
    grep -q "Recovered" "$GH_STUB_BODY_LOG"
    [[ "$output" == *"closed recovered issue #42"* ]]
}

@test "pass with no open issue → no gh issue mutations at all" {
    export GH_STUB_OPEN_ISSUE=""
    run_sweep
    [ "$status" -eq 0 ]
    [ "$(call_count "$GH_STUB_CALL_LOG" '^gh issue create')" -eq 0 ]
    [ "$(call_count "$GH_STUB_CALL_LOG" '^gh issue comment')" -eq 0 ]
    [ "$(call_count "$GH_STUB_CALL_LOG" '^gh issue close')" -eq 0 ]
}

@test "transient gh issue list failure → skips surfacing (no duplicate risk), keeps exit 1" {
    export STUB_UV_LXML_RC=1
    export GH_STUB_LIST_RC=4
    run_sweep
    [ "$status" -eq 1 ]
    [[ "$output" == *"skipping surfacing this run to avoid a duplicate"* ]]
    [ "$(call_count "$GH_STUB_CALL_LOG" '^gh issue create')" -eq 0 ]
    [ "$(call_count "$GH_STUB_CALL_LOG" '^gh issue comment')" -eq 0 ]
}

@test "missing label → creates it once (idempotent label bootstrap)" {
    export GH_STUB_LABELS=""
    export STUB_UV_LXML_RC=1
    run_sweep
    [ "$status" -eq 1 ]
    [ "$(call_count "$GH_STUB_CALL_LOG" '^gh label create a11y-regression')" -eq 1 ]
}

# --- public-repo body hygiene (#369 CR finding 1) ---------------------------

@test "issue body never contains raw test output (public repo)" {
    export STUB_UV_LXML_RC=1
    export STUB_UV_LXML_OUTPUT='FAILED test_a11y_render.py — Traceback (most recent call last): connection to postgres://user:sekret@db failed'
    run_sweep
    [ "$status" -eq 1 ]
    # The raw output reached the journal-bound stdout…
    [[ "$output" == *"Traceback"* ]]
    # …but none of it may appear in the GitHub issue body.
    # (`run` + status: bare `! grep` cannot fail a bats test — errexit exempts it.)
    [ -s "$GH_STUB_BODY_LOG" ]
    run grep -i -e traceback -e postgres -e sekret "$GH_STUB_BODY_LOG"
    [ "$status" -ne 0 ]
    # It carries the pointer to the journal instead.
    grep -q "journalctl -u power-map-a11y" "$GH_STUB_BODY_LOG"
}

# --- test hatches ------------------------------------------------------------

@test "A11Y_SWEEP_FORCE_FAIL=1 → exit 1, fail-surface path, uv never invoked" {
    export A11Y_SWEEP_FORCE_FAIL=1
    run_sweep
    [ "$status" -eq 1 ]
    [[ "$output" == *"synthetic"* ]]
    [ "$(call_count "$GH_STUB_CALL_LOG" '^gh issue create')" -eq 1 ]
    grep -q "synthetic failure (A11Y_SWEEP_FORCE_FAIL)" "$GH_STUB_BODY_LOG"
    # Guard and tiers are skipped entirely.
    [ ! -f "$STUB_UV_CALL_LOG" ]
}

@test "A11Y_SWEEP_NO_GH=1 on failure → logs intent, gh never invoked" {
    export A11Y_SWEEP_NO_GH=1
    export STUB_UV_LXML_RC=1
    run_sweep
    [ "$status" -eq 1 ]
    [[ "$output" == *"A11Y_SWEEP_NO_GH set — would surface status=fail"* ]]
    [ ! -f "$GH_STUB_CALL_LOG" ]
}

@test "A11Y_SWEEP_NO_GH=1 on pass → logs intent, gh never invoked, exit 0" {
    export A11Y_SWEEP_NO_GH=1
    run_sweep
    [ "$status" -eq 0 ]
    [[ "$output" == *"would surface status=pass"* ]]
    [ ! -f "$GH_STUB_CALL_LOG" ]
}
