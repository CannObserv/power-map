#!/usr/bin/env bats
# Tests for scripts/claude-system.sh and its SessionStart hook (#542).
#
# Hermetic: the system prefix, the user's home and the extension root all live
# in $BATS_TEST_TMPDIR; the root check is bypassed with CLAUDE_SKIP_ROOT_CHECK.
# The "installer" is a stub that mimics the native one's observable contract,
# measured against the real binary in a throwaway HOME: `install <target>`
# writes $XDG_DATA_HOME/claude/versions/<v>, and creates ~/.local/bin/claude
# only when nothing is there — an existing launcher is never replaced.

load helpers

setup() {
    T="$BATS_TEST_TMPDIR"
    export CLAUDE_SYSTEM_BIN="$T/sys/bin/claude"
    export CLAUDE_SYSTEM_LIB="$T/sys/lib"
    export CLAUDE_USER_HOME="$T/home"
    export CLAUDE_EXTENSION_ROOTS="$T/home/.vscode-server/extensions"
    export CLAUDE_SKIP_ROOT_CHECK=1
    export CLAUDE_STUB_VERSION=2.1.300
    VERSIONS="$CLAUDE_SYSTEM_LIB/claude/versions"
    mkdir -p "$T/sys/bin" "$CLAUDE_USER_HOME/.local/bin" "$CLAUDE_EXTENSION_ROOTS"
    SCRIPT="$(repo_root)/scripts/claude-system.sh"
    HOOK="$(repo_root)/.claude/hooks/claude-canonical-check.sh"
}

# A stub Claude Code: `--version` prints the version it was installed as (its
# resolved file name under versions/), or, for a bundled binary, the version in
# its `.version` sidecar; `install` behaves like the native installer, copying
# itself. The version it installs comes from CLAUDE_STUB_VERSION.
write_stub() {
    local path="$1" version="$2"
    mkdir -p "$(dirname "$path")"
    cat >"$path" <<'EOF'
#!/usr/bin/env bash
self="$(readlink -f "$0")"
name="$(basename "$self")"
if [ "$1" = --version ]; then
    case "$name" in
        [0-9]*) echo "$name (Claude Code)" ;;
        *) echo "$(cat "$self.version") (Claude Code)" ;;
    esac
    exit 0
fi
if [ "$1" = install ]; then
    v="${CLAUDE_STUB_VERSION:?}"
    mkdir -p "$XDG_DATA_HOME/claude/versions" "$HOME/.local/bin"
    cp "$self" "$XDG_DATA_HOME/claude/versions/$v"
    [ -e "$HOME/.local/bin/claude" ] || [ -L "$HOME/.local/bin/claude" ] ||
        ln -s "$XDG_DATA_HOME/claude/versions/$v" "$HOME/.local/bin/claude"
    echo "Version: $v"
    exit 0
fi
exit 3
EOF
    chmod +x "$path"
    echo "$version" >"$path.version"
}

# The pre-#542 shape of this host: the system path points into user space.
legacy_layout() {
    write_stub "$CLAUDE_USER_HOME/.local/share/claude/versions/2.1.280" 2.1.280
    ln -s "$CLAUDE_USER_HOME/.local/share/claude/versions/2.1.280" "$CLAUDE_USER_HOME/.local/bin/claude"
    ln -s "$CLAUDE_USER_HOME/.local/bin/claude" "$CLAUDE_SYSTEM_BIN"
}

# An extension directory whose bundled binary is a regular file.
make_extension() {
    local bin="$CLAUDE_EXTENSION_ROOTS/anthropic.claude-code-$1-linux-x64/resources/native-binary/claude"
    write_stub "$bin" "$1"
    echo "$bin"
}

# The converged layout, built by the script itself.
converge() {
    run bash "$SCRIPT" --execute
    [ "$status" -eq 0 ]
}

# --- dry run (the default) ----------------------------------------------------

