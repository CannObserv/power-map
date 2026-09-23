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
    # #497: the dbt wrapper tests importorskip at module scope, so a worktree
    # without this group runs a narrower suite that still reports green.
    grep -q -- "--group mapping" "$STUB_UV_CALL_LOG"
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

# --- the JS environment (#554) ----------------------------------------------

@test "the install leaves stdout empty" {
    # Every message this script emits goes to stderr, and `uv sync` writes to
    # stderr natively — so stdout is empty by construction and a wrapper may
    # capture it (`worktree-create.sh`, next to it, prints the worktree path
    # there). `npm ci` reports on stdout, so it has to be redirected.
    run --separate-stderr bash "$(repo_root)/scripts/worktree-setup.sh" "$FAKE_WORKTREE"
    [ "$status" -eq 0 ]
    [ -z "$output" ]
    [[ "$stderr" == *"npm ci"* ]]
}

@test "installs node_modules from the lockfile" {
    # `bats` and `vitest` are devDependencies resolved through
    # node_modules/.bin, and node_modules/ is gitignored — so without this the
    # first `git commit` in a worktree aborts with `vitest: not found`,
    # exit 127, after the work is done and the suite is green.
    run bash "$(repo_root)/scripts/worktree-setup.sh" "$FAKE_WORKTREE"
    [ "$status" -eq 0 ]
    [ "$(call_count "$STUB_NPM_CALL_LOG" '^npm ci')" -ge 1 ]
    [ -d "$FAKE_WORKTREE/node_modules" ]
}

@test "installs from the lockfile, never a resolving install" {
    # `npm install` is npm's `uv run` (#450): it may resolve something other
    # than what the lockfile pins, so the worktree's hooks would run versions
    # the main checkout and CI do not. `npm ci` is the exact one.
    run bash "$(repo_root)/scripts/worktree-setup.sh" "$FAKE_WORKTREE"
    [ "$status" -eq 0 ]
    [ "$(call_count "$STUB_NPM_CALL_LOG" '^npm install')" -eq 0 ]
}

@test "installs into the worktree root when run from a subdirectory" {
    # npm resolves its install root from the cwd, so an uncanonicalised run
    # would leave a node_modules/ in the subdirectory and the worktree root
    # still bare — the hooks would stay at exit 127 with provisioning "done".
    mkdir -p "$FAKE_WORKTREE/scripts"
    cd "$FAKE_WORKTREE/scripts"
    run bash "$(repo_root)/scripts/worktree-setup.sh"
    [ "$status" -eq 0 ]
    [ -d "$FAKE_WORKTREE/node_modules" ]
    [ ! -e "$FAKE_WORKTREE/scripts/node_modules" ]
}

@test "an existing node_modules survives a re-run" {
    # `npm ci` deletes node_modules/ before installing, so an unconditional one
    # turns a re-run of a script documented idempotent into a full reinstall.
    # Provision only what is unprovisioned, as the submodule step does.
    mkdir -p "$FAKE_WORKTREE/node_modules/.bin"
    : > "$FAKE_WORKTREE/node_modules/.bin/mine"

    run bash "$(repo_root)/scripts/worktree-setup.sh" "$FAKE_WORKTREE"
    [ "$status" -eq 0 ]
    [ -f "$FAKE_WORKTREE/node_modules/.bin/mine" ]
    [ "$(call_count "$STUB_NPM_CALL_LOG" '^npm ci')" -eq 0 ]
    [[ "$output" == *"node_modules already present"* ]]
}

@test "replaces a node_modules symlinked into the main checkout" {
    # `-d` dereferences, so a link at the main checkout's node_modules looks
    # provisioned — and it is the same shared-mutable-environment trap as the
    # .venv symlink this script exists to undo (#450), in its most direct form:
    # the worktree runs production's installed versions, and its own npm writes
    # back into production's working directory. It is also the obvious
    # pre-#554 workaround, so the state is one an operator arrives with.
    mkdir -p "$FAKE_MAIN/node_modules/.bin"
    : > "$FAKE_MAIN/node_modules/.bin/vitest"
    ln -s "$FAKE_MAIN/node_modules" "$FAKE_WORKTREE/node_modules"

    run bash "$(repo_root)/scripts/worktree-setup.sh" "$FAKE_WORKTREE"
    [ "$status" -eq 0 ]
    [ ! -L "$FAKE_WORKTREE/node_modules" ]
    [ -d "$FAKE_WORKTREE/node_modules/.bin" ]
    [ "$(call_count "$STUB_NPM_CALL_LOG" '^npm ci')" -ge 1 ]
    [[ "$output" == *"shared node_modules symlink"* ]]
    # The main checkout's own install is not what gets replaced.
    [ -f "$FAKE_MAIN/node_modules/.bin/vitest" ]
}

