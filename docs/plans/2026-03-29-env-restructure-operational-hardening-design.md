# Design: Env restructure & operational hardening

**Date:** 2026-03-29
**Motivated by:** CannObserv/watcher#49 — same single-VM dev+prod setup, same stale-process risk

---

## Goal

Eliminate the risk of stale manually-started uvicorn processes competing on port 8000, separate production secrets from dev/agent credentials, document the operational model, and enforce the worktree-first development workflow.

---

## Approved approach

### 1. Env file hierarchy

| File | Owner | Contents |
|---|---|---|
| `/etc/power-map/.env` | root:exedev (640) | `DATABASE_URL`, `ADDRESS_VALIDATOR_API_KEY`, `ADDRESS_VALIDATOR_RUN_VALIDATION` |
| `.env` (repo, gitignored) | developer | `GH_TOKEN`, `TEST_DATABASE_URL` |

- `DATABASE_URL` moves from the repo `env` file to `/etc/power-map/.env` — keeps the production DSN out of worktrees and eliminates accidental-commit risk
- `.gitignore`: replace both `env` entries with `.env`
- All `export $(cat env | xargs)` references → `export $(cat .env | xargs) 2>/dev/null`

### 2. Systemd service

New `deploy/power-map.service`:

```ini
[Unit]
Description=power-map API
After=network.target postgresql.service

[Service]
User=exedev
WorkingDirectory=/home/exedev/power-map
EnvironmentFile=/etc/power-map/.env
EnvironmentFile=-/home/exedev/power-map/.env
ExecStart=/home/exedev/power-map/.venv/bin/uvicorn src.api.main:app --host 0.0.0.0 --port 8000 --workers 2
Restart=on-failure

[Install]
WantedBy=multi-user.target
```

- `/etc/power-map/.env` required; repo `.env` optional (`-` prefix)
- Production service runs on port **8000** under systemd
- Dev server always on port **8001** — no collision possible

### 3. Operational docs

**AGENTS.md** — new **Infrastructure** section:
- Single-VM dev+prod model
- Port table: 8000 = systemd (production), 8001 = dev server (worktree)
- **All development work must be done in a git worktree** — never edit the main checkout directly; `brainstorming` is the entry point that triggers worktree setup
- Server Lifecycle table:

| Situation | Action |
|---|---|
| After code change (production) | `sudo systemctl restart power-map` |
| Worktree dev testing | dev server on 8001 with `--reload` from worktree dir |
| Env var change | restart service (env is read at startup) |
| Service debugging | `sudo journalctl -u power-map -f` |

**COMMANDS.md** — new sections:
- **Environment** — how to load env (`.env` + `/etc/power-map/.env`)
- **Service Management** — `systemctl status/restart/logs`
- **Development** — dev server on 8001, exe.dev proxy access at `https://power-map.exe.xyz:8001/`

### 4. `using-git-worktrees` skill override

Local `skills/using-git-worktrees/SKILL.md` replaces the vendor symlink. Additions over the vendor version:
- Run `uv sync` after worktree create
- Copy `.env` from main worktree root into the new worktree
- After worktree is ready: kill any running dev server on port 8001, then start `uv run uvicorn src.api.main:app --host 0.0.0.0 --port 8001 --reload` from the new worktree directory
- Dev server instruction uses port 8001
- exe.dev proxy note: accessible at `https://power-map.exe.xyz:8001/`

### 5. Source code + script updates

- `src/core/db.py` — update docstring to reference `/etc/power-map/.env` for `DATABASE_URL`
- `tests/conftest.py` — update module docstring: `env` file → `.env`
- `scripts/import_cannabis_observer.py` — update error message (`export $(cat env | xargs)` → `export $(cat .env | xargs)`) and docstring path (`/etc/power-map/env` → `/etc/power-map/.env`)
- `scripts/deduplicate_roles.py` — update docstring path reference if present

---

## Key decisions

- **`DATABASE_URL` to `/etc`** — production DSN should never be reachable from a worktree or accidentally committed
- **Systemd over manual uvicorn** — the stale-process failure mode is eliminated by making the production server a managed service
- **Port split (8000/8001)** — hard separation between prod and dev; dev server always `--reload`, always in a worktree
- **Worktree-first enforced in docs** — `brainstorming` skill is the documented entry point; AGENTS.md makes clear the main checkout is not for development

## Out of scope

- DEPLOYMENT.md (no such file in this repo yet; could be added later)
- Changes to ingestion pipeline or application logic
- Multi-worker gunicorn configuration (current 2-worker uvicorn is sufficient)
