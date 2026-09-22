#!/usr/bin/env bats
# The ship gate as this repo runs it: the VENDORED pre-ship.sh plus our
# `.skills/pre-ship-uv-args` (#549; the local copy #539 added is retired).
#
# `tests/scripts/test_pre_ship_gate.py` asserts the configuration's TEXT — no
# local gate, the knob upstream reads, the hook's uv args. None of that runs the
# gate, so this suite does: our real args file, copied into a fixture repo, must
# reach every uv call, and pytest must run without a `-m` (any `-m` replaces the
# addopts marker and requests the browser tier — the #539 defect). The gate's own
# stamp, help and exit-code behaviour are upstream's to test, not ours.
#
# Hermetic: `uv` is a PATH shim written into $BATS_TEST_TMPDIR, and the gate runs
# against a throwaway git repo built there — no real lint, no real pytest, no
# network. Git is real but confined to the tmpdir. The gate's per-SHA stamp lands
# in /tmp keyed on the fixture repo, so setup and teardown remove it.

setup() {
    ROOT="$BATS_TEST_DIRNAME/../.."
    # The symlink path Step 1 resolves, never skills-vendor/: that is the stable
    # interface, the vendor layout is not.
    GATE="$ROOT/skills/shipping-work-python-fastapi/scripts/pre-ship.sh"
    [ -f "$GATE" ] || skip "vendored gate absent — git submodule update --init"

    STUBS="$BATS_TEST_TMPDIR/bin"
    mkdir -p "$STUBS"
    export UV_CALL_LOG="$BATS_TEST_TMPDIR/uv-calls.log"

    # `uv` shim. A canonical shape tag, then the raw argv. The cov probe also
    # contains "pytest", so "did the suite run" keys on the tag, not the argv.
    cat > "$STUBS/uv" <<'SH'
#!/usr/bin/env bash
set -u
args="$*"
case "$args" in
    *"ruff check"*)        shape=RUFF_CHECK ;;
    *"ruff format"*)       shape=RUFF_FORMAT ;;
    *"import pytest_cov"*) shape=COV_PROBE ;;
    *pytest*)              shape=PYTEST ;;
    *)                     shape=OTHER ;;
esac
echo "$shape uv $*" >> "$UV_CALL_LOG"
[ "$shape" = PYTEST ] && exit "${STUB_PYTEST_RC:-0}"
exit 0
SH
    chmod +x "$STUBS/uv"
    PATH="$STUBS:$PATH"
    export PATH

    # Hermetic against git's own environment: under a pre-commit hook, `git
    # commit` exports GIT_DIR and GIT_INDEX_FILE for the REAL repository, and
    # those beat `-C` — a fixture call would then stage into this repo.
    unset GIT_DIR GIT_WORK_TREE GIT_INDEX_FILE GIT_PREFIX GIT_COMMON_DIR
    unset GIT_OBJECT_DIRECTORY GIT_ALTERNATE_OBJECT_DIRECTORIES
    unset GIT_AUTHOR_DATE GIT_COMMITTER_DATE GIT_EDITOR GIT_REFLOG_ACTION

    # A throwaway repo carrying our real args file, committed so the tree is
    # clean. No package.json, so the JS block is skipped.
    REPO="$BATS_TEST_TMPDIR/fixture-repo"
    mkdir -p "$REPO/.skills"
    cp "$ROOT/.skills/pre-ship-uv-args" "$REPO/.skills/pre-ship-uv-args"
    git -C "$REPO" init --quiet
    git -C "$REPO" config user.email t@example.invalid
    git -C "$REPO" config user.name t
    git -C "$REPO" add .skills/pre-ship-uv-args
    git -C "$REPO" commit --quiet -m init

    # The scrub above is load-bearing; assert it took rather than trusting it.
    [ -d "$REPO/.git" ] || {
        echo "fixture repo was not created — GIT_* scrub failed" >&2
        return 1
    }

    STAMP="/tmp/$(basename "$REPO")-tests-clean-$(git -C "$REPO" rev-parse HEAD)"
    rm -f "$STAMP"
}

teardown() {
    [ -n "${STAMP:-}" ] && rm -f "$STAMP"
    return 0
}

run_gate() {
    # Exported: the gate runs in a `bash -c` child, which inherits only exports.
    export STUB_PYTEST_RC="${STUB_PYTEST_RC:-0}"
    run bash -c "cd '$REPO' && bash '$GATE'"
}

# The uv-args file's words, as one string ("--group seed").
uv_args() {
    grep -v '^[[:space:]]*#' "$ROOT/.skills/pre-ship-uv-args" | tr -s '[:space:]' ' ' | sed 's/^ //; s/ $//'
}

@test "a green run reaches every stage and passes" {
    run_gate
    [ "$status" -eq 0 ]
    [[ "$output" == *"Lint (ruff)"* ]]
    [[ "$output" == *"Format check (ruff)"* ]]
    [[ "$output" == *"Tests (Python)"* ]]
    [[ "$output" == *"Pre-ship checks passed."* ]]
}

@test "our uv args reach every uv call" {
    run_gate
    [ "$status" -eq 0 ]
    local args shape
    args="$(uv_args)"
    [ -n "$args" ]
    for shape in RUFF_CHECK RUFF_FORMAT COV_PROBE PYTEST; do
        run grep -- "^$shape uv run $args " "$UV_CALL_LOG"
        [ "$status" -eq 0 ] || { echo "$shape ran without '$args'" >&2; cat "$UV_CALL_LOG" >&2; return 1; }
    done
}

@test "pytest runs with --group seed and no -m" {
    run_gate
    [ "$status" -eq 0 ]
    run grep -- "^PYTEST " "$UV_CALL_LOG"
    [ "$status" -eq 0 ]
    [[ "$output" == *"--group seed"* ]]
    # Any -m would replace the addopts marker wholesale — the #539 defect.
    [[ "$output" != *" -m "* ]]
}

@test "a pytest failure fails the gate" {
    STUB_PYTEST_RC=3 run_gate
    [ "$status" -eq 3 ]
    [[ "$output" != *"Pre-ship checks passed."* ]]
}