@test "default is a dry run: reports the plan, changes nothing" {
    legacy_layout
    bin="$(make_extension 2.1.280)"
    run bash "$SCRIPT"
    [ "$status" -eq 0 ]
    [[ "$output" == *"system: $CLAUDE_SYSTEM_BIN"* ]]
    [[ "$output" == *"would install"* ]]
    [[ "$output" == *"sudo bash scripts/claude-system.sh --execute"* ]]
    [ ! -d "$VERSIONS" ]
    [ "$(readlink "$CLAUDE_SYSTEM_BIN")" = "$CLAUDE_USER_HOME/.local/bin/claude" ]
    [ ! -L "$bin" ]
}

# --- --execute ---------------------------------------------------------------

@test "--execute refuses without root" {
    unset CLAUDE_SKIP_ROOT_CHECK
    legacy_layout
    run bash "$SCRIPT" --execute
    [ "$status" -eq 2 ]
    [[ "$output" == *"sudo"* ]]
}

@test "--execute installs into the system lib and points the system bin at it" {
    legacy_layout
    converge
    [ -x "$VERSIONS/2.1.300" ]
    [ "$(readlink "$CLAUDE_SYSTEM_BIN")" = "$VERSIONS/2.1.300" ]
    [ "$("$CLAUDE_SYSTEM_BIN" --version)" = "2.1.300 (Claude Code)" ]
}

@test "--execute flips the user launcher to the system bin" {
    legacy_layout
    converge
    [ "$(readlink "$CLAUDE_USER_HOME/.local/bin/claude")" = "$CLAUDE_SYSTEM_BIN" ]
    [ "$("$CLAUDE_USER_HOME/.local/bin/claude" --version)" = "2.1.300 (Claude Code)" ]
}

@test "--execute links every extension's bundled binary to the system bin" {
    legacy_layout
    old="$(make_extension 2.1.270)"
    new="$(make_extension 2.1.280)"
    converge
    [ "$(readlink "$old")" = "$CLAUDE_SYSTEM_BIN" ]
    [ "$(readlink "$new")" = "$CLAUDE_SYSTEM_BIN" ]
}

@test "--execute leaves the installer's scratch HOME out of the user's home" {
    legacy_layout
    converge
    [ ! -e "$CLAUDE_USER_HOME/.claude.json" ]
}

@test "--execute disables the user-level auto-updater, keeping other settings" {
    legacy_layout
    mkdir -p "$CLAUDE_USER_HOME/.claude"
    echo '{"theme": "dark", "env": {"FOO": "1"}}' >"$CLAUDE_USER_HOME/.claude/settings.json"
    converge
    run python3 -c 'import json,sys; d=json.load(open(sys.argv[1])); print(d["theme"], d["env"]["FOO"], d["env"]["DISABLE_AUTOUPDATER"])' \
        "$CLAUDE_USER_HOME/.claude/settings.json"
    [ "$output" = "dark 1 1" ]
}

@test "--execute updates: a newer version repoints the system bin, users follow" {
    legacy_layout
    ext="$(make_extension 2.1.280)"
    converge
    CLAUDE_STUB_VERSION=2.1.310 converge
    [ "$(readlink "$CLAUDE_SYSTEM_BIN")" = "$VERSIONS/2.1.310" ]
    [ "$("$ext" --version)" = "2.1.310 (Claude Code)" ]
}

@test "--execute keeps the current and previous version, prunes older" {
    legacy_layout
    for v in 2.1.300 2.1.310 2.1.320; do CLAUDE_STUB_VERSION=$v converge; done
    [ ! -e "$VERSIONS/2.1.300" ]
    [ -x "$VERSIONS/2.1.310" ]
    [ -x "$VERSIONS/2.1.320" ]
}

@test "--execute is idempotent" {
    legacy_layout
    make_extension 2.1.280 >/dev/null
    converge
    converge
    [[ "$output" == *"already"* ]]
    [ "$(readlink "$CLAUDE_SYSTEM_BIN")" = "$VERSIONS/2.1.300" ]
}

