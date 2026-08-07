# power-map

Maps political and corporate power: people, organizations, roles, and their temporal relationships.

## Setup

```bash
git submodule update --init --recursive
uv sync
```

## Development

```bash
uv run uvicorn src.api.main:app --host 0.0.0.0 --port 8001 --reload --log-config src/core/log_config.json
```

## Testing

```bash
uv run pytest
```

See [docs/COMMANDS.md](docs/COMMANDS.md) for everyday commands,
[docs/TESTING.md](docs/TESTING.md) for the test tiers, and
[docs/RUNBOOKS.md](docs/RUNBOOKS.md) for seeds, backfills and audits.
[AGENTS.md](AGENTS.md) § Detail Docs indexes the whole reference tree.
