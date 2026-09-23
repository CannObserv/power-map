#!/usr/bin/env bash
# SessionStart note (#542): warn when this host's Claude Code layout drifts from
# one system runtime — /usr/local/bin/claude not linking into
# /usr/local/lib/claude/versions, a user path not linking to it, an extension
# update's fresh bundled binary, or a system version over 14 days old.
# Silent when converged. Reports, never repairs: the fix needs sudo.
exec bash "$(dirname "${BASH_SOURCE[0]}")/../../scripts/claude-system.sh" --check