@test "--execute refuses to move the system bin to an older version" {
    legacy_layout
    CLAUDE_STUB_VERSION=2.1.310 converge
    CLAUDE_STUB_VERSION=2.1.300 run bash "$SCRIPT" --execute --version 2.1.300
    [ "$status" -eq 1 ]
    [[ "$output" == *"2.1.300"*"older"*"2.1.310"* ]]
    [ "$(readlink "$CLAUDE_SYSTEM_BIN")" = "$VERSIONS/2.1.310" ]
}

@test "--execute passes --version through to the installer" {
    legacy_layout
    run bash "$SCRIPT" --execute --version stable
    [ "$status" -eq 0 ]
    [[ "$output" == *"install stable"* ]]
}

@test "--execute skips an extension newer than the system version" {
    legacy_layout
    ext="$(make_extension 2.1.400)"
    run bash "$SCRIPT" --execute
    [ "$status" -eq 1 ]
    [[ "$output" == *"2.1.400"*"newer"* ]]
    [ ! -L "$ext" ]
    [ "$(readlink "$CLAUDE_SYSTEM_BIN")" = "$VERSIONS/2.1.300" ]
}

@test "--execute with no Claude Code anywhere to install from is an error" {
    run bash "$SCRIPT" --execute
    [ "$status" -eq 2 ]
    [[ "$output" == *"no Claude Code binary"* ]]
}

# --- --check (the SessionStart hook) -------------------------------------------

@test "--check is silent on the converged layout" {
    legacy_layout
    make_extension 2.1.280 >/dev/null
    converge
    run bash "$SCRIPT" --check
    [ "$status" -eq 0 ]
    [ -z "$output" ]
}

@test "--check flags a system bin that is not the system runtime" {
    legacy_layout
    mkdir -p "$VERSIONS" # the host adopted the layout; the system bin drifted
    run bash "$SCRIPT" --check
    [ "$status" -eq 0 ]
    [[ "$output" == *"not the system runtime"* ]]
    [[ "$output" == *"sudo bash scripts/claude-system.sh --execute"* ]]
}

@test "--check flags an extension update's fresh bundled binary" {
    legacy_layout
    converge
    bin="$(make_extension 2.1.290)"
    run bash "$SCRIPT" --check
    [ "$status" -eq 0 ]
    [[ "$output" == *"2.1.290"* ]]
    [ ! -L "$bin" ]
}

@test "--check flags a user launcher repointed away from the system bin" {
    legacy_layout
    converge
    ln -sfn "$VERSIONS/2.1.300" "$CLAUDE_USER_HOME/.local/bin/claude"
    run bash "$SCRIPT" --check
    [[ "$output" == *".local/bin/claude"* ]]
}

@test "--check falls back to the version file's age when no run was stamped" {
    legacy_layout
    converge
    rm "$CLAUDE_SYSTEM_LIB/claude/last-update"
    touch -d '20 days ago' "$VERSIONS/2.1.300"
    run bash "$SCRIPT" --check
    [ "$status" -eq 0 ]
    [[ "$output" == *"2.1.300"*"20 days"* ]]
}

@test "--check is silent where there is no system bin (not this layout)" {
    make_extension 2.1.280 >/dev/null
    run bash "$SCRIPT" --check
    [ "$status" -eq 0 ]
    [ -z "$output" ]
}

@test "CR 1: --check ages the last successful run, not the version file" {
    legacy_layout
    converge
    touch -d '20 days ago' "$VERSIONS/2.1.300"
    converge
    run bash "$SCRIPT" --check
    [ "$status" -eq 0 ]
    [ -z "$output" ]
}

@test "CR 1: --check flags a stale last run, and names its age" {
    legacy_layout
    converge
    touch -d '20 days ago' "$CLAUDE_SYSTEM_LIB/claude/last-update"
    run bash "$SCRIPT" --check
    [[ "$output" == *"2.1.300"*"20 days"* ]]
}

@test "CR 2: --check names a system bin that links to a missing version" {
    legacy_layout
    converge
    ln -sfn "$VERSIONS/9.9.9" "$CLAUDE_SYSTEM_BIN"
    run bash "$SCRIPT" --check
    [ "$status" -eq 0 ]
    [[ "$output" == *"9.9.9"*"missing"* ]]
    [[ "$output" != *"syntax error"* ]]
}

