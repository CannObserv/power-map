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

@test "a bare-flagged main checkout still gets the refusal, not git's fatal" {
    # `core.bare = true` on a repo that does have a work tree makes
    # `rev-parse --show-toplevel` fail (exit 128) — so canonicalising before
    # the guard must degrade, or the most common misuse loses its message.
    # Not hypothetical: this repo's main checkout was left in that state.
    git -C "$FAKE_MAIN" config core.bare true
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

# --- the vendored submodules (#482) -----------------------------------------

@test "initialises the skills-vendor submodules" {
    # A fresh worktree gets .gitmodules and an empty directory, so the vendored
    # -driver guards (tests/test_vendor_skills.py) fail on an agent's very first
    # baseline run for a reason that has nothing to do with its work.
    add_fixture_submodule
    [ -z "$(ls -A "$FAKE_WORKTREE/skills-vendor/thing")" ]

    run bash "$(repo_root)/scripts/worktree-setup.sh" "$FAKE_WORKTREE"
    [ "$status" -eq 0 ]
    [ -f "$FAKE_WORKTREE/skills-vendor/thing/SKILL.md" ]
}

@test "a submodule that cannot be initialised warns and names the command" {
    # Offline, or a source that has moved: the venv and the links are already
    # done, so this is not worth failing provisioning over — but silence would
    # hand back the same red baseline the step exists to prevent.
    add_fixture_submodule
    break_fixture_submodule

    run bash "$(repo_root)/scripts/worktree-setup.sh" "$FAKE_WORKTREE"
    [ "$status" -eq 0 ]
    [[ "$output" == *"WARN"* ]]
    [[ "$output" == *"git submodule update --init skills-vendor/"* ]]
}

@test "a repo with no submodules is not an error" {
    run bash "$(repo_root)/scripts/worktree-setup.sh" "$FAKE_WORKTREE"
    [ "$status" -eq 0 ]
    [[ "$output" != *"WARN: could not initialise"* ]]
}

@test "an already-initialised submodule survives a re-run" {
    add_fixture_submodule
    bash "$(repo_root)/scripts/worktree-setup.sh" "$FAKE_WORKTREE"
    run bash "$(repo_root)/scripts/worktree-setup.sh" "$FAKE_WORKTREE"
    [ "$status" -eq 0 ]
    [ -f "$FAKE_WORKTREE/skills-vendor/thing/SKILL.md" ]
}

@test "a submodule moved off its gitlink is left where it is" {
    # The script is documented idempotent and gets re-run; `submodule update`
    # would check the recorded gitlink back out, silently undoing the one
    # reason to be at another commit — testing a pointer bump. Provision only
    # what is unprovisioned.
    add_fixture_submodule
    bash "$(repo_root)/scripts/worktree-setup.sh" "$FAKE_WORKTREE"

    local sub="$FAKE_WORKTREE/skills-vendor/thing" moved
    git -C "$sub" -c user.email=bats@example.invalid -c user.name=bats \
        commit --quiet --no-verify --allow-empty -m "local pointer bump"
    moved="$(git -C "$sub" rev-parse HEAD)"

    run bash "$(repo_root)/scripts/worktree-setup.sh" "$FAKE_WORKTREE"
    [ "$status" -eq 0 ]
    [ "$(git -C "$sub" rev-parse HEAD)" = "$moved" ]
    [[ "$output" == *"already initialised"* ]]
}

# --- the shared gitignored data (#482) --------------------------------------

@test "symlinks data/cannabis_observer from the main checkout" {
    # Gitignored, so a worktree never gets it, so
    # test_seed_jurisdictions.py::test_load_seed_file_actual_wa_file skips and
    # the worktree's baseline is one test short of the main checkout's on an
    # identical tree — which is what makes a briefed count useless.
    mkdir -p "$FAKE_MAIN/data/cannabis_observer"
    echo '{}' > "$FAKE_MAIN/data/cannabis_observer/seed.json"

    run bash "$(repo_root)/scripts/worktree-setup.sh" "$FAKE_WORKTREE"
    [ "$status" -eq 0 ]
    [ -L "$FAKE_WORKTREE/data/cannabis_observer" ]
    [ "$(cat "$FAKE_WORKTREE/data/cannabis_observer/seed.json")" = "{}" ]
}

@test "the linked data path is gitignored, so the worktree stays clean" {
    # The .gitignore entry has to match a SYMLINK, not just a directory: with a
    # trailing slash it matches neither, and every provisioned worktree reports
    # `?? data/` forever. Asserted against the real repo's rules.
    run git -C "$(repo_root)" check-ignore -q data/cannabis_observer
    [ "$status" -eq 0 ]
}

@test "leaves an existing data directory alone" {
    mkdir -p "$FAKE_MAIN/data/cannabis_observer"
    mkdir -p "$FAKE_WORKTREE/data/cannabis_observer"
    echo "local" > "$FAKE_WORKTREE/data/cannabis_observer/mine.json"

    run bash "$(repo_root)/scripts/worktree-setup.sh" "$FAKE_WORKTREE"
    [ "$status" -eq 0 ]
    [ ! -L "$FAKE_WORKTREE/data/cannabis_observer" ]
    [ -f "$FAKE_WORKTREE/data/cannabis_observer/mine.json" ]
}

@test "replaces a dangling data symlink" {
    mkdir -p "$FAKE_WORKTREE/data"
    # Points somewhere that never exists, so the link stays dangling unless the
    # script replaces it — a link that merely predates its target would pass
    # without the script doing anything at all.
    ln -s "$FAKE_MAIN/data/moved-away" "$FAKE_WORKTREE/data/cannabis_observer"
    mkdir -p "$FAKE_MAIN/data/cannabis_observer"
    echo '{}' > "$FAKE_MAIN/data/cannabis_observer/seed.json"

    run bash "$(repo_root)/scripts/worktree-setup.sh" "$FAKE_WORKTREE"
    [ "$status" -eq 0 ]
    [ -L "$FAKE_WORKTREE/data/cannabis_observer" ]
    [ "$(cat "$FAKE_WORKTREE/data/cannabis_observer/seed.json")" = "{}" ]
}

@test "a parent that cannot hold the link warns instead of aborting the run" {
    # `set -e` on a bare mkdir would kill the script with exit 1 here — the code
    # the header documents as "uv sync failed", after the sync already succeeded.
    mkdir -p "$FAKE_MAIN/data/cannabis_observer"
    : > "$FAKE_WORKTREE/data"          # a FILE where the directory must go

    run bash "$(repo_root)/scripts/worktree-setup.sh" "$FAKE_WORKTREE"
    [ "$status" -eq 0 ]
    [[ "$output" == *"WARN: could not link data/cannabis_observer"* ]]
    [[ "$output" == *"worktree ready"* ]]
}

@test "no data in the main checkout is a warning, not a failure" {
    run bash "$(repo_root)/scripts/worktree-setup.sh" "$FAKE_WORKTREE"
    [ "$status" -eq 0 ]
    [[ "$output" == *"WARN: no data/cannabis_observer"* ]]
    [ ! -e "$FAKE_WORKTREE/data/cannabis_observer" ]
}

# --- idempotence ------------------------------------------------------------

@test "a second run is a no-op beyond re-syncing" {
    echo "GH_TOKEN=x" > "$FAKE_MAIN/.env"
    mkdir -p "$FAKE_MAIN/data/cannabis_observer"
    bash "$(repo_root)/scripts/worktree-setup.sh" "$FAKE_WORKTREE"
    run bash "$(repo_root)/scripts/worktree-setup.sh" "$FAKE_WORKTREE"
    [ "$status" -eq 0 ]
    [ ! -L "$FAKE_WORKTREE/.venv" ]
    [ -L "$FAKE_WORKTREE/.env" ]
    [ -L "$FAKE_WORKTREE/data/cannabis_observer" ]
}

@test "defaults to the current directory" {
    cd "$FAKE_WORKTREE"
    run bash "$(repo_root)/scripts/worktree-setup.sh"
    [ "$status" -eq 0 ]
    [ -f "$FAKE_WORKTREE/.venv/bin/activate" ]
}
