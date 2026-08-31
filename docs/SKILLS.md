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

### Sensitive-path list (#488)

`.skills/doc-sensitive-paths` is this repo's replacement for the built-in `SENSITIVE_PATHS` in `shipping-work-python-fastapi/scripts/doc-check.sh` (Step 1.5) — one path per line, blank lines and `#`-comments ignored, same grammar as `.skills/import-targets`. It **replaces** the defaults wholesale; five of those twelve (`CHANGELOG.md`, `alembic/versions/`, `deploy/`, `src/models/`, `.env.example`) describe a layout this service does not have.

- Entries match whole path **segments** at any depth (upstream skills#252), so `scripts/` also reaches `tests/scripts/`. Over-matching is the cheap failure for a gate that exits `1` and asks a human to look; the under-match it replaced printed as a clean green. Do not re-anchor.
- A list where **no** entry matches a tracked file exits `2`; dead entries on a green run print as a note. `tests/test_doc_sensitive_paths.py` is the ratchet: every entry must match a tracked file, each documented tree must stay covered, and the pin must contain skills#252 (below it the file is ignored in silence).
- `docs/` and `tests/` are deliberately absent — a doc edit is not a signal to check the docs, and TDD means every branch touches `tests/`, which would make the gate constant.
- The guard also mirrors the vendored `path_matches` in Python so an entry can be checked the way the gate will check it, and corroborates that mirror against the vendored source. A miss there is an upstream refactor to re-anchor against — never a reason to edit `skills-vendor/`.

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
| `init-socraticode` | `gregoryfoster-skills` | init socraticode, set up code search, index this project, socraticode setup ³ |
| `init-project-fastapi` | `gregoryfoster-skills` | init project, bootstrap project, new fastapi project, set up foundation ³ |

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

³ **Initializers — power-map is long past bootstrap.** Neither is for setting this project up again. `init-socraticode` is linked for its **audit re-run** (`references/audit-rerun.md`): every phase is idempotent, so re-running it re-validates the policy block, manifest, hooks and graph yield, and is the only thing that catches a manifest the server silently rejected. `init-project-fastapi` is linked for its reference docs, which are the written form of several conventions this repo already follows. A re-run replaces `docs/SOCRATICODE.md` **only between its `<!-- BEGIN socraticode-doc -->` / `<!-- END socraticode-doc -->` markers** (skills#210, adopted in #463) — the `## Repo-specific notes` section below `END` survives. Above it there is nothing left to lose either: no local corrections remain, and `tests/test_socraticode_doc_parity.py` reds if the markers go missing, if the span drifts from the pinned template, or if a divergence block reappears unguarded.

## SocratiCode MCP Tools

SocratiCode provides semantic search and dependency graph tools via MCP. The rule lives in `AGENTS.md § Code Exploration Policy`; the full tool table, prefetch string and per-tool notes live in [SOCRATICODE.md](SOCRATICODE.md). Infrastructure details:

- **Index status:** `codebase_status` — check before relying on search results
- **Initial setup / reindex:** use the `socraticode:codebase-management` skill
- **Exploration tasks:** use the `socraticode:codebase-explorer` subagent for multi-file tracing
- **Index lives at:** `~/.socraticode/` (process-local, not committed)
- **After large refactors:** run `codebase_update` or trigger a full reindex via `socraticode:codebase-management` to keep the graph accurate
- **Duplicate MCP config warning:** if both `mcp__plugin_socraticode_socraticode__*` and `mcp__socraticode__*` tool prefixes appear, the standalone MCP is duplicated — remove it: `claude mcp remove socraticode`

### Prefetch hook

`.claude/hooks/socraticode-reminder.sh` — a `SessionStart` hook, symlinked into `skills-vendor/gregoryfoster-skills/skills/init-socraticode/scripts/`. It prints the `ToolSearch` prefetch that loads the deferred `codebase_*` schemas; the string itself is in [SOCRATICODE.md](SOCRATICODE.md) § Prefetch, for running by hand when the hook did not fire.

It was a hand-authored copy until #463, because the vendored hook prefetched 9 tools and this repo's table recommends 12. skills#209 made it a superset, so the copy became a symlink and the upstream prefetch now arrives on the normal submodule refresh. `tests/test_socraticode_doc_parity.py` holds the doc and the hook to the same tool list, and reds if the pin rolls back past that fix.

### Health hook

`.claude/hooks/socraticode-health.sh` — a `SessionStart` hook, symlinked into `skills-vendor/gregoryfoster-skills/skills/init-socraticode/scripts/`. Once per UTC day (`.git/socraticode-health.lock`), logs to `.git/socraticode-health.log`, exits `0` on every path.

It **reports; it never repairs or re-indexes.** Silent when clean, so read a quiet session as healthy rather than as not-run — force a check with `SOCRATICODE_HEALTH_FORCE=1 bash .claude/hooks/socraticode-health.sh`.

What it catches that three green lights do not:

- a FAILED last operation or an INCOMPLETE index sitting unreported
- a stopped Qdrant container or a missing embedding model
- **declared ≠ indexed** (#461). A *completed* operation can still leave a context
  artifact out — #454 sat at `2/3 indexed` with every light green and the whole
  `docs/` tree unreachable. The check measures the manifest's declared count
  against per-artifact status and names the shortfall
  (`context artifacts 2/3 indexed — reference-docs: ○ not yet indexed`); recovery
  is `codebase_context_index`. Pinned by `tests/test_socraticode_health_parity.py`,
  which fails if the submodule rolls back past the fix
- **graph yield.** `READY` is a status, not a result: the graph can be READY with almost no edges, and `codebase_graph_query` then answers "no dependents" rather than failing. Yield is measured in edges/file against a `0.1` floor — that ratio, not `unresolvedPct`, is the verdict.

#### Reading the daily `unresolved N%` line

The hook reports `graph unresolved 65.7% (> 50%) — corroborates a resolver problem` here **every day, and that is not a defect.** `unresolvedPct` counts *call* edges whose callee resolves to no first-party symbol, so any codebase leaning on frameworks and stdlib runs high by construction — `asyncpg`, `ULID`, `os`, FastAPI and pytest are not in this repo and no re-index lowers it. Judge on `verdict` and edges/file; power-map is `verdict: ok` at 1.49 edges/file against a 0.1 floor.

Verified rather than assumed, by the differential test: `codebase_graph_query` on `src/core/db.py` returns exactly one outbound edge (`src/core/logging.py` — precisely its one first-party import) and 217 unique importers, matching an `rg` sweep over every import spelling at 217. No misses, no false positives. **The import graph is exact; treat `codebase_graph_query` and `codebase_impact` as trustworthy.**

Do not write the reverse of this into the docs — a sibling repo distrusted a correct tool for weeks on that misreading, costing an `rg` round-trip per dependency question (gregoryfoster/skills#198). The distinguishing signal for the real defect (SocratiCode#107) is *near-zero edges/file*, not a high percentage. If you do suspect the graph, re-run the differential test above rather than reasoning from the number.

### The policy block is curation-exempt

`AGENTS.md`'s `## Code Exploration Policy` sits between `<!-- BEGIN socraticode-policy -->` / `<!-- END socraticode-policy -->` markers. `curating-context` refuses to edit a marked policy block, so **that section cannot be trimmed by the budget skill** — it is the one part of `AGENTS.md` the token budget can never reclaim.

That is the deliberate trade for idempotence: a marked block is patched in place by an `init-socraticode` re-run instead of taking the whole-span replace branch that silently eats repo-authored prose (skills#115). To shrink it, move content into this file, or into `SOCRATICODE.md` **below its `<!-- END socraticode-doc -->` marker**, and re-run the skill — above that marker is the template's, and a re-run replaces it. Do not hand-edit between either file's markers, and do not expect `curating-context` to do it for you. `AGENTS.md` runs close to its 6000-token ceiling, so budget pressure has to go elsewhere. The rules that ceiling implies — index lines stay pointers, counts carry a command or drop their precision — are in [CONTEXT.md](CONTEXT.md), gated by `tests/test_context_surface.py`.

### Context artifacts

`.socraticodecontextartifacts.json` points `codebase_context_search` at the project's non-code knowledge. Three entries: `src/core/schema.sql`, `AGENTS.md`, and `docs` — the last a **directory**, indexed recursively.

Name directories, not files. The manifest previously listed four individual docs; the #407 split grew the tree to 32 and left 29 unreachable, which reads as "no results" rather than "not indexed". Globs do not work — the server `stat()`s the literal value. Guarded by `tests/test_context_artifacts.py`, which fails if any `docs/*.md` stops being reachable.

#### Declared ≠ indexed

`test_context_artifacts.py` asserts *declaration*, not *indexing* — it cannot see an entry the server accepted and never indexed. That is a second, harder cause of the same silence: to a `codebase_context_search` caller, an artifact that resolved but was never indexed looks exactly like a path that did not resolve. No results, no error.

#454 is the field case: one entry hit `fetch failed` while an incremental update ran concurrently, the operation then read *completed*, `codebase_status` settled at `2/3 indexed`, and every health light stayed green — with the whole 2.5 MB `docs/` tree unreachable.

- **Where to look.** `codebase_context` reports per-artifact `✓ indexed` status and is the only place that status exists; `codebase_status` gives a count and never a name.
- **Recovery** is a plain retry of `codebase_context_index` — but **not concurrently with an incremental update.** The CPU-only embedding backend serializes badly: a 5-file incremental took 31 min beside a context embed, and 0.7s solo. The clean retry indexed everything.
- **Who catches it now.** The daily health hook, since #461 (see § Health hook above). `tests/test_socraticode_health_parity.py` pins the vendored driver that carries the check, so a submodule rollback reds a test instead of silently reopening the gap.

This section is where those pointers live **on purpose.** They were previously a `local-divergence` block in `docs/SOCRATICODE.md`, which is generated: back then the whole file was overwritten on a re-run, so repo-specific content there survived only while a guard test protected it. All three blocks are now retired (#461, #463): the template carries each explanation itself, so that file holds the template's wording plus the adaptation it asks for, and a re-run has no correction to re-apply. Every retirement left an ancestry ratchet behind in `tests/test_socraticode_doc_parity.py` or `tests/test_socraticode_health_parity.py`, so rolling the submodule back past any of them reds a test instead of quietly reopening the gap. That file now also carries the skills#210 marker pair, so its `## Repo-specific notes` section is a second safe home for repo-authored SocratiCode prose — this one stays here because it is about the *artifacts*, not about exploration.

## Hook command form

Every entry in `.claude/settings.json` uses:

```
bash "${CLAUDE_PROJECT_DIR:-.}/.claude/hooks/<script>.sh"
```

The `:-.` fallback is load-bearing. A bare `$CLAUDE_PROJECT_DIR` becomes `bash "/.claude/hooks/…"` when the variable is unset and errors on every session start; a cwd-relative command runs the wrong file when the hook process starts anywhere but the repo root. Guarded by `tests/sh/claude_hooks.bats`, which also asserts every registered hook exists and every hook symlink resolves.

Entries installed by `managing-skills/scripts/install-hook.sh` carry a trailing `# <marker>` comment (`socraticode-reminder.sh` has `# socraticode-prefetch` since #463). It is the installer's dedupe key, written into the *command string* so a re-run can recognize its own entry by reading `settings.json` alone — not decoration, and not something to tidy away. **Never hand-wire a vendored hook:** run the installer, which writes the symlink and the registration together and upgrades a legacy hand-typed copy in place.

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
