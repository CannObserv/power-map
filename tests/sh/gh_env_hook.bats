#!/usr/bin/env bats
# Tests for .claude/hooks/gh-env.sh (#570): the SessionStart hook that puts the
# repo .env's GH_TOKEN into every agent Bash call, and warns on a `gh` too old
# for GitHub's API.
#
# Why each half exists:
#   - GH_TOKEN lived only in .env, exported by hand per command. A forgotten
#     export fell through to ~/.config/gh/hosts.yml, whose stored login had
#     gone stale — so it surfaced as `HTTP 401: Bad credentials`, not as the
#     missing export it was.
#   - Ubuntu's apt `gh` (2.45.0) requests the removed Projects (classic)
#     `projectCards` field, so `gh issue view` / `gh pr view` / `gh pr edit`
#     exit 1 (cli/cli#11992; fixed by cli/cli#11514 in v2.78.0).
#
# Hermetic: the hook reads POWER_MAP_PROJECT_ENV_FILE (a tmpdir file), writes
# to a tmpdir CLAUDE_ENV_FILE, and runs against a PATH whose `gh` is a stub.

load helpers

setup() {
    HOOK="$(repo_root)/.claude/hooks/gh-env.sh"
    SETTINGS="$(repo_root)/.claude/settings.json"
    PROJ="$BATS_TEST_TMPDIR/proj.env"
    ENVFILE="$BATS_TEST_TMPDIR/claude-env.sh"
    BIN="$BATS_TEST_TMPDIR/bin"
    mkdir -p "$BIN"
    write_gh_stub 2.101.0
}

# A `gh` whose `--version` reports $1, in the real CLI's two-line shape.
write_gh_stub() {
    cat >"$BIN/gh" <<EOF
#!/usr/bin/env bash
[ "\$1" = --version ] || exit 3
echo "gh version $1 (2026-09-15)"
echo "https://github.com/cli/cli/releases/tag/v$1"
EOF
    chmod +x "$BIN/gh"
}

# Run the hook the way the harness does: a fresh environment carrying only
# what SessionStart provides, plus the test's overrides. $@ are extra env vars.
run_hook() {
    run env -i PATH="$BIN:/usr/bin:/bin" HOME="$HOME" \
        POWER_MAP_PROJECT_ENV_FILE="$PROJ" "$@" bash "$HOOK"
}

# --- GH_TOKEN export --------------------------------------------------------

@test "exports the repo .env GH_TOKEN into CLAUDE_ENV_FILE" {
    printf 'GH_TOKEN_SKILLS=not-this\nGH_TOKEN=ghp_abc123\n' >"$PROJ"
    run_hook CLAUDE_ENV_FILE="$ENVFILE"
    [ "$status" -eq 0 ]
    run bash -c 'source "$1"; printf "%s" "$GH_TOKEN"' _ "$ENVFILE"
    [ "$output" = "ghp_abc123" ]
}

@test "the written line survives shell metacharacters in the value" {
    # A PAT never has these, but the env file is *sourced* before every Bash
    # call: an unquoted value would execute.
    printf 'GH_TOKEN=a b$(touch %s)`x`;"q\n' "$BATS_TEST_TMPDIR/pwned" >"$PROJ"
    run_hook CLAUDE_ENV_FILE="$ENVFILE"
    run bash -c 'source "$1"; printf "%s" "$GH_TOKEN"' _ "$ENVFILE"
    [ ! -e "$BATS_TEST_TMPDIR/pwned" ]
    [ "$output" = "a b\$(touch $BATS_TEST_TMPDIR/pwned)\`x\`;\"q" ]
}

@test "appends, never clobbering what other hooks wrote" {
    printf 'export OTHER_HOOK=kept\n' >"$ENVFILE"
    printf 'GH_TOKEN=ghp_abc123\n' >"$PROJ"
    run_hook CLAUDE_ENV_FILE="$ENVFILE"
    run bash -c 'source "$1"; printf "%s|%s" "$OTHER_HOOK" "$GH_TOKEN"' _ "$ENVFILE"
    [ "$output" = "kept|ghp_abc123" ]
}

@test "exports only GH_TOKEN — cross-repo tokens stay per-call" {
    printf 'GH_TOKEN=ghp_abc123\nGH_TOKEN_SKILLS=ghp_skills\nOTHER=x\n' >"$PROJ"
    run_hook CLAUDE_ENV_FILE="$ENVFILE"
    [ "$(grep -c . "$ENVFILE")" -eq 1 ]
    ! grep -q -e SKILLS -e OTHER "$ENVFILE"
}

