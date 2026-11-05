#!/usr/bin/env bats
# scripts/pre-ship.sh, the repo-root wrapper SKILL.md Step 1 resolves ahead of the
# vendored gate (#740): both a11y tiers on an admin-scoped diff, then the
# vendored gate.
#
# What it must do, one test or more each:
#   - run the tiers when a path in scripts/pre-ship-a11y-paths changed, and not
#     otherwise; run them when it cannot tell (no base ref, no list, empty list);
#   - fail on a missing browser, an unset TEST_DATABASE_URL or an unreachable DB
#     (exit 2: infrastructure, never an a11y finding), and on a tier that passes
#     nothing (a skip-only run is the vacuous pass #369 guarded against);
#   - never run two at once on the shared test DB (a flock under the git
#     common dir), and hold the lock only for the tiers;
#   - hand off to the vendored gate, whose exit code is the wrapper's on a
#     green a11y step; still run it after an a11y failure, and exit 1;
#   - honour PRE_SHIP_A11Y=off under CI only (#727), saying it verified nothing.
#
# Hermetic: `uv` is the tests/sh/stubs/uv shim, the vendored gate is a stub
# written into a throwaway repo under $BATS_TEST_TMPDIR, and git is real but
# confined there. No DB, no browser, no network.

load helpers

