#!/usr/bin/env bats
# Tests for scripts/worktree-setup.sh (#450).
#
# `uv` is a PATH shim (tests/sh/stubs/uv) — no real resolve, no network. Git is
# real but confined to $BATS_TEST_TMPDIR: each test builds a throwaway repo plus
# a linked worktree, so the guard that refuses the main checkout is exercised
# against a genuine `git worktree`, not a simulation.

load helpers

setup() {
    setup_worktree_fixture
}

# --- guard ------------------------------------------------------------------

@test "refuses to run against the main checkout" {
    run bash "$(repo_root)/scripts/worktree-setup.sh" "$FAKE_MAIN"
    [ "$status" -eq 2 ]
    [[ "$output" == *"main checkout"* ]]
}

@test "refuses a path that is not a git worktree" {
    mkdir -p "$BATS_TEST_TMPDIR/loose"
    run bash "$(repo_root)/scripts/worktree-setup.sh" "$BATS_TEST_TMPDIR/loose"
    [ "$status" -eq 2 ]
}

@test "refuses when git reports an empty layout (never guesses linked)" {
    # `cd ""` succeeds in bash, so an empty --git-dir would resolve to $TARGET,
    # mismatch the common dir and read as a linked worktree — in the main
    # checkout that would sync production's venv. Same shape apply-schema.sh
    # guards (#398).
    local shim="$BATS_TEST_TMPDIR/shim"
    mkdir -p "$shim"
    cat > "$shim/git" <<EOF
#!/usr/bin/env bash
for a in "\$@"; do
    [ "\$a" = "--git-dir" ] && exit 0     # exit 0, print nothing
done
exec $(command -v git) "\$@"
EOF
    chmod +x "$shim/git"
    PATH="$shim:$PATH" run bash "$(repo_root)/scripts/worktree-setup.sh" "$FAKE_WORKTREE"
    [ "$status" -eq 2 ]
    [[ "$output" == *"layout"* ]]
}

# --- the shared-venv fix ----------------------------------------------------

@test "replaces the shared .venv symlink with a real per-worktree venv" {
    ln -s "$FAKE_MAIN/.venv" "$FAKE_WORKTREE/.venv"
    run bash "$(repo_root)/scripts/worktree-setup.sh" "$FAKE_WORKTREE"
    [ "$status" -eq 0 ]
    [ ! -L "$FAKE_WORKTREE/.venv" ]
    [ -f "$FAKE_WORKTREE/.venv/bin/activate" ]
    [ "$(call_count "$STUB_UV_CALL_LOG" 'sync')" -ge 1 ]
    [[ "$output" == *"shared .venv"* ]]
}

@test "run from a subdirectory targets the worktree root, not the subdirectory" {
    # The guard passes from any subdir, so without canonicalising, a shared
    # .venv symlink at the root goes unseen and `uv sync` installs straight
    # through it into the main checkout's (production's) venv.
    ln -s "$FAKE_MAIN/.venv" "$FAKE_WORKTREE/.venv"
    echo "GH_TOKEN=x" > "$FAKE_MAIN/.env"
    mkdir -p "$FAKE_WORKTREE/scripts"
    cd "$FAKE_WORKTREE/scripts"
    run bash "$(repo_root)/scripts/worktree-setup.sh"
    [ "$status" -eq 0 ]
    [ ! -L "$FAKE_WORKTREE/.venv" ]
    [ -L "$FAKE_WORKTREE/.env" ]
    [ ! -e "$FAKE_WORKTREE/scripts/.env" ]
}

@test "creates a venv when the worktree has none" {
    run bash "$(repo_root)/scripts/worktree-setup.sh" "$FAKE_WORKTREE"
    [ "$status" -eq 0 ]
    [ -f "$FAKE_WORKTREE/.venv/bin/activate" ]
}

