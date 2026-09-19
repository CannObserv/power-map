#!/usr/bin/env bash
# pre-ship.sh — power-map's ship gate (#539).
#
# `shipping-work-python-fastapi` Step 1 resolves `scripts/<name>.sh` from the
# repo root BEFORE the skill directory, so this file is the sanctioned tailoring
# point. It runs the same stages as the vendored gate, with one difference: the
# Python suite is invoked the way this repo actually invokes it.
#
# Why this exists. The vendored gate runs:
#
#     uv run pytest $PYTEST_COV_FLAG -x -m "not integration"
#
# That `-m` REPLACES our pyproject `addopts` default of
# `-m 'not integration and not browser'`, so the browser tier is *requested*.
# `tests/optional_groups.py` then refuses, correctly: with Playwright absent the
# tier collects 0 tests and exits green — the vacuous pass #433 exists to stop.
# In a provisioned worktree it is worse than a refusal, because Playwright IS
# installed there: ~200 browser tests wanting a live database, a server and
# Chromium get pulled into the ship gate. The marker is hardcoded upstream and
# there is no env knob.
#
# Why a copy and not a delegating wrapper. The vendored script documents a
# wrapper pattern and it is right for env loading, but it cannot reach the pytest
# stage. The only seam that skips that stage is the per-SHA stamp, and it
# requires a clean working tree — while Step 1 runs BEFORE Step 2 ("ensure a
# clean working tree"). On the run that matters the delegate re-runs pytest with
# its own marker regardless.
#
# A copy drifts. `tests/scripts/test_pre_ship_gate.py` is what stops it drifting
# silently: it reads the vendored gate back and fails if a stage appears there
# and not here, and — the important one — fails the day the vendored pytest line
# stops hardcoding the marker. When that happens, DELETE this file and let Step 1
# resolve the vendored gate again. A local divergence should retire itself (#463).
#
# Usage: bash scripts/pre-ship.sh [--help]
set -euo pipefail

if [[ "${1:-}" == "--help" ]]; then
  cat <<'EOF'
Usage: bash scripts/pre-ship.sh

power-map's pre-ship gate. Runs, in order:

  - Lint (ruff)              uv run ruff check .
  - Format check (ruff)      uv run ruff format --check .
  - Tests (Python)           uv run --group seed pytest --no-cov -x
                             (the marker comes from pyproject addopts:
                             'not integration and not browser')
  - Lint (ESLint)            npm run lint:js          (if package.json has it)
  - Format check (Prettier)  npm run format:js:check  (if package.json has it)
  - Tests (JS)               npm run test:js          (if package.json has it)

Exits non-zero on any failure. Must pass before committing or pushing.

Diverges from the vendored gate only in the Python invocation: that one passes
-m "not integration", which replaces our addopts marker and so requests the
browser tier. See the comment block at the top of this file, and #539.

Exit codes:
  0  All checks passed
  1  Lint or test failure
  2  Tooling/infra failure (uv or node missing, git status failed, mktemp failed)

Skips pytest when HEAD hasn't changed AND the working tree is clean (per-SHA
stamp), the same contract as the vendored gate.
EOF
  exit 0
fi

PROJECT_ROOT=$(git rev-parse --show-toplevel 2>/dev/null || pwd)
cd "$PROJECT_ROOT"

# Pre-flight: warn (never fail) on zombie processes from destroyed worktrees.
AUDIT_SCRIPT="skills/using-git-worktrees/scripts/audit-worktree-zombies.sh"
if [[ -x "$AUDIT_SCRIPT" ]]; then
  if ! "$AUDIT_SCRIPT" --quiet; then
    echo "WARN: worktree zombies detected — see 'bash $AUDIT_SCRIPT'" >&2
  fi
fi

if ! command -v uv >/dev/null 2>&1; then
  echo "ERROR: uv not installed." >&2
  exit 2
fi

STATUS_OUT=""; STATUS_ERR=""; REV_ERR=""
trap 'rm -f "$STATUS_OUT" "$STATUS_ERR" "$REV_ERR"' EXIT

echo "=== Lint (ruff) ==="
uv run ruff check .

echo ""
echo "=== Format check (ruff) ==="
uv run ruff format --check .

