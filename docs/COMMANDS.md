# Common Commands

## Setup

```bash
# Provision local PostgreSQL (idempotent; run once after cloning)
bash scripts/setup-db.sh

# Install dependencies (creates .venv automatically)
uv sync
```

## Development

```bash
# FastAPI dev server (auto-reload)
uv run uvicorn src.api.main:app --host 0.0.0.0 --port 8000 --reload

# Inject admin auth headers locally via mitmdump reverse proxy (port 3000 → 8000)
mitmdump \
  --mode reverse:http://localhost:8000 \
  --listen-port 3000 \
  --set modify_headers='/~q/X-Exedev-Email/admin@example.com' \
  --set modify_headers='/~q/X-Exedev-Userid/usr_local_dev'
```

## Testing

```bash
# Run all tests (excludes integration)
uv run pytest

# Run with coverage
uv run pytest --cov

# Run a specific file
uv run pytest tests/path/to/test_file.py --no-cov

# Run integration tests (hits live external services)
uv run pytest -m integration
```

## Linting

```bash
# Check
uv run ruff check .

# Fix auto-fixable issues
uv run ruff check --fix .
```

## Import

```bash
# Load env vars first
export $(cat env | xargs)

# Import Cannabis Observer CSV exports
uv run python scripts/import_cannabis_observer.py \
    --orgs   data/cannabis_observer/Organizations.csv \
    --people data/cannabis_observer/People.csv \
    --roles  data/cannabis_observer/Roles.csv

# Also run address validation (requires ADDRESS_VALIDATOR_API_KEY)
uv run python scripts/import_cannabis_observer.py \
    --orgs   data/cannabis_observer/Organizations.csv \
    --people data/cannabis_observer/People.csv \
    --roles  data/cannabis_observer/Roles.csv \
    --validate-addresses

# Options
#   --source-reliability FLOAT  Source reliability score (0.0–1.0, default: 0.8)
#   --imported-by STRING        Importer label (default: cannabis-observer-csv-import)
#   --validate-addresses        Also call /validate for deliverability confirmation
```

## Git Submodules

```bash
# Init after cloning
git submodule update --init --recursive

# Force-refresh vendor skills
git submodule update --remote --merge vendor/gregoryfoster-skills vendor/obra-superpowers
```
