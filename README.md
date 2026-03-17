# power-map

Maps political and corporate power: people, organizations, roles, and their temporal relationships.

## Setup

```bash
git submodule update --init --recursive
uv sync
```

## Development

```bash
uv run uvicorn src.api.main:app --host 0.0.0.0 --port 8001 --reload
```

## Testing

```bash
uv run pytest
```

See [docs/COMMANDS.md](docs/COMMANDS.md) for full command reference.
