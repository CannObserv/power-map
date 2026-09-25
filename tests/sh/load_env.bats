#!/usr/bin/env bats
# Tests for scripts/load-env.sh (#570), ported from watcher's
# tests/scripts/test_load_env.py so the two parsers keep one contract.
#
# The idiom it replaces, `export $(cat /etc/power-map/.env .env | xargs)`, dumps
# every exported variable into the transcript when both files are absent, dies
# on a comment line under `set -e`, and word-splits `PW=two words`. Each class of
# test below pins one of those shut, plus the precedence the environment docs
# state: /etc/power-map/.env first, the repo .env second, later winning.
#
# Hermetic: both file paths are overridden into $BATS_TEST_TMPDIR, so the real
# /etc/power-map/.env and repo .env are never read.

load helpers

setup() {
    SCRIPT="$(repo_root)/scripts/load-env.sh"
    SYS="$BATS_TEST_TMPDIR/sys.env"
    PROJ="$BATS_TEST_TMPDIR/proj.env"
}

# Source the script in a clean shell with both paths pinned, then run $1.
source_and_probe() {
    run env -i PATH="$PATH" HOME="$HOME" SECRET_FROM_PARENT=leak \
        POWER_MAP_SYSTEM_ENV_FILE="$SYS" POWER_MAP_PROJECT_ENV_FILE="$PROJ" \
        bash -c 'set -euo pipefail; source "$1"; eval "$2"' _ "$SCRIPT" "$1"
}

# --- value parsing ----------------------------------------------------------

@test "a simple value is exported" {
    printf 'KEY_SIMPLE=plain\n' >"$SYS"
    source_and_probe 'echo "[$KEY_SIMPLE]"'
    [ "$status" -eq 0 ]
    [ "$output" = "[plain]" ]
}

@test "a value containing spaces is not split" {
    printf 'PW=two words\n' >"$SYS"
    source_and_probe 'echo "[$PW]"'
    [ "$output" = "[two words]" ]
}

@test "matched quotes are stripped" {
    printf 'DQ="has spaces"\nSQ='"'"'single'"'"'\n' >"$SYS"
    source_and_probe 'echo "[$DQ][$SQ]"'
    [ "$output" = "[has spaces][single]" ]
}

@test "a value containing = is preserved" {
    printf 'DSN=postgresql://u:p@h/db?opt=1\n' >"$SYS"
    source_and_probe 'echo "[$DSN]"'
    [ "$output" = "[postgresql://u:p@h/db?opt=1]" ]
}

@test "a glob value is not expanded" {
    printf 'GLOBBY=*.sh\n' >"$SYS"
    source_and_probe 'echo "[$GLOBBY]"'
    [ "$output" = "[*.sh]" ]
}

@test "an export prefix is tolerated" {
    printf 'export EXPORTED=viaexport\n' >"$SYS"
    source_and_probe 'echo "[$EXPORTED]"'
    [ "$output" = "[viaexport]" ]
}

@test "values are exported to child processes, not merely assigned" {
    printf 'CHILD_VISIBLE=yes\n' >"$SYS"
    source_and_probe 'bash -c '"'"'echo "[$CHILD_VISIBLE]"'"'"
    [ "$output" = "[yes]" ]
}

# --- malformed input is never fatal -----------------------------------------

@test "a comment line does not kill the caller under set -e" {
    printf '# a comment\nKEY=value\n' >"$SYS"
    source_and_probe 'echo "[$KEY]"'
    [ "$status" -eq 0 ]
    [ "$output" = "[value]" ]
}

@test "blank and whitespace-only lines are skipped" {
    printf '\n   \nKEY=value\n' >"$SYS"
    source_and_probe 'echo "[$KEY]"'
    [ "$status" -eq 0 ]
    [ "$output" = "[value]" ]
}

@test "a malformed key is skipped, not fatal" {
    printf 'BAD-KEY=skipped\nGOOD=kept\n' >"$SYS"
    source_and_probe 'echo "[$GOOD]"'
    [ "$status" -eq 0 ]
    [ "$output" = "[kept]" ]
}

