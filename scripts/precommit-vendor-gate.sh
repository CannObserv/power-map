#!/usr/bin/env bash
# Run a pre-commit stage's vendored subset on a vendor-only commit (#751).
#
# Usage — as a pre-commit hook's entry:
#   bash scripts/precommit-vendor-gate.sh <stage> [<subset cmd>...] ::: <full cmd>...
#
# Why. The SessionStart hook (.claude/hooks/skills-submodule-update.sh) bumps
# skills-vendor/ once a day and commits through this repo's pre-commit, inside
# a 120 s harness timeout. The unit tier alone takes ~550 s on this host, so
# the harness killed every commit from 2026-10-24 on and the bumps were made by
# hand. A timeout above 300 s is no fix: upstream's MARKER_MAX_AGE would read a
# live run as dead. So a commit staging ONLY registered skills-vendor/
# submodule pointers and .skills/doctor.sh runs <subset cmd> — the tests that
# read the vendored tree — and every other commit runs <full cmd>. An empty
# subset skips the stage.
#
# The staged set comes from `git diff --cached --raw` with no diff filter, and
# each pointer must stay a gitlink (mode 160000) while nothing is deleted.
# pre-commit's own file list leaves deletions out, so a files:/exclude: rule
# would have skipped the tier on a deletion-only commit too. Under the
# hook's `git commit -- <paths>`, git hands this the temporary index, so a
# file the operator had staged does not count.
#
# Fails closed: nothing staged, a git error, or any other path runs <full cmd>.
#
# Exit codes: the command it runs; 0 for a skipped stage; 2 usage error.

set -uo pipefail

usage() {
    cat <<'EOF'
usage: bash scripts/precommit-vendor-gate.sh <stage> [<subset cmd>...] ::: <full cmd>...

  A commit staging only registered skills-vendor/ submodule pointers and
  .skills/doctor.sh runs <subset cmd> (none: the stage is skipped); any other
  commit runs <full cmd>.
EOF
}

if [ "${1:-}" = "--help" ]; then
    usage
    exit 0
fi
if [ $# -lt 1 ]; then
    usage >&2
    exit 2
fi
STAGE="$1"
shift

SUBSET=()
FULL=()
SEEN=0
for arg in "$@"; do
    if [ "$SEEN" = 0 ] && [ "$arg" = ":::" ]; then
        SEEN=1
    elif [ "$SEEN" = 0 ]; then
        SUBSET+=("$arg")
    else
        FULL+=("$arg")
    fi
done
if [ "$SEEN" = 0 ] || [ "${#FULL[@]}" -eq 0 ]; then
    usage >&2
    exit 2
fi

# The skills-vendor/ submodule paths .gitmodules registers, read from the index
# being committed rather than the working tree.
registered_vendor_paths() {
    git config --blob :.gitmodules --get-regexp '^submodule\..*\.path$' 2>/dev/null \
        | sed 's/^[^ ]* //' | grep '^skills-vendor/' || true
}

vendor_only() {
    local staged registered line meta path
    local -a fields
    # --raw: the paths alone cannot tell a pointer bump from a gitlink deleted
    # (`git rm --cached`) or replaced by a file. --no-renames: a rename lists
    # only its destination, so a file moved onto .skills/doctor.sh would hide
    # the deletion it carries. --ignore-submodules=none: a diff.ignoreSubmodules
    # setting would hide the pointer itself.
    staged="$(git diff --cached --raw --no-renames --ignore-submodules=none 2>/dev/null)" \
        || return 1
    [ -n "$staged" ] || return 1
    registered="$(registered_vendor_paths)"
    while IFS= read -r line; do
        # :<old mode> <new mode> <old sha> <new sha> <status>\t<path>
        meta="${line%%$'\t'*}"
        path="${line#*$'\t'}"
        read -ra fields <<<"$meta"
        [ "${fields[4]:-}" != "D" ] || return 1
        [ "$path" = ".skills/doctor.sh" ] && continue
        [ "${fields[1]:-}" = "160000" ] || return 1
        grep -qxF -- "$path" <<<"$registered" || return 1
    done <<<"$staged"
    return 0
}

if vendor_only; then
    if [ "${#SUBSET[@]}" -eq 0 ]; then
        echo "$STAGE: vendor-only commit (#751) — skipped"
        exit 0
    fi
    echo "$STAGE: vendor-only commit (#751) — running the vendored subset"
    exec "${SUBSET[@]}"
fi
exec "${FULL[@]}"
