#!/usr/bin/env bash
# One system-wide Claude Code runtime for this host (#542).
#
# /usr/local/bin/claude is the runtime every client uses. It is a root-owned
# symlink into /usr/local/lib/claude/versions/<v>. The user-space paths link
# to it: the native launcher ~/.local/bin/claude, and each VS Code extension's
# bundled resources/native-binary/claude. Before #542 this ran the other way —
# the system path pointed into user space — and PATH sat at 2.1.58 for six
# months while the extension ran 2.1.280.
#
# Updates are manual and need root, which is deliberate. The native installer
# only moves a ~/.local/bin/claude launcher that already points into its own
# versions/ directory; a regular file or a symlink to anywhere else is left
# alone. So the user-level auto-updater cannot move this layout. --execute
# disables it (DISABLE_AUTOUPDATER) rather than let it download versions
# nothing runs. The installer is run as root with a throwaway HOME and
# XDG_DATA_HOME=/usr/local/lib, which is where it puts versions/; nothing else
# it writes survives. It also runs `npm uninstall --global
# @anthropic-ai/claude-code`, which as root removes a global npm copy if one
# exists.
#
# Usage:
#   bash scripts/claude-system.sh                         # dry run: report + plan
#   sudo bash scripts/claude-system.sh --execute [--version latest|stable|X.Y.Z]
#   bash scripts/claude-system.sh --check                 # SessionStart hook: silent unless drifted/stale
#
# --execute installs the version (default latest), repoints the system bin,
# links the user paths, and keeps the current and previous versions only.
# Exit 1: it refused a downgrade or skipped an extension newer than the system
# version. Exit 2: usage or setup error.
#
# Overrides (tests): CLAUDE_SYSTEM_BIN, CLAUDE_SYSTEM_LIB, CLAUDE_USER_HOME,
# CLAUDE_EXTENSION_ROOTS (colon-separated), CLAUDE_STALE_DAYS,
# CLAUDE_SKIP_ROOT_CHECK.
set -uo pipefail

SYSTEM_BIN="${CLAUDE_SYSTEM_BIN:-/usr/local/bin/claude}"
SYSTEM_LIB="${CLAUDE_SYSTEM_LIB:-/usr/local/lib}"
VERSIONS="$SYSTEM_LIB/claude/versions"
# Touched by every successful --execute. The installer leaves an existing
# version's file untouched, so its mtime cannot say when anyone last checked.
STAMP="$SYSTEM_LIB/claude/last-update"
STALE_DAYS="${CLAUDE_STALE_DAYS:-14}"
FIX="sudo bash scripts/claude-system.sh --execute"

# Whose user-space paths to link: the explicit override, else the sudo caller,
# else whoever runs this.
if [ -n "${CLAUDE_USER_HOME:-}" ]; then
    USER_HOME="$CLAUDE_USER_HOME"
elif [ -n "${SUDO_USER:-}" ] && [ "${SUDO_USER}" != root ]; then
    USER_HOME="$(getent passwd "$SUDO_USER" | cut -d: -f6)"
else
    USER_HOME="$HOME"
fi
USER_LAUNCHER="$USER_HOME/.local/bin/claude"
ROOTS="${CLAUDE_EXTENSION_ROOTS:-$USER_HOME/.vscode-server/extensions:$USER_HOME/.vscode/extensions:$USER_HOME/.cursor-server/extensions}"
OWNER="$(stat -c '%U:%G' "$USER_HOME" 2>/dev/null || true)"

mode=dry
target=latest
while [ $# -gt 0 ]; do
    case "$1" in
        --execute | --check)
            if [ "$mode" != dry ]; then
                echo "claude-system: --check and --execute are exclusive" >&2
                exit 2
            fi
            mode="${1#--}"
            ;;
        --version)
            [ $# -ge 2 ] || {
                echo "claude-system: --version needs a value" >&2
                exit 2
            }
            target="$2"
            shift
            ;;
        -h | --help)
            sed -n '2,/^set -uo/p' "$0" | sed '$d; s/^# \{0,1\}//'
            exit 0
            ;;
        *)
            echo "claude-system: unknown argument: $1" >&2
            exit 2
            ;;
    esac
    shift
done

# Every bundled extension binary, one per line.
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

