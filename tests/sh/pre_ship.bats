#!/usr/bin/env bats
# Tests for scripts/pre-ship.sh, the project-local ship gate (#539, CR 11).
#
# `tests/scripts/test_pre_ship_gate.py` asserts the script's TEXT — that it
# covers every vendored stage, that it passes no `-m`, that the divergence still
# has a reason. None of that runs the gate, so a stage silently dropped by a
# later edit would still pass it as long as the banner string survived. This
# suite runs it.
#
# Hermetic: `uv` and `npm` are PATH shims written into $BATS_TEST_TMPDIR, and the
# gate runs against a throwaway git repo built there — no real lint, no real
# pytest, no network. Git is real but confined to the tmpdir.
#
# The stamp slot is real (/tmp, keyed on the fixture repo's unique basename) and
# teardown removes it, because the stamp-skip path cannot be tested without one.

setup() {
    GATE="$BATS_TEST_DIRNAME/../../scripts/pre-ship.sh"

    STUBS="$BATS_TEST_TMPDIR/bin"
    mkdir -p "$STUBS"
    export UV_CALL_LOG="$BATS_TEST_TMPDIR/uv-calls.log"

    # `uv` shim. Shapes the gate uses: ruff check, ruff format --check,
    # `python -c "import pytest_cov"`, and the pytest run. Each maps to a knob so
    # a test can fail exactly one stage.
    cat > "$STUBS/uv" <<'SH'
#!/usr/bin/env bash
set -u
args="$*"
# A canonical shape tag, then the raw argv. The probe below also contains the
# string "pytest", so "did the suite run" must key on the tag, not the argv.
case "$args" in
    *"ruff check"*)        shape=RUFF_CHECK ;;
    *"ruff format"*)       shape=RUFF_FORMAT ;;
    *"import pytest_cov"*) shape=COV_PROBE ;;
    *pytest*)              shape=PYTEST ;;
    *)                     shape=OTHER ;;
esac
echo "$shape uv $*" >> "$UV_CALL_LOG"
case "$shape" in
    RUFF_CHECK)  exit "${STUB_RUFF_CHECK_RC:-0}" ;;
    RUFF_FORMAT) exit "${STUB_RUFF_FORMAT_RC:-0}" ;;
    COV_PROBE)   exit "${STUB_PYTEST_COV_RC:-0}" ;;
    PYTEST)      exit "${STUB_PYTEST_RC:-0}" ;;
esac
exit 0
SH
    chmod +x "$STUBS/uv"
    PATH="$STUBS:$PATH"
    export PATH

    # Hermetic against git's OWN environment, which is the whole difficulty. When
    # this suite runs from a pre-commit hook, `git commit` has exported GIT_DIR
    # and GIT_INDEX_FILE pointing at the REAL repository — and those beat `-C`,
    # so every fixture call below would operate on the developer's index instead.
    # That is not hypothetical: it staged a fixture file into this repo before the
    # scrub was added. `setup_worktree_fixture` in helpers.bash carries the same
    # scrub for the same reason.
    unset GIT_DIR GIT_WORK_TREE GIT_INDEX_FILE GIT_PREFIX GIT_COMMON_DIR
    unset GIT_OBJECT_DIRECTORY GIT_ALTERNATE_OBJECT_DIRECTORIES
    unset GIT_AUTHOR_DATE GIT_COMMITTER_DATE GIT_EDITOR GIT_REFLOG_ACTION

    # A throwaway repo with one commit. No package.json, so the JS block is
    # skipped — the JS stages are the vendored script's and are not what diverges.
    REPO="$BATS_TEST_TMPDIR/fixture-repo"
    mkdir -p "$REPO"
    git -C "$REPO" init --quiet
    git -C "$REPO" config user.email t@example.invalid
    git -C "$REPO" config user.name t
    echo x > "$REPO/file.txt"
    git -C "$REPO" add file.txt
    git -C "$REPO" commit --quiet -m init

    # The scrub above is load-bearing; assert it took rather than trusting it.
    [ -d "$REPO/.git" ] || {
        echo "fixture repo was not created — GIT_* scrub failed" >&2
        return 1
    }

    SHA="$(git -C "$REPO" rev-parse HEAD)"
    STAMP="/tmp/$(basename "$REPO")-tests-clean-local-${SHA}"
    rm -f "$STAMP"
}

teardown() {
    [ -n "${STAMP:-}" ] && rm -f "$STAMP"
    return 0
}

run_gate() {
    # The knobs must be EXPORTED: the gate runs in a `bash -c` child, which
    # inherits only exported variables, so a bare `VAR=1 run_gate` prefix would
    # set them in this function's scope and never reach the stub.
    export STUB_RUFF_CHECK_RC="${STUB_RUFF_CHECK_RC:-0}"
    export STUB_RUFF_FORMAT_RC="${STUB_RUFF_FORMAT_RC:-0}"
    export STUB_PYTEST_COV_RC="${STUB_PYTEST_COV_RC:-0}"
    export STUB_PYTEST_RC="${STUB_PYTEST_RC:-0}"
    run bash -c "cd '$REPO' && bash '$GATE'"
}