setup() {
    REAL_ROOT="$(repo_root)"

    PATH="$BATS_TEST_DIRNAME/stubs:$PATH"
    export PATH
    export STUB_UV_CALL_LOG="$BATS_TEST_TMPDIR/uv-calls.log"
    export STUB_UV_GUARD_RC=0
    export STUB_UV_LXML_RC=0
    export STUB_UV_BROWSER_RC=0
    export STUB_UV_LXML_OUTPUT="198 passed in 14.02s"
    export STUB_UV_BROWSER_OUTPUT="51 passed, 164 skipped in 59.31s"
    export DELEGATE_LOG="$BATS_TEST_TMPDIR/delegate.log"
    export STUB_DELEGATE_RC=0
    unset PRE_SHIP_A11Y PRE_SHIP_A11Y_BASE PRE_SHIP_A11Y_LOCK_WAIT CI TEST_DATABASE_URL

    # The same git hygiene as setup_worktree_fixture (helpers.bash): under a
    # pre-commit hook GIT_DIR points at the real repo.
    unset GIT_DIR GIT_WORK_TREE GIT_INDEX_FILE GIT_PREFIX GIT_COMMON_DIR
    unset GIT_OBJECT_DIRECTORY GIT_ALTERNATE_OBJECT_DIRECTORIES
    export GIT_CONFIG_GLOBAL=/dev/null GIT_CONFIG_SYSTEM=/dev/null

    FIX="$BATS_TEST_TMPDIR/repo"
    mkdir -p "$FIX/scripts" "$FIX/skills/shipping-work/scripts" "$FIX/src/templates/admin" "$FIX/src/core"
    git -C "$FIX" init --quiet --initial-branch=main --template=
    git -C "$FIX" config user.email bats@example.invalid
    git -C "$FIX" config user.name bats
    git -C "$FIX" config core.hooksPath /dev/null
    case "$(cd "$FIX" && git rev-parse --absolute-git-dir)" in
        "$BATS_TEST_TMPDIR"/*) : ;;
        *) echo "fixture escaped its tmpdir" >&2; return 1 ;;
    esac

    cp "$REAL_ROOT/scripts/pre-ship.sh" "$REAL_ROOT/scripts/pre-ship-a11y-paths" "$FIX/scripts/"
    # The vendored gate's stand-in: logs its argv and whether the a11y lock is
    # free while it runs, then exits as told.
    cat > "$FIX/skills/shipping-work/scripts/pre-ship.sh" <<'SH'
#!/usr/bin/env bash
lock="$(git rev-parse --path-format=absolute --git-common-dir)/pre-ship-a11y.lock"
state=held
flock -n "$lock" true 2>/dev/null && state=free
echo "DELEGATE args=[$*] lock=$state tdu=${TEST_DATABASE_URL:+set}" >> "$DELEGATE_LOG"
exit "${STUB_DELEGATE_RC:-0}"
SH
    echo '<p>admin</p>' > "$FIX/src/templates/admin/page.html"
    echo 'x = 1' > "$FIX/src/core/thing.py"
    git -C "$FIX" add -A
    git -C "$FIX" commit --quiet --no-verify -m init
    git -C "$FIX" update-ref refs/remotes/origin/main HEAD
    git -C "$FIX" checkout --quiet -b feature
    LOCK="$FIX/.git/pre-ship-a11y.lock"
}

commit_change() {
    echo "$2" >> "$FIX/$1"
    git -C "$FIX" add -A
    git -C "$FIX" commit --quiet --no-verify -m "change $1"
}

run_wrapper() {
    run bash -c 'cd "$1" && shift && bash scripts/pre-ship.sh "$@"' _ "$FIX" "$@"
}

tiers_ran() {
    [ "$(call_count "$STUB_UV_CALL_LOG" '-m integration')" -eq 1 ] &&
        [ "$(call_count "$STUB_UV_CALL_LOG" '-m browser')" -eq 1 ]
}

no_tiers_ran() {
    [ "$(call_count "$STUB_UV_CALL_LOG" '^uv ')" -eq 0 ]
}

delegate_ran() {
    [ "$(call_count "$DELEGATE_LOG" '^DELEGATE')" -eq 1 ]
}

# --- scope --------------------------------------------------------------------

@test "a non-admin diff runs no tier and hands off to the vendored gate" {
    commit_change src/core/thing.py 'y = 2'
    run_wrapper
    [ "$status" -eq 0 ]
    no_tiers_ran
    delegate_ran
    [[ "$output" == *"a11y: not run"* ]]
}

@test "an admin template change runs the preflight, then both tiers, then the gate" {
    commit_change src/templates/admin/page.html '<p>more</p>'
    run_wrapper
    [ "$status" -eq 0 ]
    tiers_ran
    delegate_ran
    # Order: preflight (python -), lxml (-m integration), browser (-m browser).
    run grep -n -o -E 'python -|-m integration|-m browser' "$STUB_UV_CALL_LOG"
    [ "${lines[0]#*:}" = "python -" ]
    [ "${lines[1]#*:}" = "-m integration" ]
    [ "${lines[2]#*:}" = "-m browser" ]
}

@test "a deleted admin file counts as an admin change" {
    git -C "$FIX" rm --quiet src/templates/admin/page.html
    git -C "$FIX" commit --quiet --no-verify -m "drop page"
    run_wrapper
    [ "$status" -eq 0 ]
    tiers_ran
}

@test "an admin file renamed out of scope counts as an admin change" {
    git -C "$FIX" mv src/templates/admin/page.html src/core/page.html
    git -C "$FIX" commit --quiet --no-verify -m "move page"
    run_wrapper
    tiers_ran
}

@test "an uncommitted admin change runs the tiers" {
    echo '<p>dirty</p>' >> "$FIX/src/templates/admin/page.html"
    run_wrapper
    tiers_ran
}

@test "an untracked admin file runs the tiers" {
    echo '<p>new</p>' > "$FIX/src/templates/admin/new.html"
    run_wrapper
    tiers_ran
}

@test "no base ref fails closed: the tiers run" {
    git -C "$FIX" update-ref -d refs/remotes/origin/main
    commit_change src/core/thing.py 'y = 2'
    run_wrapper
    [ "$status" -eq 0 ]
    tiers_ran
    [[ "$output" == *"cannot tell what changed"* ]]
}

@test "a missing path list fails closed: the tiers run" {
    rm "$FIX/scripts/pre-ship-a11y-paths"
    run_wrapper
    tiers_ran
    [[ "$output" == *"cannot tell what changed"* ]]
}

@test "a path list with no entries fails closed: the tiers run" {
    printf '# only a comment\n\n' > "$FIX/scripts/pre-ship-a11y-paths"
    run_wrapper
    tiers_ran
}

@test "PRE_SHIP_A11Y=on runs the tiers on a non-admin diff" {
    commit_change src/core/thing.py 'y = 2'
    PRE_SHIP_A11Y=on run_wrapper
    tiers_ran
}

# --- preflight: the infrastructure, labelled as infrastructure ----------------

@test "a missing browser is exit 2 and runs neither tier nor gate" {
    commit_change src/templates/admin/page.html '<p>more</p>'
    export STUB_UV_GUARD_RC=5
    run_wrapper
    [ "$status" -eq 2 ]
    [ "$(call_count "$STUB_UV_CALL_LOG" '-m ')" -eq 0 ]
    [ ! -s "$DELEGATE_LOG" ]
    [[ "$output" == *"Chromium"* ]]
    [[ "$output" == *"playwright install chromium"* ]]
}

@test "an unset TEST_DATABASE_URL is exit 2, named as a vacuous pass" {
    commit_change src/templates/admin/page.html '<p>more</p>'
    export STUB_UV_GUARD_RC=3
    run_wrapper
    [ "$status" -eq 2 ]
    [[ "$output" == *"TEST_DATABASE_URL"* ]]
    [ ! -s "$DELEGATE_LOG" ]
}

@test "an unreachable test DB is exit 2, named as infrastructure and not an a11y finding" {
    commit_change src/templates/admin/page.html '<p>more</p>'
    export STUB_UV_GUARD_RC=4
    run_wrapper
    [ "$status" -eq 2 ]
    [[ "$output" == *"not an a11y finding"* ]]
    [[ "$output" == *"RUNBOOK_DB_TRIAGE"* ]]
    [ ! -s "$DELEGATE_LOG" ]
}

@test "the tiers get the env files, and the gate does not" {
    commit_change src/templates/admin/page.html '<p>more</p>'
    echo 'TEST_DATABASE_URL=postgresql://x/y_test' > "$FIX/.env"
    mkdir -p "$FIX/.git/info"
    echo '.env' > "$FIX/.git/info/exclude"
    run_wrapper
    [ "$status" -eq 0 ]
    grep -- '-m integration' "$STUB_UV_CALL_LOG" | grep -q -- "--env-file $FIX/.env"
    grep -- '-m browser' "$STUB_UV_CALL_LOG" | grep -q -- "--env-file $FIX/.env"
    grep -- 'python -' "$STUB_UV_CALL_LOG" | grep -q -- "--env-file $FIX/.env"
    grep -q 'tdu=$' "$DELEGATE_LOG"
}

# --- the tiers ------------------------------------------------------------------

@test "a failing tier is exit 1, and the vendored gate still runs" {
    commit_change src/templates/admin/page.html '<p>more</p>'
    export STUB_UV_LXML_RC=1 STUB_UV_LXML_OUTPUT="1 failed, 197 passed in 14.0s"
    run_wrapper
    [ "$status" -eq 1 ]
    tiers_ran
    delegate_ran
    [[ "$output" == *"a11y: FAILED"* ]]
}

@test "a failing tier and a gate tooling failure is exit 2" {
    commit_change src/templates/admin/page.html '<p>more</p>'
    export STUB_UV_BROWSER_RC=1 STUB_UV_BROWSER_OUTPUT="1 failed, 50 passed"
    export STUB_DELEGATE_RC=2
    run_wrapper
    [ "$status" -eq 2 ]
}

@test "a tier that exits 0 without one pass is a vacuous pass: exit 2" {
    commit_change src/templates/admin/page.html '<p>more</p>'
    export STUB_UV_BROWSER_OUTPUT="215 skipped in 3.10s"
    run_wrapper
    [ "$status" -eq 2 ]
    [[ "$output" == *"vacuous"* ]]
    [ ! -s "$DELEGATE_LOG" ]
}

@test "a tier collecting nothing (pytest exit 5) is exit 2" {
    commit_change src/templates/admin/page.html '<p>more</p>'
    export STUB_UV_LXML_RC=5 STUB_UV_LXML_OUTPUT="no tests ran in 0.01s"
    run_wrapper
    [ "$status" -eq 2 ]
    [ ! -s "$DELEGATE_LOG" ]
}

# --- the shared test DB lock ----------------------------------------------------

@test "a held lock makes the wrapper wait, then give up with exit 2, running nothing" {
    commit_change src/templates/admin/page.html '<p>more</p>'
    exec 8>>"$LOCK"
    flock 8
    PRE_SHIP_A11Y_LOCK_WAIT=1 run_wrapper
    exec 8>&-
    [ "$status" -eq 2 ]
    no_tiers_ran
    [ ! -s "$DELEGATE_LOG" ]
    [[ "$output" == *"another a11y run"* ]]
}

@test "the lock is released before the vendored gate runs" {
    commit_change src/templates/admin/page.html '<p>more</p>'
    run_wrapper
    [ "$status" -eq 0 ]
    grep -q 'lock=free' "$DELEGATE_LOG"
}

@test "a non-admin ship takes no lock and waits for none" {
    commit_change src/core/thing.py 'y = 2'
    exec 8>>"$LOCK"
    flock 8
    PRE_SHIP_A11Y_LOCK_WAIT=1 run_wrapper
    exec 8>&-
    [ "$status" -eq 0 ]
    delegate_ran
}

# --- the hand-off ---------------------------------------------------------------

@test "the vendored gate's exit code is the wrapper's on a green a11y step" {
    commit_change src/templates/admin/page.html '<p>more</p>'
    export STUB_DELEGATE_RC=1
    run_wrapper
    [ "$status" -eq 1 ]
    tiers_ran
}

@test "arguments go to the vendored gate untouched, and run no tier" {
    commit_change src/templates/admin/page.html '<p>more</p>'
    run_wrapper --help
    [ "$status" -eq 0 ]
    no_tiers_ran
    grep -q 'args=\[--help\]' "$DELEGATE_LOG"
    [[ "$output" == *"PRE_SHIP_A11Y"* ]]
}

@test "a missing vendored gate is exit 2 before any tier" {
    commit_change src/templates/admin/page.html '<p>more</p>'
    rm "$FIX/skills/shipping-work/scripts/pre-ship.sh"
    run_wrapper
    [ "$status" -eq 2 ]
    no_tiers_ran
    [[ "$output" == *"git submodule update --init"* ]]
}

# --- the #727 hatch ---------------------------------------------------------------

@test "PRE_SHIP_A11Y=off under CI=true runs no tier and says it verified nothing" {
    commit_change src/templates/admin/page.html '<p>more</p>'
    CI=true PRE_SHIP_A11Y=off run_wrapper
    [ "$status" -eq 0 ]
    no_tiers_ran
    delegate_ran
    [[ "$output" == *"a11y: NOT RUN"* ]]
}

@test "PRE_SHIP_A11Y=off outside CI is refused with exit 2" {
    commit_change src/templates/admin/page.html '<p>more</p>'
    PRE_SHIP_A11Y=off run_wrapper
    [ "$status" -eq 2 ]
    no_tiers_ran
    [ ! -s "$DELEGATE_LOG" ]
}

@test "an unknown PRE_SHIP_A11Y value is refused with exit 2" {
    PRE_SHIP_A11Y=sometimes run_wrapper
    [ "$status" -eq 2 ]
    [ ! -s "$DELEGATE_LOG" ]
}
