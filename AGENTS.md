# power-map — Agent Guidelines

Be terse. Prefer fragments over full sentences. Skip filler and preamble. Sacrifice grammar for density. Lead with the answer or action.

## Project Overview

Web service for mapping political and corporate power: people, organizations, roles, and their temporal relationships.

## Development Methodology

TDD required. Red → Green → Refactor. No production code without a failing test first.

## Environment & Tooling

Python ≥3.12, uv, pytest, ruff

## Project Layout

```
src/api/        — FastAPI app (ASGI, routes, auth, schemas)
src/core/       — Shared domain logic
tests/          — Mirrors src/ structure
docs/           — Reference docs (API, COMMANDS, SKILLS)
```

## Services

| Service | Framework | Port |
|---|---|---|
| API | FastAPI | 8001 |

```bash
# FastAPI dev server
uv run uvicorn src.api.main:app --host 0.0.0.0 --port 8001 --reload
```

After any code change in production deployments, restart uvicorn/gunicorn — they do not auto-reload.

## Secrets

`env` (git-ignored): any API keys or tokens. Never commit secrets.

## Common Commands

```bash
# Install dependencies
uv sync

# Run tests
uv run pytest

# Run linter
uv run ruff check .

# FastAPI dev server
uv run uvicorn src.api.main:app --host 0.0.0.0 --port 8001 --reload
```

Full reference: `docs/COMMANDS.md`

## Agent Skills

Skills in `skills/` (agentskills.io) and `.claude/skills/` (Claude Code). Reference: `docs/SKILLS.md`

## Conventions

**Commit Messages:**
```
#<number> [type]: <description>      # with issue
[type]: <description>                # without issue
```
Types: feat, fix, refactor, docs, test, chore

**Logging:**
```python
from src.core.logging import get_logger
logger = get_logger(__name__)
```
Entry points only: call `configure_logging()` once.

**Date & Time:**
- All UTC
- ISO 8601: `YYYY-MM-DDTHH:MM:SS.ffffffZ` (timestamps), `YYYY-MM-DD` (dates)

**General:**
- No inline module imports; all at file top
- Docstrings for public modules, classes, functions
- Test structure mirrors source (`src/foo.py` → `tests/test_foo.py`)
- Explicit imports only
- Small, focused functions
