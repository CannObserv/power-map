# Shared bats helpers for the shell-entrypoint suite (#373).
#
# Everything is hermetic: external commands (uv, gh, systemctl) are PATH shims
# from tests/sh/stubs/, configured per-test through STUB_* / GH_STUB_* /
# SYSTEMCTL_STUB_* env vars and recording into $BATS_TEST_TMPDIR logs.
# Nothing real is invoked — no network, no GitHub, no systemd, no DB.

# Repo root, resolved from this file's location (tests/sh/ → two up).
repo_root() {
    cd "$BATS_TEST_DIRNAME/../.." && pwd
}

# Prepend the stub dir to PATH and point the recording logs at tmpdir.
# Call from setup() in suites that exercise scripts/run-a11y-sweep.sh.
setup_sweep_stubs() {
    STUBS_DIR="$BATS_TEST_DIRNAME/stubs"
    PATH="$STUBS_DIR:$PATH"
    export PATH

    export STUB_UV_CALL_LOG="$BATS_TEST_TMPDIR/uv-calls.log"
    export GH_STUB_CALL_LOG="$BATS_TEST_TMPDIR/gh-calls.log"
    export GH_STUB_BODY_LOG="$BATS_TEST_TMPDIR/gh-bodies.log"

    # Default stub posture: label exists, no open issue, gh healthy, tiers green.
    export GH_STUB_LABELS="a11y-regression"
    export GH_STUB_OPEN_ISSUE=""
    export GH_STUB_LIST_RC=0
    export STUB_UV_GUARD_RC=0
    export STUB_UV_LXML_RC=0
    export STUB_UV_BROWSER_RC=0

    # Make sure the script's own hatches don't leak in from the caller env.
    unset A11Y_SWEEP_NO_GH A11Y_SWEEP_FORCE_FAIL
}

run_sweep() {
    run bash "$(repo_root)/scripts/run-a11y-sweep.sh"
}

# Count invocations matching a grep pattern in a call log (0 if log absent).
call_count() {
    local log="$1" pattern="$2"
    if [ -f "$log" ]; then
        grep -c -- "$pattern" "$log" || true
    else
        echo 0
    fi
}

# --- reminder-hook helpers --------------------------------------------------

# Build a minimal PATH for .claude/hooks/a11y-status-reminder.sh: only the
# tools the hook needs (date), plus optionally the systemctl stub. Running with
# BASE_BIN alone simulates a host with no systemctl at all (off-VM).
setup_reminder_paths() {
    BASE_BIN="$BATS_TEST_TMPDIR/base-bin"
    mkdir -p "$BASE_BIN"
    # bash twice over: for `env … bash` itself and for the stubs' shebangs.
    for tool in bash date; do
        ln -s "$(command -v "$tool")" "$BASE_BIN/$tool"
    done
    STUBS_DIR="$BATS_TEST_DIRNAME/stubs"
    HOOK="$(repo_root)/.claude/hooks/a11y-status-reminder.sh"
}

run_hook_without_systemctl() {
    run env PATH="$BASE_BIN" bash "$HOOK"
}

run_hook_with_systemctl() {
    # env vars configuring the stub must already be exported by the test.
    run env PATH="$STUBS_DIR:$BASE_BIN" bash "$HOOK"
}

# --- worktree-setup helpers (#450) ------------------------------------------