@test "the token never reaches stdout or stderr" {
    printf 'GH_TOKEN=ghp_secret_value\n' >"$PROJ"
    run_hook CLAUDE_ENV_FILE="$ENVFILE"
    [[ "$output" != *ghp_secret_value* ]]
}

@test "silent when the export succeeds and gh is current" {
    printf 'GH_TOKEN=ghp_abc123\n' >"$PROJ"
    run_hook CLAUDE_ENV_FILE="$ENVFILE"
    [ "$status" -eq 0 ]
    [ -z "$output" ]
}

@test "a GH_TOKEN already in the environment is left alone" {
    printf 'GH_TOKEN=from-dotenv\n' >"$PROJ"
    run_hook CLAUDE_ENV_FILE="$ENVFILE" GH_TOKEN=from-parent
    [ "$status" -eq 0 ]
    [ ! -s "$ENVFILE" ]
}

@test "no GH_TOKEN in .env: says so, writes nothing, exits 0" {
    printf 'OTHER=1\n' >"$PROJ"
    run_hook CLAUDE_ENV_FILE="$ENVFILE"
    [ "$status" -eq 0 ]
    [ ! -s "$ENVFILE" ]
    [[ "$output" == *"GH_TOKEN"* ]]
    [[ "$output" == *".env"* ]]
}

@test "no .env at all: says so, exits 0" {
    run_hook CLAUDE_ENV_FILE="$ENVFILE"
    [ "$status" -eq 0 ]
    [ ! -s "$ENVFILE" ]
    [[ "$output" == *"GH_TOKEN"* ]]
}

@test "CLAUDE_ENV_FILE unset: names the manual export, exits 0" {
    # anthropics/claude-code#15840 / #11649: the harness sometimes hands a
    # SessionStart hook an empty CLAUDE_ENV_FILE.
    printf 'GH_TOKEN=ghp_abc123\n' >"$PROJ"
    run_hook
    [ "$status" -eq 0 ]
    [[ "$output" == *"CLAUDE_ENV_FILE"* ]]
    [[ "$output" == *"export GH_TOKEN="* ]]
    [[ "$output" != *ghp_abc123* ]]
}

# --- gh version floor -------------------------------------------------------

@test "warns when gh predates the Projects (classic) fix" {
    write_gh_stub 2.45.0
    printf 'GH_TOKEN=ghp_abc123\n' >"$PROJ"
    run_hook CLAUDE_ENV_FILE="$ENVFILE"
    [ "$status" -eq 0 ]
    [[ "$output" == *"2.45.0"* ]]
    [[ "$output" == *"2.78.0"* ]]
    [[ "$output" == *"gh api"* ]]
}

@test "the floor itself passes" {
    write_gh_stub 2.78.0
    printf 'GH_TOKEN=ghp_abc123\n' >"$PROJ"
    run_hook CLAUDE_ENV_FILE="$ENVFILE"
    [ -z "$output" ]
}

@test "compares versions numerically, not as strings" {
    # "2.9.0" sorts after "2.78.0" as text; it is older.
    write_gh_stub 2.9.0
    printf 'GH_TOKEN=ghp_abc123\n' >"$PROJ"
    run_hook CLAUDE_ENV_FILE="$ENVFILE"
    [[ "$output" == *"2.9.0"* ]]
}

@test "no gh on PATH: says so, still exports the token" {
    # /usr/bin holds the real gh on this VM, so build a PATH of just the core
    # tools the hook uses — everything but gh.
    rm "$BIN/gh"
    local tool
    for tool in bash dirname head sort; do
        ln -s "$(command -v "$tool")" "$BIN/$tool"
    done
    printf 'GH_TOKEN=ghp_abc123\n' >"$PROJ"
    run env -i PATH="$BIN" HOME="$HOME" POWER_MAP_PROJECT_ENV_FILE="$PROJ" \
        CLAUDE_ENV_FILE="$ENVFILE" bash "$HOOK"
    [ "$status" -eq 0 ]
    [[ "$output" == *"gh"*"not installed"* ]]
    run bash -c 'source "$1"; printf "%s" "$GH_TOKEN"' _ "$ENVFILE"
    [ "$output" = "ghp_abc123" ]
}

# --- registration -----------------------------------------------------------

@test "registered as a SessionStart hook in the anchored form" {
    run jq -r '.hooks.SessionStart[].hooks[].command' "$SETTINGS"
    [[ "$output" == *'bash "${CLAUDE_PROJECT_DIR:-.}/.claude/hooks/gh-env.sh"'* ]]
}
