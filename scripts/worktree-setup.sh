#!/usr/bin/env bash
# Finish setting up a linked git worktree: give it its own venv and an .env.
#
# Usage:
#   bash scripts/worktree-setup.sh                 # the current directory
#   bash scripts/worktree-setup.sh <worktree-path>
#
# Run once, straight after `worktree-create.sh`. Idempotent — re-run any time.
#
# Why the venv (#450). `worktree-create.sh` links a new worktree's .venv at the
# main checkout's, so every checkout shares one mutable environment while being
# isolated in every other respect. The main checkout is production's working
# directory: nine systemd units run `uv run` there (`power-map-ready` every two
# minutes), and `uv run` reinstalls the project, so a worktree suite gets its
# version metadata restamped to main's mid-run. `power-map.service`'s
# `ExecStartPre=uv sync` goes further and prunes the opt-in groups, taking the
# ~200-test browser tier with it — an `importorskip` away from a silent pass.
# The link bought a dependency resolve that a warm uv cache does in under a
# second, hardlinked, so it was never worth the shared mutable state.
#
# The groups are synced here on purpose: a worktree that only ever runs the
# default `dev` group is a worktree whose browser and seed tiers quietly do not
# exist. `tests/conftest.py` announces them when they are absent.
#
# Why the rest (#482). A worktree arrives carrying neither its submodules nor
# anything gitignored, so an agent's first act — establish a baseline — is red
# or off-by-one for reasons that have nothing to do with its work: the
# skills-vendor submodules are empty directories (three of
# `tests/test_vendor_skills.py` fail), and `data/cannabis_observer/` is absent
# (`test_seed_jurisdictions.py` skips one test, so a fully-provisioned worktree
# reports one pass fewer than the main checkout on an identical tree). A count
# that differs by provisioning is a count nobody can use as a fall-through
# detector, which is what it cost #480.
#
# Exit codes: 0 set up, 1 uv sync failed, 2 refused (not a linked worktree).

set -euo pipefail

usage() {
    cat <<'EOF'
usage: bash scripts/worktree-setup.sh [<worktree-path>]

  <worktree-path>  the linked worktree to set up (default: current directory)

Replaces a shared .venv symlink with a real per-worktree environment
(`uv sync --group browser --group seed`), initialises the skills-vendor
submodules, and symlinks the gitignored .env and data/cannabis_observer from
the main checkout. Refuses to run against the main checkout.
EOF
}

case "${1:-}" in
    -h|--help) usage; exit 0 ;;
esac

TARGET="${1:-$PWD}"

if [ ! -d "$TARGET" ]; then
    echo "ERROR: no such directory: $TARGET" >&2
    exit 2
fi
TARGET="$(cd "$TARGET" && pwd -P)"

# ── Guard: linked worktree only ──────────────────────────────────────────────
# A linked worktree's git dir is <main>/.git/worktrees/<name>; the main
# checkout's git dir *is* the common dir. Unlike apply-schema.sh this guard
# never degrades to a warning: nothing here runs on the systemd path, and
# replacing the main checkout's .venv is exactly the damage to avoid.
if ! git -C "$TARGET" rev-parse --git-dir >/dev/null 2>&1; then
    echo "ERROR: not a git checkout: $TARGET" >&2
    exit 2
fi

# Canonicalise to the checkout root *before* anything reads $TARGET, so the
# guard below and the work further down can never disagree about which
# directory is being set up. Everything here is root-relative: the shared
# .venv symlink lives at the root, and `uv sync` resolves the project root
# itself — so from a subdirectory the symlink would go unseen and the sync
# would install straight through it into the main checkout's venv, which is
# the whole thing this script exists to prevent.
#
# It degrades rather than aborting because `--show-toplevel` fails outright on
# a repo carrying `core.bare = true` despite having a work tree — a state this
# project's main checkout was actually left in. That is the path the guard
# below refuses anyway, and its message is far more useful than git's fatal,
# so an unresolvable root is re-checked *after* the guard instead.
TARGET_ROOT="$(cd "$TARGET" && git rev-parse --show-toplevel 2>/dev/null || true)"
[ -n "$TARGET_ROOT" ] && TARGET="$TARGET_ROOT"

# Read the raw paths first and refuse an empty answer rather than resolving it:
# `cd ""` *succeeds* in bash, so an empty --git-dir would resolve to $TARGET,
# mismatch the common dir and read as a linked worktree — in the main checkout
# that would sync production's venv. apply-schema.sh guards the same shape.
raw_git_dir="$(cd "$TARGET" && git rev-parse --git-dir)"
raw_common_dir="$(cd "$TARGET" && git rev-parse --git-common-dir)"
if [ -z "$raw_git_dir" ] || [ -z "$raw_common_dir" ]; then
    echo "ERROR: git did not report its layout for $TARGET — refusing to guess" >&2
    exit 2