@test "CR 2: --execute repairs a dangling system bin instead of refusing" {
    legacy_layout
    converge
    ln -sfn "$VERSIONS/9.9.9" "$CLAUDE_SYSTEM_BIN"
    ln -sfn "$CLAUDE_USER_HOME/.local/share/claude/versions/2.1.280" "$CLAUDE_USER_HOME/.local/bin/claude"
    run bash "$SCRIPT" --execute
    [ "$status" -eq 0 ]
    [ "$(readlink "$CLAUDE_SYSTEM_BIN")" = "$VERSIONS/2.1.300" ]
}

@test "CR 3: --check is silent where the host never adopted this layout" {
    # e.g. Homebrew's or a global npm install's /usr/local/bin/claude.
    write_stub "$CLAUDE_SYSTEM_BIN" 2.1.250
    make_extension 2.1.280 >/dev/null
    run bash "$SCRIPT" --check
    [ "$status" -eq 0 ]
    [ -z "$output" ]
}

# Pretend to be root: `id -u` says 0 and `chown` records instead of acting.
as_fake_root() {
    local shims="$BATS_TEST_TMPDIR/shims"
    mkdir -p "$shims"
    printf '#!/usr/bin/env bash\n[ "$1" = -u ] && { echo 0; exit 0; }\nexec /usr/bin/id "$@"\n' >"$shims/id"
    printf '#!/usr/bin/env bash\necho "$@" >>"%s"\n' "$BATS_TEST_TMPDIR/chown.log" >"$shims/chown"
    chmod +x "$shims/id" "$shims/chown"
    PATH="$shims:$PATH"
}

@test "CR 4: directories --execute creates in the user's home are handed to the user" {
    write_stub "$CLAUDE_USER_HOME/.local/share/claude/versions/2.1.280" 2.1.280
    ln -s "$CLAUDE_USER_HOME/.local/share/claude/versions/2.1.280" "$CLAUDE_SYSTEM_BIN"
    rmdir "$CLAUDE_USER_HOME/.local/bin"
    as_fake_root
    run bash "$SCRIPT" --execute
    [ "$status" -eq 0 ]
    grep -q -- " $CLAUDE_USER_HOME/.local/bin\$" "$BATS_TEST_TMPDIR/chown.log"
    grep -q -- " $CLAUDE_USER_HOME/.claude\$" "$BATS_TEST_TMPDIR/chown.log"
}

@test "CR 5: --execute names an installer that root does not own" {
    legacy_layout # every stub here belongs to the test user, not root
    converge
    [[ "$output" == *"note: running the installer from"*"not root"* ]]
}

@test "CR 8: a refused downgrade removes the version this run installed" {
    legacy_layout
    CLAUDE_STUB_VERSION=2.1.310 converge
    CLAUDE_STUB_VERSION=2.1.290 run bash "$SCRIPT" --execute --version 2.1.290
    [ "$status" -eq 1 ]
    [ ! -e "$VERSIONS/2.1.290" ]
}

@test "CR 8: a refused downgrade keeps a version that was already there" {
    legacy_layout
    CLAUDE_STUB_VERSION=2.1.300 converge
    CLAUDE_STUB_VERSION=2.1.310 converge
    CLAUDE_STUB_VERSION=2.1.300 run bash "$SCRIPT" --execute --version 2.1.300
    [ "$status" -eq 1 ]
    [ -x "$VERSIONS/2.1.300" ]
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

@test "the hook runs --check against the invoking user's home" {
    legacy_layout
    mkdir -p "$VERSIONS"
    run env HOME="$CLAUDE_USER_HOME" CLAUDE_USER_HOME= bash "$HOOK"
    [ "$status" -eq 0 ]
    [[ "$output" == *"not the system runtime"* ]]
}

@test "the hook is registered in .claude/settings.json" {
    grep -q '"bash \\"${CLAUDE_PROJECT_DIR:-.}/.claude/hooks/claude-canonical-check.sh\\""' \
        "$(repo_root)/.claude/settings.json"
}