# The version the system bin runs, when it is a link to an executable in
# VERSIONS; else empty. A dangling link is not a version: it neither blocks an
# install as a "downgrade" nor has an age to measure.
system_version() {
    local dest
    dest="$(readlink "$SYSTEM_BIN" 2>/dev/null)" || return 0
    case "$dest" in "$VERSIONS"/*) [ -x "$dest" ] && basename "$dest" ;; esac
    return 0
}

links_to_system() {
    [ -L "$1" ] && [ "$(readlink "$1")" = "$SYSTEM_BIN" ]
}

# True when version $1 is strictly older than version $2.
older_than() {
    [ "$1" != "$2" ] && [ "$(printf '%s\n%s\n' "$1" "$2" | sort -V | head -n1)" = "$1" ]
}

# Atomic symlink replace: build beside the target, rename over it. A running
# process keeps the inode it already has open.
relink() {
    local link="$1" dest="$2" tmp="$1.claude-system.$$"
    if ! { ln -s "$dest" "$tmp" && mv -Tf "$tmp" "$link"; }; then
        rm -f "$tmp"
        return 1
    fi
    [ -z "$OWNER" ] || [ "$(id -u)" -ne 0 ] || [ "$link" = "$SYSTEM_BIN" ] ||
        chown -h "$OWNER" "$link"
}

# mkdir -p under the user's home, handing whatever it creates to the user: as
# root it would otherwise leave a root-owned ~/.claude, which breaks every
# session that user starts.
ensure_user_dir() {
    local dir="$1" top="$1"
    [ -d "$dir" ] && return 0
    while [ ! -d "$(dirname "$top")" ]; do top="$(dirname "$top")"; done
    mkdir -p "$dir" || return 1
    [ -z "$OWNER" ] || [ "$(id -u)" -ne 0 ] || chown -R "$OWNER" "$top"
}

mapfile -t BINARIES < <(bundled_binaries)

# --- --check: the SessionStart hook. Reads links and mtimes; runs nothing. ------
# Silent on any host that never adopted this layout: the hook ships to every
# clone, and a Homebrew or npm /usr/local/bin/claude is not drift. (It also
# keeps the GNU-only stat below off macOS.)
if [ "$mode" = check ]; then
    [ -d "$VERSIONS" ] || exit 0
    issues=()
    current="$(system_version)"
    dest="$(readlink "$SYSTEM_BIN" 2>/dev/null || true)"
    if [ -z "$current" ] && [[ "$dest" == "$VERSIONS"/* ]]; then
        issues+=("$SYSTEM_BIN links to $dest, which is missing — every client is broken")
    elif [ -z "$current" ]; then
        issues+=("$SYSTEM_BIN is not the system runtime (it should link into $VERSIONS)")
    else
        since="$STAMP"
        [ -e "$since" ] || since="$VERSIONS/$current"
        age=$((($(date +%s) - $(stat -c %Y "$since")) / 86400))
        [ "$age" -le "$STALE_DAYS" ] ||
            issues+=("system Claude Code $current was last updated $age days ago")
    fi
    if { [ -e "$USER_LAUNCHER" ] || [ -L "$USER_LAUNCHER" ]; } && ! links_to_system "$USER_LAUNCHER"; then
        issues+=("$USER_LAUNCHER does not link to $SYSTEM_BIN")
    fi
    drifted=()
    for bin in "${BINARIES[@]}"; do
        links_to_system "$bin" || drifted+=("$(extension_version "$bin")")
    done
    [ "${#drifted[@]}" -eq 0 ] ||
        issues+=("VS Code extension ${drifted[*]} runs its own bundled binary")
    if [ "${#issues[@]}" -gt 0 ]; then
        joined="$(printf '%s; ' "${issues[@]}")"
        echo "⚠️  Claude Code (#542): ${joined%; }. Fix: \`$FIX\`"
    fi
    exit 0
fi

# --- dry run and --execute ------------------------------------------------------
if [ "$mode" = execute ] && [ -z "${CLAUDE_SKIP_ROOT_CHECK:-}" ] && [ "$(id -u)" -ne 0 ]; then
    echo "claude-system: --execute writes $SYSTEM_BIN and $VERSIONS; run it with sudo: $FIX" >&2
    exit 2
fi

current="$(system_version)"
echo "system: $SYSTEM_BIN -> $(readlink "$SYSTEM_BIN" 2>/dev/null || echo '(absent)') (${current:-not the system runtime})"
echo "user:   $USER_LAUNCHER, ${#BINARIES[@]} extension binary(ies) under $USER_HOME"

# Something to run the installer with: the runtime itself, else any user copy.
installer=""
for candidate in "$SYSTEM_BIN" "$USER_LAUNCHER" "${BINARIES[@]}"; do
    resolved="$(readlink -f "$candidate" 2>/dev/null)" || continue
    if [ -f "$resolved" ] && [ -x "$resolved" ]; then
        installer="$resolved"
        break
    fi
done

if [ "$mode" = dry ]; then
    echo "would install $target into $VERSIONS (installer: ${installer:-none found})"
    echo "would link    $SYSTEM_BIN -> $VERSIONS/<version>"
    links_to_system "$USER_LAUNCHER" || echo "would link    $USER_LAUNCHER -> $SYSTEM_BIN"
    for bin in "${BINARIES[@]}"; do
        links_to_system "$bin" || echo "would link    $bin -> $SYSTEM_BIN"
    done
    echo "dry run — nothing changed. Apply with: $FIX"
    exit 0
fi

if [ -z "$installer" ]; then
    echo "claude-system: no Claude Code binary to install from (looked at $SYSTEM_BIN," \
        "$USER_LAUNCHER and the extension binaries)" >&2
    exit 2
fi

# Normally the root-owned runtime itself. The bootstrap, or a broken system
# bin, falls back to a user copy — run as root, so say whose file that is.
installer_owner="$(stat -c %U "$installer" 2>/dev/null || echo unknown)"
[ "$installer_owner" = root ] ||
    echo "note: running the installer from $installer, owned by $installer_owner, not root"

scratch="$(mktemp -d)"
trap 'rm -rf "$scratch"' EXIT
mkdir -p "$VERSIONS"
echo "install $target with $installer"
# sudo has already reset the environment; only HOME and XDG_DATA_HOME are ours.
if ! HOME="$scratch" XDG_DATA_HOME="$SYSTEM_LIB" \
    "$installer" install "$target" >"$scratch/install.log" 2>&1; then
    cat "$scratch/install.log" >&2
    echo "claude-system: the installer failed; nothing was relinked" >&2
    exit 2
fi
# A fresh HOME has no launcher, so the installer creates one naming what it installed.
installed="$(readlink "$scratch/.local/bin/claude" 2>/dev/null)"
case "$installed" in
    "$VERSIONS"/*) [ -x "$installed" ] || installed="" ;;
    *) installed="" ;;
esac
if [ -z "$installed" ]; then
    cat "$scratch/install.log" >&2
    echo "claude-system: the installer reported success but left no version in $VERSIONS" >&2
    exit 2
fi
new="$(basename "$installed")"

rc=0
if [ -n "$current" ] && older_than "$new" "$current"; then
    echo "refused      $new is older than the current system version $current;" \
        "the system bin stays at $current"
    exit 1
elif [ "$new" = "$current" ]; then
    echo "already      $SYSTEM_BIN -> $installed"
else
    relink "$SYSTEM_BIN" "$installed" || exit 2
    echo "linked       $SYSTEM_BIN -> $installed (was ${current:-not the system runtime})"
fi

if links_to_system "$USER_LAUNCHER"; then
    echo "already      $USER_LAUNCHER"
else
    ensure_user_dir "$(dirname "$USER_LAUNCHER")" || exit 2
    relink "$USER_LAUNCHER" "$SYSTEM_BIN" || exit 2
    echo "linked       $USER_LAUNCHER -> $SYSTEM_BIN"
fi

for bin in "${BINARIES[@]}"; do
    ext_version="$(extension_version "$bin")"
    if links_to_system "$bin"; then
        echo "already      $bin"
    elif [ -n "$ext_version" ] && older_than "$new" "$ext_version"; then
        echo "skipped      $bin — extension $ext_version is newer than the system" \
            "version $new; re-run with a newer --version"
        rc=1
    else
        relink "$bin" "$SYSTEM_BIN" || exit 2
        echo "linked       $bin -> $SYSTEM_BIN"
    fi
done

# The user-level auto-updater can no longer move anything; stop its downloads.
settings="$USER_HOME/.claude/settings.json"
ensure_user_dir "$(dirname "$settings")" || exit 2
python3 - "$settings" <<'PY' || exit 2
import json, sys
path = sys.argv[1]
try:
    with open(path) as fh:
        data = json.load(fh)
except FileNotFoundError:
    data = {}
env = data.setdefault("env", {})
if env.get("DISABLE_AUTOUPDATER") != "1":
    env["DISABLE_AUTOUPDATER"] = "1"
    with open(path, "w") as fh:
        json.dump(data, fh, indent=2)
        fh.write("\n")
    print(f"set          DISABLE_AUTOUPDATER=1 in {path}")
else:
    print(f"already      DISABLE_AUTOUPDATER=1 in {path}")
PY
[ -z "$OWNER" ] || [ "$(id -u)" -ne 0 ] || chown "$OWNER" "$settings"

# Keep the running version and the one before it: rollback is one relink.
mapfile -t all < <(find "$VERSIONS" -mindepth 1 -maxdepth 1 -printf '%f\n' | sort -V)
for ((i = 0; i < ${#all[@]} - 2; i++)); do
    [ "${all[$i]}" = "$new" ] && continue
    rm -f "${VERSIONS:?}/${all[$i]}"
    echo "pruned       $VERSIONS/${all[$i]}"
done

touch "$STAMP"
exit "$rc"