@test "syncs the opt-in groups, so no tier silently vanishes" {
    run bash "$(repo_root)/scripts/worktree-setup.sh" "$FAKE_WORKTREE"
    grep -q -- "--group browser" "$STUB_UV_CALL_LOG"
    grep -q -- "--group seed" "$STUB_UV_CALL_LOG"
}

@test "a failed sync is fatal and names the worktree" {
    export STUB_UV_SYNC_RC=1
    run bash "$(repo_root)/scripts/worktree-setup.sh" "$FAKE_WORKTREE"
    [ "$status" -eq 1 ]
    [[ "$output" == *"uv sync"* ]]
    # The shared symlink is already gone by then — say so, or the operator
    # cannot tell whether the old environment survived.
    [[ "$output" == *"re-run"* ]]
}

# --- the .env symlink -------------------------------------------------------

@test "symlinks .env from the main checkout" {
    echo "GH_TOKEN=x" > "$FAKE_MAIN/.env"
    run bash "$(repo_root)/scripts/worktree-setup.sh" "$FAKE_WORKTREE"
    [ "$status" -eq 0 ]
    [ -L "$FAKE_WORKTREE/.env" ]
    [ "$(cat "$FAKE_WORKTREE/.env")" = "GH_TOKEN=x" ]
}

@test "replaces a dangling .env symlink" {
    ln -s "$FAKE_MAIN/.env.moved-away" "$FAKE_WORKTREE/.env"   # target absent
    echo "GH_TOKEN=x" > "$FAKE_MAIN/.env"
    run bash "$(repo_root)/scripts/worktree-setup.sh" "$FAKE_WORKTREE"
    [ "$status" -eq 0 ]
    [ -L "$FAKE_WORKTREE/.env" ]
    [ "$(cat "$FAKE_WORKTREE/.env")" = "GH_TOKEN=x" ]
}

@test "a dangling .env symlink with nothing to link to is removed, not left broken" {
    ln -s "$FAKE_MAIN/.env" "$FAKE_WORKTREE/.env"   # main has no .env at all
    run bash "$(repo_root)/scripts/worktree-setup.sh" "$FAKE_WORKTREE"
    [ "$status" -eq 0 ]
    [ ! -L "$FAKE_WORKTREE/.env" ]
    [[ "$output" == *"WARN"* ]]
}

@test "leaves an existing .env alone" {
    echo "GH_TOKEN=main" > "$FAKE_MAIN/.env"
    echo "GH_TOKEN=local" > "$FAKE_WORKTREE/.env"
    run bash "$(repo_root)/scripts/worktree-setup.sh" "$FAKE_WORKTREE"
    [ "$status" -eq 0 ]
    [ ! -L "$FAKE_WORKTREE/.env" ]
    [ "$(cat "$FAKE_WORKTREE/.env")" = "GH_TOKEN=local" ]
}

@test "no .env in the main checkout is a warning, not a failure" {
    run bash "$(repo_root)/scripts/worktree-setup.sh" "$FAKE_WORKTREE"
    [ "$status" -eq 0 ]
    [[ "$output" == *"WARN"* ]]
}

# --- idempotence ------------------------------------------------------------

@test "a second run is a no-op beyond re-syncing" {
    echo "GH_TOKEN=x" > "$FAKE_MAIN/.env"
    bash "$(repo_root)/scripts/worktree-setup.sh" "$FAKE_WORKTREE"
    run bash "$(repo_root)/scripts/worktree-setup.sh" "$FAKE_WORKTREE"
    [ "$status" -eq 0 ]
    [ ! -L "$FAKE_WORKTREE/.venv" ]
    [ -L "$FAKE_WORKTREE/.env" ]
}

@test "defaults to the current directory" {
    cd "$FAKE_WORKTREE"
    run bash "$(repo_root)/scripts/worktree-setup.sh"
    [ "$status" -eq 0 ]
    [ -f "$FAKE_WORKTREE/.venv/bin/activate" ]
}