# Count invocations of one stub shape, by its tag (0 when the log is absent).
#
# `grep -c` prints "0" AND exits 1 when it matches nothing, so the familiar
# `|| echo 0` emits the count TWICE on exactly the runs that expect zero — and
# `[ "0\n0" -eq 0 ]` is an error, not a pass. Capture first, default after.
uv_shape_calls() {
    local n
    n=$(grep -c "^$1 " "$UV_CALL_LOG" 2>/dev/null) || n=0
    echo "${n:-0}"
}

# --- help -------------------------------------------------------------------

@test "--help exits 0 and documents the exit codes" {
    run bash "$GATE" --help
    [ "$status" -eq 0 ]
    [[ "$output" == *"Exit codes:"* ]]
    [[ "$output" == *"pre-ship"* ]]
}

@test "--help runs nothing" {
    run bash "$GATE" --help
    [ "$status" -eq 0 ]
    [ ! -s "$UV_CALL_LOG" ] || [ "$(uv_shape_calls PYTEST)" -eq 0 ]
}

# --- stage ordering and short-circuit ---------------------------------------

@test "a green run reaches every stage and passes" {
    run_gate
    [ "$status" -eq 0 ]
    [[ "$output" == *"Lint (ruff)"* ]]
    [[ "$output" == *"Format check (ruff)"* ]]
    [[ "$output" == *"Tests (Python)"* ]]
    [[ "$output" == *"Pre-ship checks passed."* ]]
}

@test "a ruff failure stops the gate before pytest" {
    STUB_RUFF_CHECK_RC=1 run_gate
    [ "$status" -ne 0 ]
    [ "$(uv_shape_calls PYTEST)" -eq 0 ]
}

@test "a format failure stops the gate before pytest" {
    STUB_RUFF_FORMAT_RC=1 run_gate
    [ "$status" -ne 0 ]
    [ "$(uv_shape_calls PYTEST)" -eq 0 ]
}

@test "a pytest failure fails the gate with pytest's own status" {
    STUB_PYTEST_RC=3 run_gate
    [ "$status" -eq 3 ]
    [[ "$output" != *"Pre-ship checks passed."* ]]
}

@test "exit 5 (nothing collected) is tolerated, as upstream tolerates it" {
    STUB_PYTEST_RC=5 run_gate
    [ "$status" -eq 0 ]
    [[ "$output" == *"Pre-ship checks passed."* ]]
}

# --- the #539 invocation ----------------------------------------------------

@test "pytest is invoked with --group seed and no -m" {
    run_gate
    [ "$status" -eq 0 ]
    run grep -- "^PYTEST " "$UV_CALL_LOG"
    [[ "$output" == *"--group seed"* ]]
    # Any -m here would replace the addopts marker wholesale — the whole defect.
    [[ "$output" != *" -m "* ]]
}

@test "--no-cov is passed only when pytest-cov is importable" {
    STUB_PYTEST_COV_RC=1 run_gate
    [ "$status" -eq 0 ]
    run grep -- "^PYTEST " "$UV_CALL_LOG"
    [[ "$output" != *"--no-cov"* ]]
}

# --- the stamp --------------------------------------------------------------

@test "a clean green run writes the stamp" {
    run_gate
    [ "$status" -eq 0 ]
    [ -f "$STAMP" ]
}

@test "the stamp is the local slot, never the vendored gate's" {
    run_gate
    [ "$status" -eq 0 ]
    # CR 8: the two gates run different selections, so one verdict must never
    # speak for the other's.
    [ ! -f "/tmp/$(basename "$REPO")-tests-clean-${SHA}" ]
}

@test "an existing stamp on a clean tree skips pytest" {
    touch "$STAMP"
    run_gate
    [ "$status" -eq 0 ]
    [[ "$output" == *"already passed"* ]]
    [ "$(uv_shape_calls PYTEST)" -eq 0 ]
}

@test "a dirty tree ignores the stamp and runs the suite" {
    touch "$STAMP"
    echo dirty >> "$REPO/file.txt"
    run_gate
    [ "$status" -eq 0 ]
    [ "$(uv_shape_calls PYTEST)" -ge 1 ]
}

@test "a failing suite does not write the stamp" {
    STUB_PYTEST_RC=1 run_gate
    [ "$status" -ne 0 ]
    [ ! -f "$STAMP" ]
}

# --- tooling failures are exit 2, distinct from a gate failure --------------

@test "a missing uv is exit 2, not a lint failure" {
    # Removing the stub is not enough: a real uv lives on the system PATH. Narrow
    # PATH to the stub dir plus what git/mktemp/grep need.
    rm "$STUBS/uv"
    export PATH="$STUBS:/usr/bin:/bin"
    run_gate
    [ "$status" -eq 2 ]
    [[ "$output" == *"uv not installed"* ]]
}