fi

# Both resolved from inside $TARGET: git reports these relative to the
# checkout, so resolving them from the caller's cwd would land elsewhere (or,
# for the main checkout's bare ".git", not resolve at all).
git_dir="$(cd "$TARGET" && cd "$raw_git_dir" && pwd -P)"
common_dir="$(cd "$TARGET" && cd "$raw_common_dir" && pwd -P)"

if [ "$git_dir" = "$common_dir" ]; then
    cat >&2 <<EOF
refusing: $TARGET is the main checkout, not a linked worktree.

The main checkout is production's working directory — its .venv is the one
systemd runs from. Create a worktree first, then set that up:

  bash skills-vendor/gregoryfoster-skills/skills/using-git-worktrees/scripts/worktree-create.sh --new <branch>
  bash scripts/worktree-setup.sh <worktree-path>
EOF
    exit 2
fi

# A linked worktree always has a work tree, so an unresolvable root here is
# unexplained — never guess, the cost of being wrong is production's venv.
if [ -z "$TARGET_ROOT" ]; then
    echo "ERROR: git did not report a checkout root for $TARGET — refusing to guess" >&2
    exit 2
fi

# The main worktree is the first entry of `git worktree list --porcelain`, by
# definition — deriving it from the common dir would assume a <main>/.git
# layout that --separate-git-dir does not have.
MAIN_ROOT="$(cd "$TARGET" && git worktree list --porcelain | awk 'NR==1 {print $2; exit}')"

# ── The venv ─────────────────────────────────────────────────────────────────
if [ -L "$TARGET/.venv" ]; then
    echo "removing the shared .venv symlink -> $(readlink "$TARGET/.venv") (#450)" >&2
    rm "$TARGET/.venv"
fi

echo "syncing $TARGET/.venv (dev + browser + seed)" >&2
if ! (cd "$TARGET" && uv sync --group browser --group seed); then
    echo "ERROR: uv sync failed in $TARGET — the shared symlink (if any) was" >&2
    echo "       already removed, so the worktree has no venv; re-run after fixing" >&2
    exit 1
fi

# ── The vendored skill submodules ────────────────────────────────────────────
# `git worktree add` populates tracked files only: .gitmodules arrives, the
# submodule directories arrive empty. Non-fatal on failure — the source may be
# unreachable (offline host, moved remote) and the venv and links are already
# in place by here — but never silent, because the symptom of skipping it is a
# red baseline that reads as the agent's own doing.
if [ -f "$TARGET/.gitmodules" ] && grep -q 'skills-vendor/' "$TARGET/.gitmodules"; then
    echo "initialising the skills-vendor submodules" >&2
    if ! (cd "$TARGET" && git submodule update --init skills-vendor/); then
        echo "WARN: could not initialise skills-vendor/ — the vendored-driver" >&2
        echo "      guards will fail; re-run from $TARGET when reachable:" >&2
        echo "      git submodule update --init skills-vendor/" >&2
    fi
fi

# ── The shared gitignored paths ──────────────────────────────────────────────
# Gitignored paths never arrive with a worktree, so anything read from one has
# to be linked from the main checkout:
#   .env                    — the GH_* tokens; TEST_DATABASE_URL comes from
#                             /etc/power-map/.env, not this file
#   data/cannabis_observer  — importer and seed fixtures; without it the
#                             worktree's baseline count cannot match main's
#
# A dangling symlink is `-L` true and `-e` false: leaving it in place would
# break every reader, which is the state this step exists to resolve — so
# replace it rather than report it present.
link_shared() {
    local rel="$1" consequence="$2"
    local dest="$TARGET/$rel" src="$MAIN_ROOT/$rel"

    if [ -L "$dest" ] && [ ! -e "$dest" ]; then
        echo "removing dangling $rel symlink -> $(readlink "$dest")" >&2
        rm "$dest"
    fi

    if [ -e "$dest" ] || [ -L "$dest" ]; then
        echo "$rel already present — left alone" >&2
    elif [ -e "$src" ]; then
        mkdir -p "$(dirname "$dest")"
        ln -s "$src" "$dest"
        echo "linked $rel -> $src" >&2
    else
        echo "WARN: no $rel in $MAIN_ROOT — $consequence" >&2
    fi
}

link_shared .env "GH_TOKEN-dependent commands will not work"
link_shared data/cannabis_observer "importer and seed-file tests will skip"

echo "worktree ready: $TARGET" >&2
