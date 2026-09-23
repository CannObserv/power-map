#!/usr/bin/env bash
# SessionStart note (#542): warn when a VS Code extension runs its own bundled
# Claude Code binary instead of the host's canonical native install. Silent when
# linked. Reports, never repairs — the fix is the command it prints.
exec bash "$(dirname "${BASH_SOURCE[0]}")/../../scripts/claude-link-canonical.sh" --check
