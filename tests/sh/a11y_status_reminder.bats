#!/usr/bin/env bats
# Tests for .claude/hooks/a11y-status-reminder.sh (#373, hook from #369).
#
# systemctl is a PATH shim (tests/sh/stubs/systemctl); the hook runs against a
# minimal PATH so the host's real systemd is never consulted.

load helpers

setup() {
    setup_reminder_paths
    unset SYSTEMCTL_STUB_CAT_RC SYSTEMCTL_STUB_STATE SYSTEMCTL_STUB_SHOW_TS
}

@test "no systemctl on PATH (off-VM) → silent exit 0" {
    run_hook_without_systemctl
    [ "$status" -eq 0 ]
    [ -z "$output" ]
}

@test "unit not installed (systemctl cat fails) → silent exit 0" {
    export SYSTEMCTL_STUB_CAT_RC=4
    run_hook_with_systemctl
    [ "$status" -eq 0 ]
    [ -z "$output" ]
}

@test "unit failed → emits the warning line, exit 0" {
    export SYSTEMCTL_STUB_STATE=failed
    run_hook_with_systemctl
    [ "$status" -eq 0 ]
    [[ "$output" == *"Weekly a11y sweep (power-map-a11y) is FAILED"* ]]
    [[ "$output" == *"journalctl -u power-map-a11y"* ]]
    [[ "$output" == *"a11y-regression"* ]]
}

@test "healthy and ran recently → silent exit 0" {
    export SYSTEMCTL_STUB_STATE=active
    SYSTEMCTL_STUB_SHOW_TS="$(date -u -d '2 days ago' '+%a %F %T UTC')"
    export SYSTEMCTL_STUB_SHOW_TS
    run_hook_with_systemctl
    [ "$status" -eq 0 ]
    [ -z "$output" ]
}

@test "never run (empty timestamp) → first-run note, exit 0" {
    export SYSTEMCTL_STUB_STATE=active
    export SYSTEMCTL_STUB_SHOW_TS=""
    run_hook_with_systemctl
    [ "$status" -eq 0 ]
    [[ "$output" == *"has not run yet"* ]]
    [[ "$output" == *"sudo systemctl start power-map-a11y.service"* ]]
}

@test "last run over 8 days ago → staleness note, exit 0" {
    export SYSTEMCTL_STUB_STATE=active
    SYSTEMCTL_STUB_SHOW_TS="$(date -u -d '9 days ago' '+%a %F %T UTC')"
    export SYSTEMCTL_STUB_SHOW_TS
    run_hook_with_systemctl
    [ "$status" -eq 0 ]
    [[ "$output" == *"over 8 days ago"* ]]
    [[ "$output" == *"systemctl list-timers power-map-a11y.timer"* ]]
}

@test "last run just under 8 days ago → still silent (boundary)" {
    export SYSTEMCTL_STUB_STATE=active
    SYSTEMCTL_STUB_SHOW_TS="$(date -u -d '7 days ago' '+%a %F %T UTC')"
    export SYSTEMCTL_STUB_SHOW_TS
    run_hook_with_systemctl
    [ "$status" -eq 0 ]
    [ -z "$output" ]
}

@test "unparseable timestamp → silent exit 0 (epoch-0 guard)" {
    export SYSTEMCTL_STUB_STATE=active
    export SYSTEMCTL_STUB_SHOW_TS="not-a-date"
    run_hook_with_systemctl
    [ "$status" -eq 0 ]
    [ -z "$output" ]
}
