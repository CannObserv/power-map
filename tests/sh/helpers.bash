# Shared bats helpers for the shell-entrypoint suite (#373).
#
# Everything is hermetic: external commands (uv, gh, systemctl) are PATH shims
# from tests/sh/stubs/, configured per-test through STUB_* / GH_STUB_* /
# SYSTEMCTL_STUB_* env vars and recording into $BATS_TEST_TMPDIR logs.
# Nothing real is invoked — no network, no GitHub, no systemd, no DB.

# Repo root, resolved from this file's location (tests/sh/ → two up).
repo_root() {
    cd "$BATS_TEST_DIRNAME/../.." && pwd
}

# Prepend the stub dir to PATH and point the recording logs at tmpdir.
# Call from setup() in suites that exercise scripts/run-a11y-sweep.sh.
setup_sweep_stubs() {
    STUBS_DIR="$BATS_TEST_DIRNAME/stubs"
    PATH="$STUBS_DIR:$PATH"
    export PATH

    export STUB_UV_CALL_LOG="$BATS_TEST_TMPDIR/uv-calls.log"
    export GH_STUB_CALL_LOG="$BATS_TEST_TMPDIR/gh-calls.log"
    export GH_STUB_BODY_LOG="$BATS_TEST_TMPDIR/gh-bodies.log"

    # Default stub posture: label exists, no open issue, gh healthy, tiers green.
    export GH_STUB_LABELS="a11y-regression"
    export GH_STUB_OPEN_ISSUE=""
    export GH_STUB_LIST_RC=0
    export STUB_UV_GUARD_RC=0
    export STUB_UV_LXML_RC=0
    export STUB_UV_BROWSER_RC=0

    # Make sure the script's own hatches don't leak in from the caller env.
    unset A11Y_SWEEP_NO_GH A11Y_SWEEP_FORCE_FAIL
}

run_sweep() {
    run bash "$(repo_root)/scripts/run-a11y-sweep.sh"
}

# Count invocations matching a grep pattern in a call log (0 if log absent).
call_count() {
    local log="$1" pattern="$2"
    if [ -f "$log" ]; then
        grep -c -- "$pattern" "$log" || true
    else
        echo 0
    fi
}

# --- reminder-hook helpers --------------------------------------------------

# Build a minimal PATH for .claude/hooks/a11y-status-reminder.sh: only the
# tools the hook needs (date), plus optionally the systemctl stub. Running with
# BASE_BIN alone simulates a host with no systemctl at all (off-VM).
setup_reminder_paths() {
    BASE_BIN="$BATS_TEST_TMPDIR/base-bin"
    mkdir -p "$BASE_BIN"
    # bash twice over: for `env … bash` itself and for the stubs' shebangs.
    for tool in bash date; do
        ln -s "$(command -v "$tool")" "$BASE_BIN/$tool"
    done
    STUBS_DIR="$BATS_TEST_DIRNAME/stubs"
    HOOK="$(repo_root)/.claude/hooks/a11y-status-reminder.sh"
}

run_hook_without_systemctl() {
    run env PATH="$BASE_BIN" bash "$HOOK"
}

run_hook_with_systemctl() {
    # env vars configuring the stub must already be exported by the test.
    run env PATH="$STUBS_DIR:$BASE_BIN" bash "$HOOK"
}