@test "a half-installed node_modules is finished, not declared present" {
    # An interrupted `npm ci` leaves the directory behind without the .bin/ the
    # hooks resolve through. Gating on the directory alone would report that
    # worktree provisioned and leave its first commit refused; gating on .bin/
    # makes a re-run the recovery it reads as.
    mkdir -p "$FAKE_WORKTREE/node_modules/some-package"

    run bash "$(repo_root)/scripts/worktree-setup.sh" "$FAKE_WORKTREE"
    [ "$status" -eq 0 ]
    [ "$(call_count "$STUB_NPM_CALL_LOG" '^npm ci')" -ge 1 ]
    [ -d "$FAKE_WORKTREE/node_modules/.bin" ]
}

@test "no lockfile means nothing to install, not a failure" {
    rm "$FAKE_WORKTREE/package-lock.json"
    run bash "$(repo_root)/scripts/worktree-setup.sh" "$FAKE_WORKTREE"
    [ "$status" -eq 0 ]
    [ "$(call_count "$STUB_NPM_CALL_LOG" '^npm ci')" -eq 0 ]
    [[ "$output" != *"npm"* ]]
}

@test "a host without npm warns and finishes the rest of the setup" {
    # The venv, the submodules and the links are the parts that do not need
    # npm; a missing interpreter is not a reason to withhold them.
    PATH="$(path_without_npm)" run bash "$(repo_root)/scripts/worktree-setup.sh" "$FAKE_WORKTREE"
    [ "$status" -eq 0 ]
    [[ "$output" == *"WARN: npm"* ]]
    # All four JS hooks resolve through node_modules/.bin, not just the two that
    # happened to fire first in #554 — eslint and prettier are gated on `\.js$`,
    # so they refuse a JS-touching commit before vitest is ever reached.
    [[ "$output" == *"eslint"* ]]
    [[ "$output" == *"worktree ready"* ]]
}

@test "a failed npm ci warns and names the command rather than failing the setup" {
    # Offline, or a registry that refuses: same call as the submodule step, and
    # the warning has to name both the symptom (exit 127) and the remedy, or
    # the next stop is .pre-commit-config.yaml.
    export STUB_NPM_CI_RC=1
    run bash "$(repo_root)/scripts/worktree-setup.sh" "$FAKE_WORKTREE"
    [ "$status" -eq 0 ]
    [[ "$output" == *"WARN"* ]]
    [[ "$output" == *"npm ci"* ]]
    [[ "$output" == *"127"* ]]
    [[ "$output" == *"eslint"* ]]
    [[ "$output" == *"worktree ready"* ]]
}

@test "the installed node_modules is gitignored, so the worktree stays clean" {
    # Asserted against the real repo's rules: an unignored install would leave
    # every provisioned worktree permanently dirty.
    run git -C "$(repo_root)" check-ignore -q node_modules
    [ "$status" -eq 0 ]
}

@test "usage names the JS half of what it provisions" {
    # The acceptance the operator actually reads: --help and the header
    # enumerate what a worktree gets, and node_modules was missing from both.
    run bash "$(repo_root)/scripts/worktree-setup.sh" --help
    [ "$status" -eq 0 ]
    [[ "$output" == *"npm ci"* ]]
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
    [ -d "$FAKE_WORKTREE/node_modules" ]
    [ -L "$FAKE_WORKTREE/.env" ]
    [ -L "$FAKE_WORKTREE/data/cannabis_observer" ]
}

@test "defaults to the current directory" {
    cd "$FAKE_WORKTREE"
    run bash "$(repo_root)/scripts/worktree-setup.sh"
    [ "$status" -eq 0 ]
    [ -f "$FAKE_WORKTREE/.venv/bin/activate" ]
}