@test "a line without = is skipped" {
    printf 'notakeyline\nGOOD=kept\n' >"$SYS"
    source_and_probe 'echo "[$GOOD]"'
    [ "$status" -eq 0 ]
    [ "$output" = "[kept]" ]
}

@test "a final line without a newline is read" {
    printf 'KEY=value' >"$SYS"
    source_and_probe 'echo "[$KEY]"'
    [ "$output" = "[value]" ]
}

@test "the secrets file is parsed as data, never executed" {
    local sentinel="$BATS_TEST_TMPDIR/pwned"
    printf 'EVIL=$(touch %s)\n' "$sentinel" >"$SYS"
    source_and_probe 'echo "[$EVIL]"'
    [ "$status" -eq 0 ]
    [ ! -e "$sentinel" ]
    [ "$output" = "[\$(touch $sentinel)]" ]
}

# --- missing files ----------------------------------------------------------

@test "both files absent is not an error" {
    source_and_probe 'echo done'
    [ "$status" -eq 0 ]
    [ "$output" = "done" ]
}

@test "both files absent does not dump the environment" {
    source_and_probe 'echo done'
    [[ "$output" != *SECRET_FROM_PARENT* ]]
}

# --- precedence -------------------------------------------------------------

@test "the repo .env overrides /etc/power-map/.env" {
    printf 'SHARED=from-system\nONLY_SYSTEM=sys\n' >"$SYS"
    printf 'SHARED=from-project\n' >"$PROJ"
    source_and_probe 'echo "[$SHARED][$ONLY_SYSTEM]"'
    [ "$output" = "[from-project][sys]" ]
}

# --- library mode (the gh-env hook's entry point) ---------------------------

@test "--functions-only defines the parser without loading either file" {
    printf 'SHOULD_NOT_LOAD=1\n' >"$SYS"
    printf 'SHOULD_NOT_LOAD=1\n' >"$PROJ"
    run env -i PATH="$PATH" HOME="$HOME" \
        POWER_MAP_SYSTEM_ENV_FILE="$SYS" POWER_MAP_PROJECT_ENV_FILE="$PROJ" \
        bash -c 'set -euo pipefail; source "$1" --functions-only
                 echo "[${SHOULD_NOT_LOAD:-unset}]"; type -t env_file_value' _ "$SCRIPT"
    [ "$status" -eq 0 ]
    [ "${lines[0]}" = "[unset]" ]
    [ "${lines[1]}" = "function" ]
}

@test "env_file_value prints one key's value, applying the same parse rules" {
    printf '# c\nGH_TOKEN_SKILLS=wrong\nexport GH_TOKEN="ghp_right value"\n' >"$PROJ"
    run bash -c 'source "$1" --functions-only; env_file_value "$2" GH_TOKEN' _ "$SCRIPT" "$PROJ"
    [ "$status" -eq 0 ]
    [ "$output" = "ghp_right value" ]
}

@test "env_file_value takes the last assignment, as loading would" {
    printf 'GH_TOKEN=first\nGH_TOKEN=second\n' >"$PROJ"
    run bash -c 'source "$1" --functions-only; env_file_value "$2" GH_TOKEN' _ "$SCRIPT" "$PROJ"
    [ "$output" = "second" ]
}

@test "env_file_value fails quietly on an absent key or file" {
    printf 'OTHER=1\n' >"$PROJ"
    run bash -c 'source "$1" --functions-only; env_file_value "$2" GH_TOKEN' _ "$SCRIPT" "$PROJ"
    [ "$status" -eq 1 ]
    [ -z "$output" ]
    run bash -c 'source "$1" --functions-only; env_file_value "$2" GH_TOKEN' _ "$SCRIPT" "$BATS_TEST_TMPDIR/nope"
    [ "$status" -eq 1 ]
    [ -z "$output" ]
}
