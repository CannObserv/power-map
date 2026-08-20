#!/usr/bin/env bats
# Guards .claude/settings.json hook registrations and .claude/hooks/ symlinks.
#
# Two drifts this suite exists to catch, both found by audit rather than by any
# gate:
#
#   1. Command anchoring. The canonical form is
#      `bash "${CLAUDE_PROJECT_DIR:-.}/.claude/hooks/<script>.sh"` (managing-skills
#      #110). A bare `$CLAUDE_PROJECT_DIR` degrades to `/.claude/hooks/...` and
#      errors on every session start when the variable is unset; a cwd-relative
#      command silently runs the wrong file when the hook process starts anywhere
#      but the repo root. Both forms were live here simultaneously.
#   2. Dangling hook symlinks. A hook linked into skills-vendor/ fails as exit 127
#      naming a path `ls` plainly shows exists, on every session start or file
#      edit. .skills/doctor.sh heals these, but only once something runs it.
#
# Pure file inspection — no hook is executed, nothing is stubbed.

load helpers

# The hook symlinks point into skills-vendor/, which is uninitialized in a fresh
# worktree or a shallow CI clone — so they dangle for a reason that is not drift.
# .skills/doctor.sh is the project's own self-heal for exactly that state and is
# the documented Phase 1 preflight for every reviewing-*/shipping-* skill.
#
# Heal ONLY when something actually dangles. The doctor's mutating path runs
# `git submodule update --init --recursive` — a network clone of two submodules
# in a fresh worktree — and re-syncs `.skills/doctor.sh` from the vendored copy
# when the two differ. Calling it unconditionally would make this suite
# network-dependent on every run, contradicting the hermetic contract
# helpers.bash states, and would dirty the tree mid-commit in the window after a
# submodule bump. `bats` runs from pre-commit with always_run, so that cost
# would land on every commit.
#
# Best-effort even then: a doctor that cannot heal (offline) leaves the
# assertions below to fail with their own, more specific message.
setup_file() {
    local root entry
    root="$(repo_root)"
    for entry in "$root"/.claude/hooks/*; do
        if [ -L "$entry" ] && [ ! -e "$entry" ]; then
            bash "$root/.skills/doctor.sh" >/dev/null 2>&1 || true
            return 0
        fi
    done
}

setup() {
    SETTINGS="$(repo_root)/.claude/settings.json"
    HOOKS_DIR="$(repo_root)/.claude/hooks"
}

# Every command string registered in settings.json, one per line.
hook_commands() {
    jq -r '.hooks // {} | to_entries[] | .value[] | .hooks[]? | .command' "$SETTINGS"
}

@test "settings.json is valid JSON" {
    run jq empty "$SETTINGS"
    [ "$status" -eq 0 ]
}

@test "every hook command is anchored on \${CLAUDE_PROJECT_DIR:-.}" {
    local offenders=()
    while IFS= read -r cmd; do
        [ -z "$cmd" ] && continue
        # Only commands that reach into .claude/hooks/ are in scope.
        case "$cmd" in
            *".claude/hooks/"*) ;;
            *) continue ;;
        esac
        case "$cmd" in
            *'${CLAUDE_PROJECT_DIR:-.}'*) ;;
            *) offenders+=("$cmd") ;;
        esac
    done < <(hook_commands)

    if [ ${#offenders[@]} -gt 0 ]; then
        printf 'not anchored on ${CLAUDE_PROJECT_DIR:-.}:\n' >&2
        printf '  %s\n' "${offenders[@]}" >&2
    fi
    [ ${#offenders[@]} -eq 0 ]
}

@test "every .claude/hooks/ symlink resolves" {
    local dangling=()
    for entry in "$HOOKS_DIR"/*; do
        [ -e "$entry" ] || [ -L "$entry" ] || continue
        if [ -L "$entry" ] && [ ! -e "$entry" ]; then
            dangling+=("$(basename "$entry") -> $(readlink "$entry")")
        fi
    done

    if [ ${#dangling[@]} -gt 0 ]; then
        printf 'dangling hook symlinks:\n' >&2
        printf '  %s\n' "${dangling[@]}" >&2
    fi
    [ ${#dangling[@]} -eq 0 ]
}

@test "every registered hook script exists on disk" {
    local missing=()
    while IFS= read -r cmd; do
        [ -z "$cmd" ] && continue
        case "$cmd" in
            *".claude/hooks/"*) ;;
            *) continue ;;
        esac
        # Extract the .claude/hooks/<name> path out of the command string.
        local rel="${cmd#*.claude/hooks/}"
        rel="${rel%%[\" ]*}"
        [ -e "$HOOKS_DIR/$rel" ] || missing+=("$rel")
    done < <(hook_commands)

    if [ ${#missing[@]} -gt 0 ]; then
        printf 'registered but absent from .claude/hooks/:\n' >&2
        printf '  %s\n' "${missing[@]}" >&2
    fi
    [ ${#missing[@]} -eq 0 ]
}

@test "the SocratiCode health hook is registered" {
    run bash -c "jq -r '.hooks.SessionStart[]?.hooks[]?.command' '$SETTINGS' | grep -c 'socraticode-health'"
    [ "$status" -eq 0 ]
    [ "$output" -ge 1 ]
}

@test "the SocratiCode prefetch hook is registered" {
    run bash -c "jq -r '.hooks.SessionStart[]?.hooks[]?.command' '$SETTINGS' | grep -cE 'socraticode-reminder|socraticode-prefetch'"
    [ "$status" -eq 0 ]
    [ "$output" -ge 1 ]
}
