#!/usr/bin/env bash
# Make the host's native Claude Code install the one every client runs (#542).
#
# The canonical install is the native launcher, ~/.local/bin/claude: a symlink
# into ~/.local/share/claude/versions/<v> that `claude update` and the
# auto-updater repoint. /usr/local/bin/claude already resolves to it. The VS Code
# extension ships its own binary instead, under
# <extensions>/anthropic.claude-code-<v>-<platform>/resources/native-binary/claude,
# so this host ran two versions and PATH's went six months stale unnoticed.
# This script replaces each bundled binary with a symlink to the launcher, not
# to the version it resolves to today, so the extension follows every update.
#
# An extension update unpacks a fresh directory with a fresh bundled binary.
# The SessionStart hook runs --check to report that; it never repairs.
#
# Usage:
#   bash scripts/claude-link-canonical.sh            # dry run: what would change
#   bash scripts/claude-link-canonical.sh --execute  # relink
#   bash scripts/claude-link-canonical.sh --check    # hook mode: silent unless drifted
#
# --execute exits 1 if it skipped a downgrade (an extension newer than the
# canonical install; run `claude update` first), 2 on a usage or setup error.
#
# Overrides (tests): CLAUDE_CANONICAL, CLAUDE_EXTENSION_ROOTS (colon-separated).
set -uo pipefail

CANONICAL="${CLAUDE_CANONICAL:-$HOME/.local/bin/claude}"
ROOTS="${CLAUDE_EXTENSION_ROOTS:-$HOME/.vscode-server/extensions:$HOME/.vscode/extensions:$HOME/.cursor-server/extensions}"
FIX="bash scripts/claude-link-canonical.sh --execute"

mode=dry
for arg in "$@"; do
    case "$arg" in
        --execute | --check)
            if [ "$mode" != dry ]; then
                echo "claude-link-canonical: --check and --execute are exclusive" >&2
                exit 2
            fi
            mode="${arg#--}"
            ;;
        -h | --help)
            sed -n '2,/^set -uo/p' "$0" | sed '$d; s/^# \{0,1\}//'
            exit 0
            ;;
        *)
            echo "claude-link-canonical: unknown argument: $arg" >&2
            exit 2
            ;;
    esac
done

# Every bundled binary under the extension roots, one per line.
bundled_binaries() {
    local root dir
    local IFS=:
    for root in $ROOTS; do
        [ -d "$root" ] || continue
        for dir in "$root"/anthropic.claude-code-*/resources/native-binary; do
            [ -e "$dir/claude" ] || [ -L "$dir/claude" ] || continue
            echo "$dir/claude"
        done
    done
}

# The extension version a bundled binary belongs to, from its directory name.
extension_version() {
    local ext="${1%/resources/native-binary/claude}"
    basename "$ext" | grep -oE '[0-9]+(\.[0-9]+)+' | head -n1
}

is_linked() {
    [ -L "$1" ] && [ "$(readlink "$1")" = "$CANONICAL" ]
}

# True when version $1 is strictly older than version $2.
older_than() {
    [ "$1" != "$2" ] && [ "$(printf '%s\n%s\n' "$1" "$2" | sort -V | head -n1)" = "$1" ]
}

mapfile -t BINARIES < <(bundled_binaries)

if [ "$mode" = check ]; then
    # Report-only and silent when there is nothing to converge on.
    [ -x "$CANONICAL" ] || exit 0
    drifted=()
    for bin in "${BINARIES[@]}"; do
        is_linked "$bin" || drifted+=("$(extension_version "$bin")")
    done
    if [ "${#drifted[@]}" -gt 0 ]; then
        echo "⚠️  Claude Code: VS Code extension ${drifted[*]} runs its own bundled" \
            "binary, not the canonical $CANONICAL (#542). Link it: \`$FIX\`"
    fi
    exit 0
fi

if [ ! -x "$CANONICAL" ]; then
    echo "claude-link-canonical: no canonical launcher at $CANONICAL." \
        "Install it first: \`claude install latest\`, then re-run." >&2
    exit 2
fi
resolved="$(readlink -f "$CANONICAL")"
IFS=: read -r -a root_list <<<"$ROOTS"
for root in "${root_list[@]}"; do
    case "$resolved" in
        "$root"/*)
            echo "claude-link-canonical: $CANONICAL resolves into an extension" \
                "($resolved); linking the extension to it would make a loop." >&2
            exit 2
            ;;
    esac
done
canonical_version="$("$CANONICAL" --version 2>/dev/null | awk 'NR==1 {print $1}')"
echo "canonical: $CANONICAL -> $resolved (${canonical_version:-unknown version})"

if [ "${#BINARIES[@]}" -eq 0 ]; then
    echo "no VS Code extension binaries found under $ROOTS"
    exit 0
fi

rc=0
for bin in "${BINARIES[@]}"; do
    ext_version="$(extension_version "$bin")"
    if is_linked "$bin"; then
        echo "linked       $bin"
        continue
    fi
    if [ -n "$canonical_version" ] && [ -n "$ext_version" ] &&
        older_than "$canonical_version" "$ext_version"; then
        echo "skipped      $bin — extension $ext_version is newer than canonical" \
            "$canonical_version; run \`claude update\` first, then re-run"
        rc=1
        continue
    fi
    if [ "$mode" = dry ]; then
        echo "would link   $bin -> $CANONICAL"
        continue
    fi
    # Atomic: build the link beside the target, then rename over it. A running
    # session keeps the inode it already has open.
    tmp="$bin.canonical.$$"
    if ln -s "$CANONICAL" "$tmp" && mv -Tf "$tmp" "$bin"; then
        echo "relinked     $bin -> $CANONICAL"
    else
        rm -f "$tmp"
        echo "failed       $bin" >&2
        rc=2
    fi
done

[ "$mode" = dry ] && echo "dry run — nothing changed. Apply with: $FIX"
exit "$rc"
