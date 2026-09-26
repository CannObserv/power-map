#!/usr/bin/env bash
# load-env.sh — load power-map's two env files into the CURRENT shell (#570).
#
#   source scripts/load-env.sh                    # load both files
#   source scripts/load-env.sh --functions-only   # define the parser, load nothing
#
# Sourced, never executed: the exports have to land in the caller's shell.
# Ported from CannObserv/watcher's scripts/load-env.sh; tests/sh/load_env.bats
# carries the same contract. It replaces the idiom
#
#   export $(cat /etc/power-map/.env .env 2>/dev/null | xargs)     # do not use
#
# which, with both files absent, degrades to a bare `export` that prints every
# exported variable (secrets included) into the transcript; dies on a `#`
# comment line under `set -e`; and word-splits `PW=two words` into a wrong
# value, exit 0.
#
# Files load in the order docs/COMMANDS.md § Environment documents, later
# winning:
#   1. /etc/power-map/.env     production secrets, managed on the VM
#   2. <repo root>/.env        dev/agent secrets (GH_TOKEN*), git-ignored
#
# Both paths are overridable (POWER_MAP_SYSTEM_ENV_FILE /
# POWER_MAP_PROJECT_ENV_FILE) so tests never touch the real files.
#
# `--functions-only` is the library entry point: .claude/hooks/gh-env.sh reads
# one key with `env_file_value` under the same parse rules, without exporting
# the rest of either file into its own process.

# Parse one env file line by line, calling `$2 KEY VALUE` for each assignment.
# Never sources the file: a secrets file is data, and executing it would run any
# command substitution inside. A malformed line is skipped rather than fatal —
# a bad line in a secrets file must not decide whether the caller's command runs.
_env_file_each() {
    local file="$1" callback="$2" line key val
    [ -r "$file" ] || return 0
    while IFS= read -r line || [ -n "$line" ]; do
        line=${line#"${line%%[![:space:]]*}"}   # drop leading blanks
        case $line in '' | \#*) continue ;; esac  # blank or comment
        line=${line#export }                     # tolerate `export K=v`
        case $line in *=*) ;; *) continue ;; esac
        key=${line%%=*} val=${line#*=}
        key=${key%"${key##*[![:space:]]}"}       # drop trailing blanks on key
        case $key in '' | *[!A-Za-z0-9_]*) continue ;; esac
        case $val in                             # strip matched quotes
            \"*\") val=${val#\"} val=${val%\"} ;;
            \'*\') val=${val#\'} val=${val%\'} ;;
        esac
        "$callback" "$key" "$val"
    done <"$file"
}

_env_export() { export "$1=$2"; }  # quoted: spaces and globs survive

# Export every assignment in one env file. An absent file is not an error.
load_env_file() {
    _env_file_each "$1" _env_export
}

# Callback for env_file_value: writes through bash's dynamic scoping into the
# caller's locals `_want` / `_found` / `_value`.
_env_capture() {
    if [ "$1" = "$_want" ]; then
        _found=1 _value="$2"
    fi
}

# Print the value `load_env_file` would export for key $2 from file $1 — the
# last assignment wins, as it would when loading. Exit 1, printing nothing, when
# the file or the key is absent.
env_file_value() {
    local _want="$2" _found=0 _value=""
    _env_file_each "$1" _env_capture
    [ "$_found" -eq 1 ] || return 1
    printf '%s\n' "$_value"
}

if [ "${1:-}" != "--functions-only" ]; then
    load_env_file "${POWER_MAP_SYSTEM_ENV_FILE:-/etc/power-map/.env}"
    load_env_file "${POWER_MAP_PROJECT_ENV_FILE:-$(git rev-parse --show-toplevel 2>/dev/null || pwd)/.env}"
fi

# End on a success so that sourcing under `set -e` cannot kill the caller when
# the last file happened to be absent.
:
