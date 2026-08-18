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

The `command` string is the literal `bash .claude/hooks/skills-submodule-update.sh` (project-dir-relative, not `$CLAUDE_PROJECT_DIR`-prefixed like the other hooks): `managing-skills` keys its idempotence and uninstall jq on that exact string.

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
