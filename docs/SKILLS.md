# Agent Skills

This project follows the [agentskills.io](https://agentskills.io) spec.

## Directory Layout

Two directories serve different discovery systems:

| Directory | Discovery system | Contents |
|---|---|---|
| `skills/` | agentskills.io | Committed overrides + symlinks → `skills-vendor/` |
| `.claude/skills/` | Claude Code | Symlinks → `../../skills/<name>` |

Local overrides in `skills/` automatically shadow vendor skills in both systems. When adding a skill, always create both the `skills/<name>` entry and `.claude/skills/<name>` symlink.

## External Skill Repos (Git Submodules)

| Repo | Submodule path |
|---|---|
| [`gregoryfoster/skills`](https://github.com/gregoryfoster/skills) | `skills-vendor/gregoryfoster-skills/` |
| [`obra/superpowers`](https://github.com/obra/superpowers) | `skills-vendor/obra-superpowers/` |

Init after cloning: `git submodule update --init --recursive`

### Auto-refresh hook (#386)

Submodule freshness is maintained by the vendored `SessionStart` hook `.claude/hooks/skills-submodule-update.sh` — a **symlink** into `skills-vendor/gregoryfoster-skills/skills/managing-skills/scripts/`, so upstream fixes to the hook arrive with the next submodule bump.

- Once per UTC day (`.git/skills-update.lock`), `main` only, scoped to `skills-vendor/` — never other submodules
- Auto-commits the pointer bump (`chore: update skills submodules`); logs to `.git/skills-update.log`
- Exits `0` on every non-fatal condition — a session can never be blocked by it
- Opportunistically runs `install-doctor.sh` on **every** branch (not day-gated) so `.skills/doctor.sh` self-heals; the commit of that refresh stays behind the `main`-only + daily gates

Replaced the legacy inline `UserPromptSubmit` one-liner, which committed submodule bumps on any branch and never refreshed the doctor. Do not re-add it — two mechanisms racing on the same submodule.

The `command` string is `bash "${CLAUDE_PROJECT_DIR:-.}/.claude/hooks/skills-submodule-update.sh"`, the same anchored form as every other hook — see [Hook command form](#hook-command-form) below. An earlier note here claimed this one had to stay cwd-relative because `managing-skills` keyed its idempotence and uninstall jq on that exact string; it does not. Both the strip and the uninstall filter match on the *script path* substring precisely so an entry written in either form stays removable, and `install-refresh.sh --check` confirms it recognizes the anchored form.

Force-refresh: `git submodule update --remote --merge -- skills-vendor/`

### Doctor

`.skills/doctor.sh` diagnoses and repairs broken vendor symlinks / uninitialized submodules. It is a real **file copy**, not a symlink — a symlinked doctor would dangle in exactly the failure mode it exists to repair — and re-syncs itself from the vendored source on every run (upstream skills#84). Check with `bash .skills/doctor.sh --version`; silent + exit `0` means healthy.

To add a new external skill repo: follow the `managing-skills` skill.

## Available Skills

| Skill | Source | Triggers |
|---|---|---|
| `reviewing-code-python-fastapi` | `gregoryfoster-skills` | CR, code review, perform a review |
| `reviewing-architecture` | `gregoryfoster-skills` | AR, architecture review, architectural review |
| `enforcing-architecture` | `gregoryfoster-skills` | add a fitness function, enforce this contract, lock this rule |
| `shipping-work-python-fastapi` | `gregoryfoster-skills` | ship it, push GH, close GH, wrap up |
| `brainstorming` | Local override | brainstorm, design this, let's design |
| `systematic-debugging` | `obra-superpowers` | description-driven¹ |
| `verification-before-completion` | `obra-superpowers` | description-driven¹ |
| `test-driven-development` | `obra-superpowers` | description-driven¹ |
| `writing-plans` | `gregoryfoster-skills` | write a plan, plan this, let's plan |
| `writing-skills` | `obra-superpowers` | write skill, new skill, author skill |
| `subagent-driven-development` | `obra-superpowers` | subagent dev, dispatch agents |
| `dispatching-parallel-agents` | `obra-superpowers` | parallel agents |
| `using-git-worktrees` | `gregoryfoster-skills` | create worktree, new worktree, destroy worktree, wt ² |
| `managing-skills` | `gregoryfoster-skills` | add skill repo, add external skills, manage skills |
| `orchestrating-issue-backlog` | `gregoryfoster-skills` | orchestrate backlog, prioritize issues, plan issue execution, clear backlog |
| `curating-context` | `gregoryfoster-skills` | curate context, context budget, hone AGENTS.md, trim AGENTS.md, prune context |

² **Project override — `using-git-worktrees` (#450):** its `worktree-create.sh` links a new
worktree's `.venv` at the main checkout's. Here the main checkout is production's working
directory, so that shared venv is rewritten by the service's own tooling — `uv run` (nine
systemd units, one every 2 min) restamps the project version, `uv sync` (ExecStartPre)
prunes the opt-in groups. `scripts/worktree-setup.sh` undoes the link straight after
creation; it is not redundant with the skill, it deliberately reverses one of its steps.
Opt-out requested upstream in [gregoryfoster/skills#201](https://github.com/gregoryfoster/skills/issues/201) —
per the vendored-skill policy, never edit `skills-vendor/`. Details → `docs/COMMANDS.md`
§ Development.

¹ Description-driven: `systematic-debugging` on any bug/test failure; `verification-before-completion` before any completion claim or commit; `test-driven-development` before writing implementation code.

## SocratiCode MCP Tools

SocratiCode provides semantic search and dependency graph tools via MCP. Tool selection guide is in `AGENTS.md §Code Exploration Policy`. Infrastructure details:

- **Index status:** `codebase_status` — check before relying on search results
- **Initial setup / reindex:** use the `socraticode:codebase-management` skill
- **Exploration tasks:** use the `socraticode:codebase-explorer` subagent for multi-file tracing
- **Index lives at:** `~/.socraticode/` (process-local, not committed)
- **After large refactors:** run `codebase_update` or trigger a full reindex via `socraticode:codebase-management` to keep the graph accurate
- **Duplicate MCP config warning:** if both `mcp__plugin_socraticode_socraticode__*` and `mcp__socraticode__*` tool prefixes appear, the standalone MCP is duplicated — remove it: `claude mcp remove socraticode`

### Health hook

`.claude/hooks/socraticode-health.sh` — a `SessionStart` hook, symlinked into `skills-vendor/gregoryfoster-skills/skills/init-socraticode/scripts/`. Once per UTC day (`.git/socraticode-health.lock`), logs to `.git/socraticode-health.log`, exits `0` on every path.

It **reports; it never repairs or re-indexes.** Silent when clean, so read a quiet session as healthy rather than as not-run — force a check with `SOCRATICODE_HEALTH_FORCE=1 bash .claude/hooks/socraticode-health.sh`.

What it catches that three green lights do not:

- a FAILED last operation or an INCOMPLETE index sitting unreported
- a stopped Qdrant container or a missing embedding model
- **graph yield and resolver health.** `READY` is a status, not a result: the graph can be READY with almost no edges, and `codebase_graph_query` then answers "no dependents" rather than failing. Yield is measured in edges/file against a `0.1` floor; unresolved call edges are warned above 50%.

> **Open finding.** This repo currently reports **unresolved 65.7%** — edge yield is healthy (1.49 edges/file) but roughly two-thirds of call edges do not resolve to a target, so `codebase_graph_query` and `codebase_impact` are materially incomplete. Prefer `codebase_search` for semantic questions and corroborate impact analysis by hand until this is diagnosed.

### Context artifacts

`.socraticodecontextartifacts.json` points `codebase_context_search` at the project's non-code knowledge. Three entries: `src/core/schema.sql`, `AGENTS.md`, and `docs` — the last a **directory**, indexed recursively.

Name directories, not files. The manifest previously listed four individual docs; the #407 split grew the tree to 32 and left 29 unreachable, which reads as "no results" rather than "not indexed". Globs do not work — the server `stat()`s the literal value. Guarded by `tests/test_context_artifacts.py`, which fails if any `docs/*.md` stops being reachable.

## Hook command form

Every entry in `.claude/settings.json` uses:

```
bash "${CLAUDE_PROJECT_DIR:-.}/.claude/hooks/<script>.sh"
```

The `:-.` fallback is load-bearing. A bare `$CLAUDE_PROJECT_DIR` becomes `bash "/.claude/hooks/…"` when the variable is unset and errors on every session start; a cwd-relative command runs the wrong file when the hook process starts anywhere but the repo root. Guarded by `tests/sh/claude_hooks.bats`, which also asserts every registered hook exists and every hook symlink resolves.

## Local Overrides

A committed directory in `skills/` completely supersedes the vendor version (no inheritance). Must be fully self-contained.

| Skill | Override reason |
|---|---|
| `brainstorming` | Project conventions (docs/plans/ path, commit format); invokes using-git-worktrees after design approval; FastAPI stack context; proactive-suggestion mode |

## Authoring New Skills

Follow the `writing-skills` TDD cycle:
1. **RED** — run pressure scenarios without the skill; document where the agent fails
2. **GREEN** — write a minimal SKILL.md addressing those failures
3. **REFACTOR** — find new rationalizations, close loopholes, re-test

New project-specific skills go in `skills/<name>/` with a `.claude/skills/<name>` symlink to `../../skills/<name>`. Cross-project skills belong in `gregoryfoster/skills`.
