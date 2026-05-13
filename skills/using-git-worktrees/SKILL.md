---
name: using-git-worktrees
description: Use when starting feature work that needs isolation from current workspace or before executing implementation plans - creates isolated git worktrees with smart directory selection and safety verification
---

# Using Git Worktrees — power-map

## Overview

Git worktrees create isolated workspaces sharing the same repository, allowing work on multiple branches simultaneously without switching.

**Core principle:** Systematic directory selection + safety verification = reliable isolation.

**Announce at start:** "I'm using the using-git-worktrees skill to set up an isolated workspace."

## Directory Selection Process

Follow this priority order:

### 1. Check Existing Directories

```bash
# Check in priority order
ls -d .worktrees 2>/dev/null     # Preferred (hidden)
ls -d worktrees 2>/dev/null      # Alternative
```

**If found:** Use that directory. If both exist, `.worktrees` wins.

### 2. Check CLAUDE.md

```bash
grep -i "worktree.*director" CLAUDE.md 2>/dev/null
```

**If preference specified:** Use it without asking.

### 3. Ask User

If no directory exists and no CLAUDE.md preference:

```
No worktree directory found. Where should I create worktrees?

1. .worktrees/ (project-local, hidden)
2. ~/.config/superpowers/worktrees/<project-name>/ (global location)

Which would you prefer?
```

## Safety Verification

### For Project-Local Directories (.worktrees or worktrees)

**MUST verify directory is ignored before creating worktree:**

```bash
# Check if directory is ignored (respects local, global, and system gitignore)
git check-ignore -q .worktrees 2>/dev/null || git check-ignore -q worktrees 2>/dev/null
```

**If NOT ignored:**

Per Jesse's rule "Fix broken things immediately":
1. Add appropriate line to .gitignore
2. Commit the change
3. Proceed with worktree creation

**Why critical:** Prevents accidentally committing worktree contents to repository.

### For Global Directory (~/.config/superpowers/worktrees)

No .gitignore verification needed - outside project entirely.

## Creation Steps

### 1. Detect Project Name

```bash
project=$(basename "$(git rev-parse --show-toplevel)")
```

### 2. Create Worktree

```bash
# Set these from context before running:
#   LOCATION   — the resolved worktree parent dir (e.g. .worktrees)
#   BRANCH_NAME — the new branch name (e.g. fix/48-my-feature)

# Determine full path
case $LOCATION in
  .worktrees|worktrees)
    path="$LOCATION/$BRANCH_NAME"
    ;;
  *)
    path="$LOCATION/$project/$BRANCH_NAME"
    ;;
esac

# Create worktree with new branch
git worktree add "$path" -b "$BRANCH_NAME"
```

### 3. Run Project Setup

```bash
# From the new worktree directory
cd "$path"

# Python deps
uv sync

# JS deps — required for the vitest pre-commit hook.
# `npm ci` (not `install`) honours package-lock.json exactly and won't
# mutate the lock when it sees minor format drift.
npm ci

# .env carries GH_TOKEN + TEST_DATABASE_URL; without it integration tests
# silently skip. Copy explicitly and warn loudly on miss.
# git rev-parse --git-common-dir points to the main .git regardless of worktree.
parent_env="$(git rev-parse --git-common-dir | xargs dirname)/.env"
if [ -f "$parent_env" ]; then
    cp "$parent_env" .env
    echo ".env copied from $parent_env"
else
    echo "WARNING: $parent_env not found — integration tests will skip and gh CLI will fail."
    echo "Create .env with TEST_DATABASE_URL and GH_TOKEN (see docs/COMMANDS.md)."
fi
```

### 4. Reload Dev Server on Port 8001

Kill any existing dev server on 8001, then start fresh with `--reload` from the new worktree:

```bash
fuser -k 8001/tcp 2>/dev/null; sleep 1
# uv has a proper dotenv parser; pass both files via --env-file (later
# wins on conflict, matching the cat-and-export ordering this replaces).
nohup uv run \
    --env-file /etc/power-map/.env \
    --env-file .env \
    uvicorn src.api.main:app --host 0.0.0.0 --port 8001 --reload \
    > /tmp/power-map-dev.log 2>&1 &
sleep 2 && curl -s -o /dev/null -w "Dev server: %{http_code}\n" http://localhost:8001/admin/
```

Dev server accessible at `https://power-map.exe.xyz:8001/`.

### 5. Verify Clean Baseline

```bash
# Full suite (unit + integration). If TEST_DATABASE_URL is unset, all
# integration tests will skip — the conftest skip reason names the misconfig.
uv run \
    --env-file /etc/power-map/.env \
    --env-file .env \
    pytest --no-cov -q

# Surface integration-test status explicitly so a silent skip-everything
# can't be mistaken for a clean pass. Read TEST_DATABASE_URL out of .env
# directly since uv loads the env only inside its child process.
if ! grep -q '^TEST_DATABASE_URL=' .env 2>/dev/null; then
    echo "WARNING: TEST_DATABASE_URL not set in .env — integration tests were skipped."
fi
```

**If tests fail:** Report failures, ask whether to proceed or investigate.

**If integration tests skipped:** Report the warning before claiming ready.

**If tests pass:** Report ready.

### 6. Report Location

```
Worktree ready at <full-path>
Tests passing (<N> tests, 0 failures)
Dev server running at https://power-map.exe.xyz:8001/
Ready to implement <feature-name>
```

## Quick Reference

| Situation | Action |
|-----------|--------|
| `.worktrees/` exists | Use it (verify ignored) |
| `worktrees/` exists | Use it (verify ignored) |
| Both exist | Use `.worktrees/` |
| Neither exists | Check CLAUDE.md → Ask user |
| Directory not ignored | Add to .gitignore + commit |
| Tests fail during baseline | Report failures + ask |

## Common Mistakes

### Skipping ignore verification

- **Problem:** Worktree contents get tracked, pollute git status
- **Fix:** Always use `git check-ignore` before creating project-local worktree

### Running dev server from main checkout

- **Problem:** Mixes in-progress code with the production branch
- **Fix:** Always run dev server from the worktree directory

### Forgetting to reload the dev server

- **Problem:** Dev server still pointing at previous worktree
- **Fix:** Always kill+restart on port 8001 after creating a new worktree

## Teardown

When a worktree is no longer needed (feature shipped, work abandoned), always ask the user to confirm before destroying anything:

```
Ready to tear down worktree `.worktrees/<branch>` and delete branch `<branch>`.
This will:
  - Kill the dev server on port 8001 (if running from this worktree)
  - Remove the worktree directory
  - Delete the local branch

Confirm teardown? (y/n)
```

**Wait for explicit confirmation.** If the user says no, leave everything intact.

Once confirmed:

```bash
# Kill dev server if running from this worktree
fuser -k 8001/tcp 2>/dev/null || true

# Remove worktree (run from main checkout)
git worktree remove --force .worktrees/<branch> 2>/dev/null || true

# Delete the branch
git branch -d <branch> 2>/dev/null || true

# Prune stale refs
git worktree prune
```

## Integration

**Called by:**
- **brainstorming** (Phase 4) - REQUIRED when design is approved and implementation follows
- **subagent-driven-development** - REQUIRED before executing any tasks
- **executing-plans** - REQUIRED before executing any tasks
- Any skill needing isolated workspace

**Pairs with:**
- **finishing-a-development-branch** - REQUIRED for cleanup after work complete
