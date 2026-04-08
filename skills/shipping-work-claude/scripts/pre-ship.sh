#!/usr/bin/env bash
# pre-ship.sh
# Runs lint and tests. Exits non-zero on any failure.
# Detects the git project root automatically; safe to invoke from any directory.
#
# Usage: bash skills/shipping-work-claude/scripts/pre-ship.sh [--help]
set -euo pipefail

if [[ "${1:-}" == "--help" ]]; then
  echo "Usage: bash skills/shipping-work-claude/scripts/pre-ship.sh"
  echo ""
  echo "Runs ruff lint, pytest, ESLint, Prettier check, and vitest."
  echo "Exits non-zero on any failure. Must pass before committing or pushing."
  echo ""
  echo "Exit codes:"
  echo "  0  All checks passed"
  echo "  1  Lint or test failure"
  exit 0
fi

PROJECT_ROOT=$(git rev-parse --show-toplevel 2>/dev/null || pwd)
cd "$PROJECT_ROOT"

echo "=== Lint (ruff) ==="
uv run ruff check .

echo ""
echo "=== Tests (Python) ==="
CURRENT_SHA=$(git rev-parse HEAD 2>/dev/null || echo "unknown")
STAMP_FILE="/tmp/pm-tests-clean-${CURRENT_SHA}"
WORKING_TREE_DIRTY=$(git status --porcelain 2>/dev/null \
  | grep -v '^??' \
  | grep -v '^[ M]M.*vendor/' \
  || true)

if [[ -f "$STAMP_FILE" && -z "$WORKING_TREE_DIRTY" ]]; then
  echo "Test suite already passed for commit ${CURRENT_SHA:0:7} with a clean working tree — skipping."
else
  # Exit code 5 = no tests collected (acceptable on an empty suite)
  uv run pytest --no-cov -x -m "not integration" || { EC=$?; [ $EC -eq 5 ] || exit $EC; }
  if [[ -z "$WORKING_TREE_DIRTY" ]]; then
    touch "$STAMP_FILE"
  fi
fi

if [[ -f "package.json" ]]; then
  echo ""
  echo "=== Lint (ESLint) ==="
  npm run lint:js

  echo ""
  echo "=== Format check (Prettier) ==="
  npm run format:js:check

  echo ""
  echo "=== Tests (JS) ==="
  npm run test:js
fi

echo ""
echo "Pre-ship checks passed."