# Build a throwaway repo ($FAKE_MAIN) with one linked worktree
# ($FAKE_WORKTREE) under $BATS_TEST_TMPDIR, and put the uv stub on PATH.
# Real git, so `worktree-setup.sh`'s main-checkout guard is tested against a
# genuine linked worktree rather than a fabricated .git file.
#
# Hermetic against git's own environment, which is the whole difficulty here:
# when this suite runs from a pre-commit hook, `git commit` has exported
# GIT_DIR / GIT_INDEX_FILE pointing at the REAL repository, and every fixture
# `git` call would operate on that instead — staging the fixture's files into
# the developer's index. Global config is neutralised for the same reason
# (`core.hooksPath` would run the real hooks against the fixture), and the
# assertion below refuses to continue if any of it fails to take.
setup_worktree_fixture() {
    PATH="$BATS_TEST_DIRNAME/stubs:$PATH"
    export PATH
    export STUB_UV_CALL_LOG="$BATS_TEST_TMPDIR/uv-calls.log"
    export STUB_UV_SYNC_RC=0

    unset GIT_DIR GIT_WORK_TREE GIT_INDEX_FILE GIT_PREFIX GIT_COMMON_DIR
    unset GIT_OBJECT_DIRECTORY GIT_ALTERNATE_OBJECT_DIRECTORIES
    unset GIT_AUTHOR_DATE GIT_COMMITTER_DATE GIT_EDITOR GIT_REFLOG_ACTION

    # A tmpdir global config rather than /dev/null: the developer's own global
    # config is still neutralised, but `protocol.file.allow` has to come from
    # somewhere. Submodule clones run with file transport disabled (the fix for
    # CVE-2022-39253), and the setting is ignored from the superproject's own
    # config — the clone reads it as the submodule repo. Without it every
    # fixture submodule fails to clone from its file:// source.
    export GIT_CONFIG_GLOBAL="$BATS_TEST_TMPDIR/gitconfig-global"
    export GIT_CONFIG_SYSTEM=/dev/null
    printf '[protocol "file"]\n\tallow = always\n' > "$GIT_CONFIG_GLOBAL"

    FAKE_MAIN="$BATS_TEST_TMPDIR/main"
    FAKE_WORKTREE="$BATS_TEST_TMPDIR/wt/feature"
    export FAKE_MAIN FAKE_WORKTREE

    mkdir -p "$FAKE_MAIN"
    # --template= keeps `pre-commit init-templatedir` hooks out of the fixture.
    git -C "$FAKE_MAIN" init --quiet --initial-branch=main --template=
    git -C "$FAKE_MAIN" config user.email bats@example.invalid
    git -C "$FAKE_MAIN" config user.name bats
    git -C "$FAKE_MAIN" config core.hooksPath /dev/null

    # Refuse to touch anything outside the tmpdir, whatever the caller's env.
    local resolved
    resolved="$(cd "$FAKE_MAIN" && git rev-parse --absolute-git-dir)"
    case "$resolved" in
        "$BATS_TEST_TMPDIR"/*) : ;;
        *) echo "fixture escaped its tmpdir: git dir is $resolved" >&2; return 1 ;;
    esac

    : > "$FAKE_MAIN/README.md"
    git -C "$FAKE_MAIN" add README.md
    git -C "$FAKE_MAIN" commit --quiet --no-verify -m "init"
    git -C "$FAKE_MAIN" worktree add --quiet -b feature "$FAKE_WORKTREE" >/dev/null
}

# Add a submodule at skills-vendor/$1 (default: thing) to $FAKE_MAIN and
# fast-forward the linked worktree onto it, leaving the worktree in the state a
# fresh `worktree-create.sh` produces: .gitmodules present, submodule directory
# empty (#482).
#
# The source is a throwaway repo in $BATS_TEST_TMPDIR reached over file
# transport — see setup_worktree_fixture for why that needs a global config.
add_fixture_submodule() {
    local name="${1:-thing}"
    local src="$BATS_TEST_TMPDIR/sub-$name"

    mkdir -p "$src"
    git -C "$src" init --quiet --initial-branch=main --template=
    git -C "$src" config user.email bats@example.invalid
    git -C "$src" config user.name bats
    git -C "$src" config core.hooksPath /dev/null
    : > "$src/SKILL.md"
    git -C "$src" add SKILL.md
    git -C "$src" commit --quiet --no-verify -m "vendored skill"

    git -C "$FAKE_MAIN" submodule add --quiet "$src" "skills-vendor/$name"
    git -C "$FAKE_MAIN" commit --quiet --no-verify -m "vendor $name"
    # The worktree branch was cut before the submodule commit, so it carries no
    # .gitmodules until it catches up. Tracked files only — the .venv and .env
    # this suite fusses over are untracked and survive.
    git -C "$FAKE_WORKTREE" reset --hard --quiet main
}

# Point a fixture submodule at a commit nothing has, so
# `git submodule update --init` fails the way an unreachable source does: it
# can populate the worktree only if it can resolve the recorded gitlink.
#
# The gitlink, not the URL: `submodule add` already cloned the source into
# <common-dir>/modules/, so an update in the worktree reuses that gitdir and
# never touches the URL at all.
break_fixture_submodule() {
    local name="${1:-thing}"
    local bogus="0000000000000000000000000000000000000001"
    git -C "$FAKE_MAIN" update-index --add --cacheinfo "160000,$bogus,skills-vendor/$name"
    git -C "$FAKE_MAIN" commit --quiet --no-verify -m "break $name gitlink"
    git -C "$FAKE_WORKTREE" reset --hard --quiet main
}
