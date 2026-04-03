---
name: shipping-work-claude
description: "Finalizes work by ensuring everything is committed, pushed to the remote, and reflected on GitHub: closes issues, posts summary comments, and presents a completion table. Use when the user says 'ship it', 'push GH', 'close GH', or 'wrap up'."
compatibility: Designed for Claude. Requires git and gh CLI. Python project using uv, ruff, pytest, FastAPI.
metadata:
  author: gregoryfoster
  version: "1.2"
  triggers: ship it, push GH, close GH, wrap up
  overrides: shipping-work-claude
  override-reason: "Concrete test/lint commands (uv run pytest --no-cov, uv run ruff check); project commit convention (#n [type]: desc); Iron Law + rationalization-prevention table; HARD-GATE on partial issue closure; worktree teardown + dev server shutdown"
---

# Shipping Work — power-map

Finalizes work: lint, tests, clean commit, push, GitHub issue comments and closure.

## The Iron Law

```
NO PUSH WITHOUT PASSING TESTS — VERIFIED IN THIS SESSION
NO ISSUE CLOSURE WITHOUT FULL IMPLEMENTATION — VERIFIED AGAINST ORIGINAL REQUIREMENTS
```

## Rationalization prevention

| Thought | Reality |
|---|---|
| "Tests passed earlier in this session" | Run them again. State can change. Require fresh output. |
| "It's basically done, just needs minor cleanup" | Incomplete = not done. Finish or explicitly descope before closing. |
| "The issue will track follow-up work" | Only close if the core requirement is fully met. Open a new issue for follow-up. |
| "gh push is failing, I'll skip it" | Resolve the error. Do not mark as shipped without a successful push. |
| "User is in a hurry" | A bad ship is slower than a good one. Run the checklist. |

## Scope detection

Determine which GitHub issue(s) to close (priority order):
1. **Explicit scope** — user specifies issue number(s) (e.g., `wrap up #19 #20`)
2. **Conversation context** — issues referenced in recent commit messages or discussion
3. **Ask** — if ambiguous, confirm before closing anything

## Procedure

### Step 1 — Lint and test

```bash
bash skills/shipping-work-claude/scripts/pre-ship.sh
```

```
NO CONTINUATION IF TESTS FAIL
```

If tests fail: stop, report the failure, fix it before proceeding.

**Integration tests are always excluded** from `pre-ship.sh`. Run them explicitly with:
```bash
uv run pytest -m integration
```

### Step 2 — Ensure a clean working tree

```bash
bash skills/shipping-work-claude/scripts/check-status.sh
```

If uncommitted changes exist, commit them using this project's convention:
```
#<number> [type]: <description>       # with GH issue
[type]: <description>                 # without GH issue
```
Multiple issues: `#19, #20 [type]: <description>`
Types: `feat`, `fix`, `refactor`, `docs`, `test`, `chore`

### Step 3 — Ensure on main

If on a feature branch, merge to `main` first. Then continue.

**If working in a worktree:** You must merge from the main checkout, not the worktree.

```bash
# Note the current branch name before leaving the worktree
BRANCH=$(git branch --show-current)

# Switch to main checkout and merge
cd /home/exedev/power-map
git merge "$BRANCH"
```

### Step 4 — Push

```bash
bash skills/shipping-work-claude/scripts/push.sh
```

### Step 5 — Comment on GitHub issues

For each issue in scope:

```bash
bash skills/shipping-work-claude/scripts/comment-issue.sh <number> "<summary>"
```

Comment must include:
- What was implemented (2–4 bullets)
- Key commit SHAs or commit range
- Any follow-up items or known limitations

### Step 6 — Close GitHub issues

<HARD-GATE>
Before closing any issue, verify the original issue requirements against what was implemented:
1. Re-read the issue body
2. Confirm each stated requirement is addressed in commits
3. If any requirement is missing: do NOT close — ask the user whether to descope or continue
</HARD-GATE>

```bash
bash skills/shipping-work-claude/scripts/close-issue.sh <number>
```

### Step 7 — Report

Present a summary table:

| Issue | Title | Status | Comment |
|---|---|---|---|
| #19 | ... | ✅ Closed | Summary posted |

### Step 8 — Next-steps notification

After the summary table, review commits and changes shipped to identify any post-deploy work the user may need to perform. Common categories:

| Category | Trigger | Example action |
|---|---|---|
| DB migration | `schema.sql` changed, new table/column/index | `apply_schema` or restart service |
| Service restart | Any production code change (no auto-reload) | `systemctl restart power-map` or gunicorn signal |
| Data migration | New normalizer, field rename, backfill script | Run the relevant `scripts/` file |
| Env var / secret | New config key added | Add to `/etc/power-map/env` and restart |
| Index bootstrap | New unique index on dirty table | Deduplicate first, then re-run `apply_schema` |

Present only the items that apply. Be specific — name the file, table, or command. Example:

```
**Next steps for production:**
- `apply_schema` required — `schema.sql` changed (new `link_types`/`links` tables + migration)
- Restart uvicorn/gunicorn — code changes don't auto-reload in production
```

Then **offer to execute** any item within your capabilities (e.g., running `apply_schema`, running a migration script). Ask once — don't nag.

If nothing applies, omit this step entirely.

### Step 9 — Teardown worktree (if applicable)

If work was done in a `.worktrees/` worktree, clean up after shipping:

```bash
# Kill the dev server on port 8001
fuser -k 8001/tcp 2>/dev/null || true

# Remove the worktree (from main checkout)
git worktree remove --force .worktrees/<branch> 2>/dev/null || true

# Delete the feature branch (now merged, safe to remove)
git branch -d <branch> 2>/dev/null || true
```

Report: "Dev server stopped. Worktree and branch cleaned up."

If no worktree was used (e.g. direct fix on main), skip this step.
