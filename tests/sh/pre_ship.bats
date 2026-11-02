#!/usr/bin/env bats
# The ship gate as this repo runs it: the VENDORED pre-ship.sh plus our
# `.skills/pre-ship-uv-args` (#549; the local copy #539 added is retired).
#
# `tests/scripts/test_pre_ship_gate.py` asserts the configuration's TEXT — no
# local gate, the knob upstream reads, the hook's uv args. None of that runs the
# gate, so this suite does: our real args file, copied into a fixture repo, must
# reach every uv call, and pytest must run without a `-m` (any `-m` replaces the
# addopts marker and requests the browser tier — the #539 defect). The gate's own
# stamp and help are upstream's to test, not ours; we assert only that a pytest
# failure fails it.
#
# The gate is the unified `shipping-work` one (#739): it detects the stack, so the
# fixture carries a uv `pyproject.toml` naming fastapi, as this repo's does.
#
# Hermetic: `uv` is a PATH shim written into $BATS_TEST_TMPDIR, and the gate runs
# against a throwaway git repo built there — no real lint, no real pytest, no
# network. Git is real but confined to the tmpdir, and so is the gate's pass
# stamp, which lives under the fixture's git common directory.

setup() {
    ROOT="$BATS_TEST_DIRNAME/../.."
    # The symlink path Step 1 resolves, never skills-vendor/: that is the stable
    # interface, the vendor layout is not.
    GATE="$ROOT/skills/shipping-work/scripts/pre-ship.sh"
    [ -f "$GATE" ] || skip "vendored gate absent — git submodule update --init"

    STUBS="$BATS_TEST_TMPDIR/bin"
    mkdir -p "$STUBS"
    export UV_CALL_LOG="$BATS_TEST_TMPDIR/uv-calls.log"

    # `uv` shim. A canonical shape tag, then the raw argv. The cov probe also
    # contains "pytest", so "did the suite run" keys on the tag, not the argv.
    # The xdist probe fails, as it does in this repo's environment, so the
    # gate runs serially on any core count. Newlines in an argv (a `python -c`
    # script) are folded, so one call is one log line.
    cat > "$STUBS/uv" <<'SH'
#!/usr/bin/env bash
set -u
args="$*"
case "$args" in
    *"ruff check"*)        shape=RUFF_CHECK ;;
    *"ruff format"*)       shape=RUFF_FORMAT ;;
    *"import pytest_cov"*) shape=COV_PROBE ;;
    *"import xdist"*)      shape=XDIST_PROBE ;;
    *"python --version"*)  shape=PY_VERSION ;;
    *"import tomllib"*)    shape=NAME_PROBE ;;
    *pytest*)              shape=PYTEST ;;
    *)                     shape=OTHER ;;
esac
printf '%s uv %s\n' "$shape" "${args//$'\n'/ }" >> "$UV_CALL_LOG"
case "$shape" in
    PYTEST)      exit "${STUB_PYTEST_RC:-0}" ;;
    XDIST_PROBE) exit 1 ;;
    PY_VERSION)  echo "Python 3.12.0" ;;
esac
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

    # A throwaway repo carrying our real args file and a uv pyproject that
    # names fastapi, committed so the tree is clean. No package.json, so the
    # JS block is skipped.
    REPO="$BATS_TEST_TMPDIR/fixture-repo"
    mkdir -p "$REPO/.skills"
    cp "$ROOT/.skills/pre-ship-uv-args" "$REPO/.skills/pre-ship-uv-args"
    printf '[project]\nname = "fixture"\nversion = "0"\ndependencies = ["fastapi"]\n\n[tool.uv]\n' \
        > "$REPO/pyproject.toml"
    git -C "$REPO" init --quiet
    git -C "$REPO" config user.email t@example.invalid
    git -C "$REPO" config user.name t
    git -C "$REPO" config commit.gpgsign false
    git -C "$REPO" add .skills/pre-ship-uv-args pyproject.toml
    git -C "$REPO" commit --quiet -m init

    # The scrub above is load-bearing; assert it took rather than trusting it.
    [ -d "$REPO/.git" ] || {
        echo "fixture repo was not created — GIT_* scrub failed" >&2
        return 1
    }
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
    [[ "$output" == *"Detected: toolchain uv, framework fastapi"* ]]
    [[ "$output" == *"Lint (ruff)"* ]]
    [[ "$output" == *"Format check (ruff)"* ]]
    [[ "$output" == *"Tests (pytest)"* ]]
    [[ "$output" == *"Pre-ship checks passed."* ]]
}

@test "our uv args reach every uv call" {
    run_gate
    [ "$status" -eq 0 ]
    local args shape
    args="$(uv_args)"
    [ -n "$args" ]
    for shape in RUFF_CHECK RUFF_FORMAT COV_PROBE PY_VERSION PYTEST; do
        run grep -- "^$shape uv run $args " "$UV_CALL_LOG"
        [ "$status" -eq 0 ] || { echo "$shape ran without '$args'" >&2; cat "$UV_CALL_LOG" >&2; return 1; }
    done
    # The shapes above must be there; no other `uv run` may lack the args — a
    # call upstream adds later is otherwise invisible to the loop. Exempt:
    # detect-import-targets.sh's [project]-name probe, a bare `uv run` upstream
    # does not route through the args file (it only reads pyproject.toml).
    run bash -c 'grep -- "^[A-Z_]* uv run " "$1" | grep -v -- "^NAME_PROBE " | grep -v -- "^[A-Z_]* uv run $2 "' _ "$UV_CALL_LOG" "$args"
    [ "$status" -eq 1 ] || { echo "uv run without '$args':" >&2; echo "$output" >&2; return 1; }
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
    # The gate runs every check, then exits 1 naming each failure.
    STUB_PYTEST_RC=3 run_gate
    [ "$status" -eq 1 ]
    [[ "$output" == *"Tests (pytest) (exit 3)"* ]]
    [[ "$output" != *"Pre-ship checks passed."* ]]
}
