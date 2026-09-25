#!/usr/bin/env bash
# SessionStart (#570): make `gh` work in every agent Bash call without a
# hand-typed export, and flag a `gh` too old for GitHub's API.
#
# 1. Appends `export GH_TOKEN=…` — the repo .env's value, parsed as data by
#    scripts/load-env.sh — to $CLAUDE_ENV_FILE, which the harness sources before
#    each Bash command. Only GH_TOKEN: the cross-repo GH_TOKEN_* stay per-call,
#    passed as GH_TOKEN for the one command that needs them.
# 2. Warns when `gh` predates v2.78.0. Older builds (Ubuntu's apt 2.45.0) request
#    the removed Projects (classic) `projectCards` field, so `gh issue view`,
#    `gh pr view` and `gh pr edit` exit 1 (cli/cli#11992, fixed by #11514).
#
# Silent when both are fine. Never prints the token. Always exits 0: a session
# must not fail to start over GitHub tooling.
#
# The reference for `gh` on this host (docs/COMMANDS.md § Environment points
# here; its token budget has no room for it):
#
#   - Cross-repo tokens: pass a GH_TOKEN_<REPO> for one call, anchored so it
#     cannot match the other GH_TOKEN_* lines:
#       GH_TOKEN=$(grep -E '^GH_TOKEN_SKILLS=' .env | cut -d= -f2-) gh …
#   - ~/.config/gh/hosts.yml holds NO login, by design. A stored second copy of
#     the PAT went stale unnoticed (2026-09), so every forgotten export surfaced
#     as `HTTP 401: Bad credentials`. Without it, a missing token reads as "not
#     logged in". Do not `gh auth login` to paper over a missing export.
#   - Upgrading: `gh` comes from GitHub's apt repository
#     (/etc/apt/sources.list.d/github-cli.list, signed by
#     /etc/apt/keyrings/githubcli-archive-keyring.gpg; steps and key
#     fingerprints in cli/cli docs/install_linux.md), so
#     `sudo apt update && sudo apt install gh` keeps it current. Ubuntu's own
#     archive stays at 2.45.0.
#   - REST fallback on a host still below the floor:
#       gh api repos/<owner>/<repo>/issues/<n>           (and …/comments)
#       gh api -X PATCH repos/<owner>/<repo>/pulls/<n> -F body=@<file>
set -uo pipefail

GH_MIN_VERSION="2.78.0"
root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
env_file="${POWER_MAP_PROJECT_ENV_FILE:-$root/.env}"
manual="export GH_TOKEN=\$(grep -E '^GH_TOKEN=' .env | cut -d= -f2-)"

# shellcheck disable=SC1091  # load-env.sh is linted as its own input
source "$root/scripts/load-env.sh" --functions-only

export_token() {
    local token
    # Already set by whoever started the harness: every Bash call inherits it.
    [ -n "${GH_TOKEN:-}" ] && return 0
    if ! token="$(env_file_value "$env_file" GH_TOKEN)" || [ -z "$token" ]; then
        echo "ℹ️  No GH_TOKEN in $env_file — \`gh\` will report \"not logged in\". See docs/COMMANDS.md § Environment."
        return 0
    fi
    if [ -z "${CLAUDE_ENV_FILE:-}" ]; then
        # anthropics/claude-code#15840 / #11649: sometimes empty on SessionStart.
        echo "ℹ️  CLAUDE_ENV_FILE is unset, so GH_TOKEN was not exported for this session. Prefix \`gh\` calls with: $manual"
        return 0
    fi
    # %q: the file is *sourced* before every Bash call, so the value is quoted
    # as shell input, never interpolated.
    printf 'export GH_TOKEN=%q\n' "$token" >>"$CLAUDE_ENV_FILE"
}

check_gh_version() {
    local line version oldest
    if ! command -v gh >/dev/null 2>&1; then
        echo "⚠️  \`gh\` is not installed. Install ≥ v$GH_MIN_VERSION from GitHub's apt repository: docs/COMMANDS.md § Environment."
        return 0
    fi
    line="$(gh --version 2>/dev/null | head -n1)"
    version="${line#gh version }"
    version="${version%% *}"
    case $version in [0-9]*.[0-9]*) ;; *) return 0 ;; esac  # unparseable: say nothing
    oldest="$(printf '%s\n%s\n' "$version" "$GH_MIN_VERSION" | sort -V | head -n1)"
    if [ "$oldest" != "$GH_MIN_VERSION" ]; then
        echo "⚠️  \`gh\` $version predates v$GH_MIN_VERSION: \`gh issue view\` / \`gh pr view\` / \`gh pr edit\` fail on the Projects (classic) deprecation." \
            "Use \`gh api repos/<owner>/<repo>/issues/<n>\` (and \`-X PATCH …/pulls/<n> -F body=@file\`) until it is upgraded from GitHub's apt repository: docs/COMMANDS.md § Environment."
    fi
}

export_token
check_gh_version
exit 0
