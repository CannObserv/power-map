#!/usr/bin/env bats
# Tests for scripts/claude-link-canonical.sh (#542).
#
# Hermetic: HOME is a tmpdir, the canonical launcher is a stub that prints a
# version, and each "extension" is a directory shaped like the VS Code
# extension's (anthropic.claude-code-<ver>-<platform>/resources/native-binary/
# claude) holding a regular file. No real Claude binary is run or touched.

load helpers

setup() {
    export HOME="$BATS_TEST_TMPDIR/home"
    EXT_ROOT="$HOME/.vscode-server/extensions"
    mkdir -p "$EXT_ROOT" "$HOME/.local/share/claude/versions" "$HOME/.local/bin"
    make_canonical 2.1.300
    export CLAUDE_CANONICAL="$HOME/.local/bin/claude"
    export CLAUDE_EXTENSION_ROOTS="$EXT_ROOT"
    SCRIPT="$(repo_root)/scripts/claude-link-canonical.sh"
}

# The native layout: a versioned binary behind the ~/.local/bin launcher link.
make_canonical() {
    local version="$1" bin="$HOME/.local/share/claude/versions/$1"
    printf '#!/usr/bin/env bash\necho "%s (Claude Code)"\n' "$version" >"$bin"
    chmod +x "$bin"
    ln -sfn "$bin" "$HOME/.local/bin/claude"
}

# An extension directory with its bundled binary as a regular file.
make_extension() {
    local dir="$EXT_ROOT/anthropic.claude-code-$1-linux-x64/resources/native-binary"
    mkdir -p "$dir"
    printf '#!/usr/bin/env bash\necho "%s (Claude Code)"\n' "$1" >"$dir/claude"
    chmod +x "$dir/claude"
    echo "$dir/claude"
}

# --- dry run (the default) ----------------------------------------------------

@test "default is a dry run: names the target, changes nothing" {
    bin="$(make_extension 2.1.280)"
    run bash "$SCRIPT"
    [ "$status" -eq 0 ]
    [[ "$output" == *"canonical: $CLAUDE_CANONICAL"* ]]
    [[ "$output" == *"would link"*"$bin"* ]]
    [[ "$output" == *"--execute"* ]]
    [ ! -L "$bin" ]
}

# --- --execute ---------------------------------------------------------------

@test "--execute repoints a bundled binary at the canonical launcher" {
    bin="$(make_extension 2.1.280)"
    run bash "$SCRIPT" --execute
    [ "$status" -eq 0 ]
    [ -L "$bin" ]
    [ "$(readlink "$bin")" = "$CLAUDE_CANONICAL" ]
    [ "$("$bin" --version)" = "2.1.300 (Claude Code)" ]
}

@test "--execute links the launcher, not the version it resolves to today" {
    # Auto-update repoints ~/.local/bin/claude; the extension must follow it.
    bin="$(make_extension 2.1.280)"
    bash "$SCRIPT" --execute
    make_canonical 2.1.310
    [ "$("$bin" --version)" = "2.1.310 (Claude Code)" ]
}

@test "--execute is idempotent: a second run reports already linked" {
    make_extension 2.1.280 >/dev/null
    bash "$SCRIPT" --execute
    run bash "$SCRIPT" --execute
    [ "$status" -eq 0 ]
    [[ "$output" == *"linked"* ]]
    [[ "$output" != *"relinked"* ]]
}

@test "--execute refuses to downgrade an extension newer than the canonical" {
    bin="$(make_extension 2.1.400)"
    run bash "$SCRIPT" --execute
    [ "$status" -eq 1 ]
    [[ "$output" == *"2.1.400"*"2.1.300"* ]]
    [[ "$output" == *"claude update"* ]]
    [ ! -L "$bin" ]
}

@test "--execute handles every installed extension version" {
    old="$(make_extension 2.1.270)"
    new="$(make_extension 2.1.280)"
    bash "$SCRIPT" --execute
    [ -L "$old" ]
    [ -L "$new" ]
}

@test "--execute refuses a missing canonical launcher" {
    make_extension 2.1.280 >/dev/null
    rm "$HOME/.local/bin/claude"
    run bash "$SCRIPT" --execute
    [ "$status" -eq 2 ]
    [[ "$output" == *"claude install"* ]]
}

@test "--execute refuses a canonical that resolves into an extension (a loop)" {
    bin="$(make_extension 2.1.280)"
    ln -sfn "$bin" "$HOME/.local/bin/claude"
    run bash "$SCRIPT" --execute
    [ "$status" -eq 2 ]
    [[ "$output" == *"extension"* ]]
    [ ! -L "$bin" ]
}

# --- --check (the SessionStart hook) -------------------------------------------

@test "--check is silent when every extension is linked" {
    make_extension 2.1.280 >/dev/null
    bash "$SCRIPT" --execute
    run bash "$SCRIPT" --check
    [ "$status" -eq 0 ]
    [ -z "$output" ]
}

@test "--check names the drift and the fix, and never repairs" {
    bin="$(make_extension 2.1.290)"
    run bash "$SCRIPT" --check
    [ "$status" -eq 0 ]
    [[ "$output" == *"2.1.290"* ]]
    [[ "$output" == *"claude-link-canonical.sh --execute"* ]]
    [ ! -L "$bin" ]
}

@test "--check is silent with no canonical launcher (not this host's layout)" {
    make_extension 2.1.280 >/dev/null
    rm "$HOME/.local/bin/claude"
    run bash "$SCRIPT" --check
    [ "$status" -eq 0 ]
    [ -z "$output" ]
}

@test "--check is silent with no extension installed" {
    run bash "$SCRIPT" --check
    [ "$status" -eq 0 ]
    [ -z "$output" ]
}

# --- arguments -----------------------------------------------------------------

@test "an unknown flag is an error" {
    run bash "$SCRIPT" --bogus
    [ "$status" -eq 2 ]
    [[ "$output" == *"--bogus"* ]]
}

@test "--check and --execute together is an error" {
    run bash "$SCRIPT" --check --execute
    [ "$status" -eq 2 ]
}

# --- the SessionStart hook -------------------------------------------------------

@test "the hook runs --check: reports drift, never repairs" {
    bin="$(make_extension 2.1.290)"
    run bash "$(repo_root)/.claude/hooks/claude-canonical-check.sh"
    [ "$status" -eq 0 ]
    [[ "$output" == *"2.1.290"*"--execute"* ]]
    [ ! -L "$bin" ]
}

@test "the hook is registered in .claude/settings.json" {
    grep -q '"bash \\"${CLAUDE_PROJECT_DIR:-.}/.claude/hooks/claude-canonical-check.sh\\""' \
        "$(repo_root)/.claude/settings.json"
}