echo ""
echo "=== Tests (Python) ==="

# Per-SHA stamp, same contract as the vendored gate: skip the suite when HEAD is
# unchanged and the tree is clean. Degrade to running unconditionally rather than
# poisoning a shared stamp slot when git cannot resolve HEAD.
REV_ERR=$(mktemp) || { echo "ERROR: mktemp failed (REV_ERR)" >&2; exit 2; }
CURRENT_SHA=""
if ! CURRENT_SHA=$(git rev-parse HEAD 2>"$REV_ERR"); then
  echo "WARN: could not resolve HEAD SHA; running pytest unconditionally (no stamp):" >&2
  cat "$REV_ERR" >&2
fi

STATUS_OUT=$(mktemp) || { echo "ERROR: mktemp failed (STATUS_OUT)" >&2; exit 2; }
STATUS_ERR=$(mktemp) || { echo "ERROR: mktemp failed (STATUS_ERR)" >&2; exit 2; }
STATUS_RC=0
git status --porcelain >"$STATUS_OUT" 2>"$STATUS_ERR" || STATUS_RC=$?
if [[ $STATUS_RC -ne 0 ]]; then
  echo "ERROR: git status --porcelain failed (exit $STATUS_RC):" >&2
  cat "$STATUS_ERR" >&2
  exit 2
fi
# grep -v exits 1 when nothing matches its inverse filter (the clean-tree case),
# which pipefail would otherwise propagate into set -e.
WORKING_TREE_DIRTY=$(grep -v '^??' "$STATUS_OUT" | grep -v '^[ M]M.*vendor/' || true)

# The stamp prefix matches the vendored gate's, so the two share a slot rather
# than each re-running a suite the other just passed.
if [[ -n "$CURRENT_SHA" ]]; then
  STAMP_FILE="/tmp/$(basename "$PROJECT_ROOT")-tests-clean-${CURRENT_SHA}"
else
  STAMP_FILE=""
fi

# --no-cov only when pytest-cov is installed: passing it without the plugin is a
# hard usage error. Coverage thresholds belong in CI, not in a pre-push gate.
PYTEST_COV_FLAG=""
if uv run python -c "import pytest_cov" >/dev/null 2>&1; then
  PYTEST_COV_FLAG="--no-cov"
fi

if [[ -n "$STAMP_FILE" && -f "$STAMP_FILE" && -z "$WORKING_TREE_DIRTY" ]]; then
  echo "Test suite already passed for commit ${CURRENT_SHA:0:7} with a clean working tree — skipping."
else
  # No -m: pyproject addopts supplies 'not integration and not browser', and any
  # -m here would replace it wholesale — the #539 defect. --group seed matches
  # the pre-commit `pytest (unit)` hook, which the vendored gate omitted.
  # Exit code 5 = nothing collected, acceptable on an empty suite.
  # $PYTEST_COV_FLAG is intentionally unquoted: empty expansion → no arg.
  uv run --group seed pytest $PYTEST_COV_FLAG -x || { EC=$?; [ $EC -eq 5 ] || exit $EC; }
  if [[ -n "$STAMP_FILE" && -z "$WORKING_TREE_DIRTY" ]]; then
    touch "$STAMP_FILE"
  fi
fi

# --- Optional JS toolchain (auto-detected) -----------------------------------
if [[ -f "package.json" ]]; then
  if ! command -v node >/dev/null 2>&1; then
    echo "ERROR: node is required to probe package.json scripts (no JS gates would run)" >&2
    exit 2
  fi
  if ! node -e 'require("./package.json")' >/dev/null; then
    echo "ERROR: package.json failed to parse" >&2
    exit 2
  fi

  has_script() {
    SCRIPT="$1" node -e 'const s=require("./package.json").scripts; process.exit(s&&s[process.env.SCRIPT]?0:1)'
  }

  if has_script lint:js; then
    echo ""
    echo "=== Lint (ESLint) ==="
    npm run lint:js
  fi

  if has_script format:js:check; then
    echo ""
    echo "=== Format check (Prettier) ==="
    npm run format:js:check
  fi

  if has_script test:js; then
    echo ""
    echo "=== Tests (JS) ==="
    npm run test:js
  fi
fi

echo ""
echo "Pre-ship checks passed."
