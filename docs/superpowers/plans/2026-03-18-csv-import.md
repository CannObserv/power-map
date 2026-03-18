# CSV Import & Ingestion Infrastructure Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Import Cannabis Observer CSVs (Organizations, People, Roles) into PostgreSQL while building reusable normalizer and ingestion infrastructure in `src/core/`.

**Architecture:** EVTL pipeline (Extract → Validate → Transform → Load). Pydantic v2 validates each CSV row and accumulates field errors. Pure normalizers (phone, email, url, identifier) are sync; address normalizer is async (calls external service or falls back to `usaddress`). Multi-pass loading (orgs → people → roles → assignments) resolves references via in-memory indices. Every created entity gets a provenance record and per-field confidence records in the same transaction.

**Tech Stack:** Python 3.12, asyncpg, Pydantic v2, phonenumbers, email-validator, validators, usaddress, httpx, pytest

---

## Prerequisites (manual steps before Task 1)

1. Obtain the three CSV files and place them at:
   - `data/cannabis_observer/Organizations.csv`
   - `data/cannabis_observer/People.csv`
   - `data/cannabis_observer/Roles.csv`
2. Verify exact CSV column headers match what the plan assumes (open each file and check row 1).
3. `ADDRESS_VALIDATOR_API_KEY` is not yet in `env` — add it when available. The import runs without it (`run_validation=False` and `ExternalAddressNormalizerConfig` requires the key only if you instantiate the external normalizer).

---

## File Map

**Create:**
```
src/core/normalizers/__init__.py
src/core/normalizers/base.py          NormalizationResult, Normalizer Protocol, is_null_like
src/core/normalizers/phone.py         PhoneNormalizer
src/core/normalizers/email.py         EmailNormalizer
src/core/normalizers/url.py           UrlNormalizer
src/core/normalizers/identifier.py    IdentifierNormalizer
src/core/normalizers/address.py       LocalAddressNormalizer, ExternalAddressNormalizer,
                                       FallbackAddressNormalizer, AddressNormalizerConfig
src/core/ingestion/__init__.py
src/core/ingestion/base.py            RowResult, FieldError, ConfidenceRecord, value_hash
src/core/ingestion/pipeline.py        ReferenceData, ImportBatch, run_import
src/core/ingestion/sources/__init__.py
src/core/ingestion/sources/csv_org.py    OrgRow, validate_org, transform_org
src/core/ingestion/sources/csv_person.py PersonRow, validate_person, transform_person
src/core/ingestion/sources/csv_role.py   RoleRow, validate_role, transform_role
scripts/import_cannabis_observer.py   CLI entry point
tests/core/normalizers/__init__.py
tests/core/normalizers/test_phone.py
tests/core/normalizers/test_email.py
tests/core/normalizers/test_url.py
tests/core/normalizers/test_identifier.py
tests/core/normalizers/test_address.py
tests/core/ingestion/__init__.py
tests/core/ingestion/test_base.py
tests/core/ingestion/test_csv_org.py
tests/core/ingestion/test_csv_person.py
tests/core/ingestion/test_csv_role.py
tests/core/ingestion/test_pipeline.py  (integration)
tests/fixtures/ingestion/             small fixture CSV files
```

**Modify:**
```
src/core/schema.sql        add google_drive URL type + 3 new tables
src/core/db.py             remove normalize_phone, validate_email
tests/core/test_db.py      remove phone/email tests + update imports
pyproject.toml             add validators, usaddress; move httpx to main deps
.gitignore                 add data/cannabis_observer/
```

---

### Task 1: Schema additions

**Files:**
- Modify: `src/core/schema.sql`
- Modify: `tests/core/test_schema.py`

The schema needs four additions:
1. `google_drive` URL type seed row
2. `import_batches` table
3. `import_provenance` table
4. `field_confidence` table

- [ ] **Step 1: Write failing integration tests for new tables**

Add to `tests/core/test_schema.py` (at the bottom, inside the existing file which already has an `asyncpg` `db` fixture):

```python
# ---------------------------------------------------------------------------
# import_batches
# ---------------------------------------------------------------------------

@pytest.mark.integration
async def test_import_batches_insert(db):
    batch_id = generate_id()
    await db.execute(
        """INSERT INTO import_batches (id, source_file, file_hash, row_count, loaded_count, error_count)
           VALUES ($1, $2, $3, $4, $5, $6)""",
        batch_id, "orgs.csv", "abc123", 10, 9, 1,
    )
    row = await db.fetchrow("SELECT * FROM import_batches WHERE id = $1", batch_id)
    assert row["row_count"] == 10
    assert row["error_count"] == 1


# ---------------------------------------------------------------------------
# import_provenance
# ---------------------------------------------------------------------------

@pytest.mark.integration
async def test_import_provenance_insert(db):
    import json
    batch_id = generate_id()
    await db.execute(
        """INSERT INTO import_batches (id, source_file, file_hash, row_count, loaded_count, error_count)
           VALUES ($1, $2, $3, $4, $5, $6)""",
        batch_id, "orgs.csv", "abc123", 1, 1, 0,
    )
    prov_id = generate_id()
    org_id = generate_id()
    await db.execute(
        """INSERT INTO import_provenance
               (id, batch_id, source_row, entity_type, entity_id, action, raw_data)
           VALUES ($1, $2, $3, $4, $5, $6, $7)""",
        prov_id, batch_id, 2, "organization", org_id, "created",
        json.dumps({"Name": "Acme Corp"}),
    )
    row = await db.fetchrow("SELECT * FROM import_provenance WHERE id = $1", prov_id)
    assert row["action"] == "created"
    assert row["entity_type"] == "organization"


@pytest.mark.integration
async def test_import_provenance_invalid_action(db):
    batch_id = generate_id()
    await db.execute(
        """INSERT INTO import_batches (id, source_file, file_hash, row_count, loaded_count, error_count)
           VALUES ($1, $2, $3, $4, $5, $6)""",
        batch_id, "orgs.csv", "abc123", 1, 0, 1,
    )
    with pytest.raises(asyncpg.CheckViolationError):
        await db.execute(
            """INSERT INTO import_provenance
                   (id, batch_id, source_row, entity_type, entity_id, action, raw_data)
               VALUES ($1, $2, $3, $4, $5, $6, $7)""",
            generate_id(), batch_id, 1, "organization", generate_id(), "bogus",
            "{}",
        )


# ---------------------------------------------------------------------------
# field_confidence
# ---------------------------------------------------------------------------

@pytest.mark.integration
async def test_field_confidence_insert(db):
    org_id = generate_id()
    await db.execute(
        "INSERT INTO organizations (id) VALUES ($1)", org_id
    )
    conf_id = generate_id()
    await db.execute(
        """INSERT INTO field_confidence
               (id, entity_type, entity_id, field_name, value_hash,
                source_reliability, validation_status)
           VALUES ($1, $2, $3, $4, $5, $6, $7)""",
        conf_id, "organization", org_id, "phone",
        "abc123hash", 0.8, "unconfirmed",
    )
    row = await db.fetchrow("SELECT * FROM field_confidence WHERE id = $1", conf_id)
    assert row["source_reliability"] == pytest.approx(0.8)
    assert row["validation_status"] == "unconfirmed"


@pytest.mark.integration
async def test_field_confidence_source_reliability_bounds(db):
    org_id = generate_id()
    await db.execute("INSERT INTO organizations (id) VALUES ($1)", org_id)
    with pytest.raises(asyncpg.CheckViolationError):
        await db.execute(
            """INSERT INTO field_confidence
                   (id, entity_type, entity_id, field_name, value_hash,
                    source_reliability, validation_status)
               VALUES ($1, $2, $3, $4, $5, $6, $7)""",
            generate_id(), "organization", org_id, "phone",
            "abc123", 1.5, "unconfirmed",  # out of range
        )


@pytest.mark.integration
async def test_field_confidence_append_only_by_convention(db):
    """Two confidence rows for same entity+field is allowed (append-only history)."""
    org_id = generate_id()
    await db.execute("INSERT INTO organizations (id) VALUES ($1)", org_id)
    for _ in range(2):
        await db.execute(
            """INSERT INTO field_confidence
                   (id, entity_type, entity_id, field_name, value_hash,
                    source_reliability, validation_status)
               VALUES ($1, $2, $3, $4, $5, $6, $7)""",
            generate_id(), "organization", org_id, "phone",
            "samehash", 0.8, "unconfirmed",
        )
    count = await db.fetchval(
        "SELECT count(*) FROM field_confidence WHERE entity_id = $1", org_id
    )
    assert count == 2


@pytest.mark.integration
async def test_url_type_google_drive_seeded(db):
    row = await db.fetchrow("SELECT * FROM url_types WHERE slug = 'google_drive'")
    assert row is not None
    assert row["display_name"] == "Google Drive"
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd .worktrees/feature/csv-import
export $(cat ../../env | xargs)
uv run pytest tests/core/test_schema.py -m integration -k "import_batches or import_provenance or field_confidence or google_drive" -v
```

Expected: `ERROR` — tables don't exist yet.

- [ ] **Step 3: Add to schema.sql**

Add the `google_drive` URL type to the existing `url_types` INSERT block (after `'other'`):

```sql
    ('01KKZ3WGJSZF0F96SMYC000AVP', 'google_drive', 'Google Drive'),
```

Wait — `01KKZ3WGJSZF0F96SMYC000AVP` conflicts with the existing entity_identifier_types seed.
Generate fresh ULIDs for new seeds by running: `python3 -c "from ulid import ULID; [print(ULID()) for _ in range(3)]"`

Use the three generated IDs for the new tables' seed rows and the google_drive URL type.

Add to `schema.sql` after the existing seed `INSERT INTO url_types` block:

```sql
-- google_drive added separately to avoid regenerating existing seed IDs
INSERT INTO url_types (id, slug, display_name) VALUES
    ('<new-ulid-1>', 'google_drive', 'Google Drive')
ON CONFLICT (slug) DO NOTHING;
```

Add the three new tables and `updated_at` trigger for `import_batches` **after the existing seed data section**:

```sql
-- =============================================================================
-- Ingestion Audit Tables
-- =============================================================================

CREATE TABLE IF NOT EXISTS import_batches (
    id           TEXT        PRIMARY KEY,
    source_file  TEXT        NOT NULL,
    file_hash    TEXT        NOT NULL,
    imported_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    imported_by  TEXT,
    row_count    INTEGER     NOT NULL,
    loaded_count INTEGER     NOT NULL,
    error_count  INTEGER     NOT NULL,
    notes        TEXT
);

CREATE TABLE IF NOT EXISTS import_provenance (
    id              TEXT        PRIMARY KEY,
    batch_id        TEXT        NOT NULL REFERENCES import_batches(id),
    source_row      INTEGER     NOT NULL,
    entity_type     TEXT        NOT NULL,
    entity_id       TEXT        NOT NULL,
    action          TEXT        NOT NULL CHECK (action IN ('created','matched','skipped','error')),
    error_detail    JSONB,
    raw_data        JSONB       NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_import_provenance_batch
    ON import_provenance(batch_id);

CREATE INDEX IF NOT EXISTS idx_import_provenance_entity
    ON import_provenance(entity_type, entity_id);

-- Append-only: never UPDATE, always INSERT to preserve history.
-- Latest assessment: ORDER BY assessed_at DESC LIMIT 1.
CREATE TABLE IF NOT EXISTS field_confidence (
    id                  TEXT        PRIMARY KEY,
    entity_type         TEXT        NOT NULL,
    entity_id           TEXT        NOT NULL,
    field_name          TEXT        NOT NULL,
    value_hash          TEXT        NOT NULL,
    source_reliability  REAL        NOT NULL CHECK (source_reliability BETWEEN 0.0 AND 1.0),
    validation_status   TEXT        NOT NULL CHECK (validation_status IN (
                            'confirmed', 'unconfirmed', 'failed', 'not_attempted')),
    validation_detail   JSONB,
    assessed_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    assessed_by         TEXT
);

CREATE INDEX IF NOT EXISTS idx_field_confidence_entity
    ON field_confidence(entity_type, entity_id, field_name);
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
uv run pytest tests/core/test_schema.py -m integration -k "import_batches or import_provenance or field_confidence or google_drive" -v
```

Expected: all new tests pass. Existing tests still pass.

- [ ] **Step 5: Commit**

```bash
git add src/core/schema.sql tests/core/test_schema.py
git commit -m "#5 feat: add import_batches, import_provenance, field_confidence tables"
```

---

### Task 2: Project setup

**Files:**
- Modify: `pyproject.toml`
- Modify: `.gitignore`

- [ ] **Step 1: Update pyproject.toml**

Move `httpx` from `[dependency-groups] dev` to `[project] dependencies`.
Add `validators` and `usaddress` to `[project] dependencies`:

```toml
dependencies = [
    "asyncpg>=0.31.0",
    "email-validator>=2.3.0",
    "fastapi>=0.115.0",
    "httpx>=0.28.1",
    "phonenumbers>=9.0.26",
    "python-dotenv>=1.2.2",
    "python-json-logger>=4.0.0",
    "python-ulid>=3.1.0",
    "typing-extensions>=4.15.0",
    "usaddress>=0.5.10",
    "uvicorn>=0.34.0",
    "validators>=0.34.0",
]
```

Remove `"httpx>=0.28.1"` from `[dependency-groups] dev`.

- [ ] **Step 2: Update .gitignore**

Add a line:
```
data/cannabis_observer/
```

- [ ] **Step 3: Install and verify**

```bash
uv sync
python3 -c "import usaddress, validators, httpx; print('ok')"
```

Expected: `ok`

- [ ] **Step 4: Commit**

```bash
git add pyproject.toml uv.lock .gitignore
git commit -m "#5 chore: add validators, usaddress deps; move httpx to main"
```

---

### Task 3: Normalizer base

**Files:**
- Create: `src/core/normalizers/__init__.py`
- Create: `src/core/normalizers/base.py`
- Create: `tests/core/normalizers/__init__.py`
- Create: `tests/core/ingestion/__init__.py`
- Create: `tests/core/normalizers/test_base.py`

- [ ] **Step 1: Write failing tests**

Create `tests/core/normalizers/__init__.py` (empty).
Create `tests/core/ingestion/__init__.py` (empty).
Create `tests/core/normalizers/test_base.py`:

```python
"""Tests for src.core.normalizers.base."""

from src.core.normalizers.base import NormalizationResult, is_null_like


def test_is_null_like_empty():
    assert is_null_like("") is True
    assert is_null_like(None) is True


def test_is_null_like_sentinels():
    for v in ("N/A", "n/a", "NA", "None", "null", "TBD", "unknown", "-", "--"):
        assert is_null_like(v) is True, f"expected {v!r} to be null-like"


def test_is_null_like_real_value():
    assert is_null_like("Acme Corp") is False
    assert is_null_like("(206) 555-1234") is False
    assert is_null_like("user@example.com") is False


def test_normalization_result_defaults():
    r = NormalizationResult(value="foo")
    assert r.skipped is False
    assert r.warnings == []
    assert r.confidence_hint == "unconfirmed"
    assert r.validation_detail is None


def test_normalization_result_skipped():
    r = NormalizationResult(value=None, skipped=True)
    assert r.skipped is True
    assert r.value is None
```

- [ ] **Step 2: Run to verify failure**

```bash
uv run pytest tests/core/normalizers/test_base.py -v
```

Expected: `ImportError` — module doesn't exist.

- [ ] **Step 3: Implement**

Create `src/core/normalizers/__init__.py` (empty).

Create `src/core/normalizers/base.py`:

```python
"""Base types for the normalizer hierarchy."""

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

# Strings treated as absent / unknown regardless of case
NULL_LIKE: frozenset[str] = frozenset({
    "", "n/a", "na", "none", "null", "unknown", "tbd", "-", "--", "n.a.", "not available",
})


def is_null_like(raw: str | None) -> bool:
    """Return True if *raw* is absent or a known null-like sentinel."""
    return raw is None or raw.strip().lower() in NULL_LIKE


@dataclass
class NormalizationResult:
    """Output of a single normalizer call."""

    value: Any | None                    # normalized output; None when skipped
    skipped: bool = False                # True when input was absent/null-like
    warnings: list[str] = field(default_factory=list)
    confidence_hint: str = "unconfirmed"  # feeds field_confidence.validation_status
    validation_detail: dict | None = None


@runtime_checkable
class Normalizer(Protocol):
    """Synchronous normalizer interface (phone, email, url, identifier)."""

    def normalize(self, raw: str | None) -> NormalizationResult: ...


@runtime_checkable
class AsyncNormalizer(Protocol):
    """Asynchronous normalizer interface (address: may call external service)."""

    async def normalize(self, raw: str | None) -> NormalizationResult: ...
```

- [ ] **Step 4: Run tests**

```bash
uv run pytest tests/core/normalizers/test_base.py -v
```

Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add src/core/normalizers/ tests/core/normalizers/ tests/core/ingestion/
git commit -m "#5 feat: add normalizer base types (NormalizationResult, Normalizer protocol)"
```

---

### Task 4: Phone normalizer

**Files:**
- Create: `src/core/normalizers/phone.py`
- Create: `tests/core/normalizers/test_phone.py`

- [ ] **Step 1: Write failing tests**

Create `tests/core/normalizers/test_phone.py`:

```python
"""Tests for PhoneNormalizer."""

import pytest

from src.core.normalizers.phone import PhoneNormalizer


@pytest.fixture
def normalizer():
    return PhoneNormalizer()


def test_us_number(normalizer):
    r = normalizer.normalize("(206) 555-1234")
    assert r.value == "+12065551234"
    assert r.skipped is False


def test_already_e164(normalizer):
    r = normalizer.normalize("+12065551234")
    assert r.value == "+12065551234"


def test_dotted_format(normalizer):
    r = normalizer.normalize("206.555.1234")
    assert r.value == "+12065551234"


def test_null_like_returns_skipped(normalizer):
    for v in (None, "", "N/A", "n/a"):
        r = normalizer.normalize(v)
        assert r.skipped is True
        assert r.value is None


def test_invalid_raises(normalizer):
    with pytest.raises(ValueError, match="invalid phone"):
        normalizer.normalize("not-a-phone")


def test_local_number_raises(normalizer):
    """7-digit local numbers without area code are not valid."""
    with pytest.raises(ValueError, match="invalid phone"):
        normalizer.normalize("555-1234")
```

- [ ] **Step 2: Verify failure**

```bash
uv run pytest tests/core/normalizers/test_phone.py -v
```

- [ ] **Step 3: Implement**

Create `src/core/normalizers/phone.py`:

```python
"""Phone number normalizer — E.164 output via libphonenumber."""

from dataclasses import dataclass

import phonenumbers

from src.core.normalizers.base import NormalizationResult, is_null_like


@dataclass
class PhoneNormalizer:
    """Normalizes raw phone strings to E.164 format.

    Args:
        default_region: ISO 3166-1 alpha-2 hint for numbers without country code.
    """

    default_region: str = "US"

    def normalize(self, raw: str | None) -> NormalizationResult:
        """Return E.164 string, or skipped result for null-like input.

        Raises:
            ValueError: If *raw* is not a parseable, valid phone number.
        """
        if is_null_like(raw):
            return NormalizationResult(value=None, skipped=True)
        raw = raw.strip()
        try:
            parsed = phonenumbers.parse(raw, self.default_region)
        except phonenumbers.NumberParseException:
            raise ValueError(f"invalid phone number: {raw!r}")
        if not phonenumbers.is_valid_number(parsed):
            raise ValueError(f"invalid phone number: {raw!r}")
        return NormalizationResult(
            value=phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164),
        )
```

- [ ] **Step 4: Run tests**

```bash
uv run pytest tests/core/normalizers/test_phone.py -v
```

- [ ] **Step 5: Commit**

```bash
git add src/core/normalizers/phone.py tests/core/normalizers/test_phone.py
git commit -m "#5 feat: add PhoneNormalizer"
```

---

### Task 5: Email normalizer

**Files:**
- Create: `src/core/normalizers/email.py`
- Create: `tests/core/normalizers/test_email.py`

- [ ] **Step 1: Write failing tests**

Create `tests/core/normalizers/test_email.py`:

```python
"""Tests for EmailNormalizer."""

import pytest

from src.core.normalizers.email import EmailNormalizer


@pytest.fixture
def normalizer():
    return EmailNormalizer()


def test_valid_email(normalizer):
    r = normalizer.normalize("user@example.com")
    assert r.value == "user@example.com"
    assert r.skipped is False


def test_normalizes_domain_case(normalizer):
    r = normalizer.normalize("User@Example.COM")
    assert r.value.endswith("@example.com")


def test_null_like_returns_skipped(normalizer):
    for v in (None, "", "N/A"):
        r = normalizer.normalize(v)
        assert r.skipped is True


def test_invalid_raises(normalizer):
    with pytest.raises(ValueError, match="invalid email"):
        normalizer.normalize("not-an-email")


def test_empty_domain_raises(normalizer):
    with pytest.raises(ValueError, match="invalid email"):
        normalizer.normalize("user@")
```

- [ ] **Step 2: Verify failure**

```bash
uv run pytest tests/core/normalizers/test_email.py -v
```

- [ ] **Step 3: Implement**

Create `src/core/normalizers/email.py`:

```python
"""Email address normalizer — validates and lowercases domain."""

from dataclasses import dataclass

from email_validator import EmailNotValidError
from email_validator import validate_email as _ev_validate

from src.core.normalizers.base import NormalizationResult, is_null_like


@dataclass
class EmailNormalizer:
    """Validates email addresses and normalizes domain to lowercase."""

    def normalize(self, raw: str | None) -> NormalizationResult:
        """Return normalized email, or skipped result for null-like input.

        Raises:
            ValueError: If *raw* is not a valid email address.
        """
        if is_null_like(raw):
            return NormalizationResult(value=None, skipped=True)
        raw = raw.strip()
        try:
            info = _ev_validate(raw, check_deliverability=False)
            return NormalizationResult(value=info.normalized)
        except EmailNotValidError as exc:
            raise ValueError(f"invalid email address: {raw!r}") from exc
```

- [ ] **Step 4: Run tests**

```bash
uv run pytest tests/core/normalizers/test_email.py -v
```

- [ ] **Step 5: Commit**

```bash
git add src/core/normalizers/email.py tests/core/normalizers/test_email.py
git commit -m "#5 feat: add EmailNormalizer"
```

---

### Task 6: URL normalizer

**Files:**
- Create: `src/core/normalizers/url.py`
- Create: `tests/core/normalizers/test_url.py`

- [ ] **Step 1: Write failing tests**

Create `tests/core/normalizers/test_url.py`:

```python
"""Tests for UrlNormalizer."""

import pytest

from src.core.normalizers.url import UrlNormalizer


@pytest.fixture
def normalizer():
    return UrlNormalizer()


def test_valid_https(normalizer):
    r = normalizer.normalize("https://example.com")
    assert r.value == "https://example.com"
    assert r.skipped is False


def test_lowercases_scheme_and_host(normalizer):
    r = normalizer.normalize("HTTPS://Example.COM/path")
    assert r.value.startswith("https://example.com")


def test_strips_trailing_slash_on_root(normalizer):
    r = normalizer.normalize("https://example.com/")
    assert r.value == "https://example.com"


def test_preserves_path(normalizer):
    r = normalizer.normalize("https://example.com/path/to/page")
    assert "/path/to/page" in r.value


def test_null_like_skipped(normalizer):
    for v in (None, "", "N/A"):
        r = normalizer.normalize(v)
        assert r.skipped is True


def test_invalid_raises(normalizer):
    with pytest.raises(ValueError, match="invalid url"):
        normalizer.normalize("not a url")


def test_bare_domain_raises(normalizer):
    with pytest.raises(ValueError, match="invalid url"):
        normalizer.normalize("example.com")
```

- [ ] **Step 2: Verify failure**

```bash
uv run pytest tests/core/normalizers/test_url.py -v
```

- [ ] **Step 3: Implement**

Create `src/core/normalizers/url.py`:

```python
"""URL normalizer — validates and canonicalizes web URLs."""

from dataclasses import dataclass
from urllib.parse import urlparse, urlunparse

import validators

from src.core.normalizers.base import NormalizationResult, is_null_like


@dataclass
class UrlNormalizer:
    """Validates and canonicalizes URLs (scheme lowercase, host lowercase, no trailing slash)."""

    def normalize(self, raw: str | None) -> NormalizationResult:
        """Return canonical URL string, or skipped result for null-like input.

        Raises:
            ValueError: If *raw* is not a valid URL.
        """
        if is_null_like(raw):
            return NormalizationResult(value=None, skipped=True)
        raw = raw.strip()
        if not validators.url(raw):
            raise ValueError(f"invalid url: {raw!r}")
        parsed = urlparse(raw)
        canonical = urlunparse((
            parsed.scheme.lower(),
            parsed.netloc.lower(),
            parsed.path.rstrip("/") if parsed.path != "/" else "",
            parsed.params,
            parsed.query,
            parsed.fragment,
        ))
        return NormalizationResult(value=canonical)
```

- [ ] **Step 4: Run tests**

```bash
uv run pytest tests/core/normalizers/test_url.py -v
```

- [ ] **Step 5: Commit**

```bash
git add src/core/normalizers/url.py tests/core/normalizers/test_url.py
git commit -m "#5 feat: add UrlNormalizer"
```

---

### Task 7: Identifier normalizer

**Files:**
- Create: `src/core/normalizers/identifier.py`
- Create: `tests/core/normalizers/test_identifier.py`

Identifiers (UBI, WA PDC IDs, etc.) are stored as-is after stripping whitespace — no format transformation, just presence validation.

- [ ] **Step 1: Write failing tests**

Create `tests/core/normalizers/test_identifier.py`:

```python
"""Tests for IdentifierNormalizer."""

import pytest

from src.core.normalizers.identifier import IdentifierNormalizer


@pytest.fixture
def normalizer():
    return IdentifierNormalizer()


def test_strips_whitespace(normalizer):
    r = normalizer.normalize("  603 123 456  ")
    assert r.value == "603 123 456"


def test_null_like_skipped(normalizer):
    for v in (None, "", "N/A"):
        r = normalizer.normalize(v)
        assert r.skipped is True


def test_valid_ubi(normalizer):
    r = normalizer.normalize("603 123 456")
    assert r.value == "603 123 456"
    assert r.skipped is False
```

- [ ] **Step 2: Verify failure**

```bash
uv run pytest tests/core/normalizers/test_identifier.py -v
```

- [ ] **Step 3: Implement**

Create `src/core/normalizers/identifier.py`:

```python
"""Identifier normalizer — strips whitespace, checks presence."""

from dataclasses import dataclass

from src.core.normalizers.base import NormalizationResult, is_null_like


@dataclass
class IdentifierNormalizer:
    """Normalizes identifier strings (UBI, WA PDC IDs, etc.) by stripping whitespace."""

    def normalize(self, raw: str | None) -> NormalizationResult:
        """Return stripped identifier, or skipped result for null-like input."""
        if is_null_like(raw):
            return NormalizationResult(value=None, skipped=True)
        return NormalizationResult(value=raw.strip())
```

- [ ] **Step 4: Run tests**

```bash
uv run pytest tests/core/normalizers/test_identifier.py -v
```

- [ ] **Step 5: Commit**

```bash
git add src/core/normalizers/identifier.py tests/core/normalizers/test_identifier.py
git commit -m "#5 feat: add IdentifierNormalizer"
```

---

### Task 8: Address normalizer

**Files:**
- Create: `src/core/normalizers/address.py`
- Create: `tests/core/normalizers/test_address.py`

Three classes:
- `LocalAddressNormalizer` — sync, uses `usaddress.tag()`; `validation_status='not_attempted'`
- `ExternalAddressNormalizer` — async, calls address-validator API; configurable `/standardize` vs `/validate`
- `FallbackAddressNormalizer` — async, tries external first, falls back to local on 5xx/timeout/429-exhausted

- [ ] **Step 1: Write failing tests**

Create `tests/core/normalizers/test_address.py`:

```python
"""Tests for address normalizers."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.core.normalizers.address import (
    AddressNormalizerConfig,
    ExternalAddressNormalizer,
    FallbackAddressNormalizer,
    LocalAddressNormalizer,
)
from src.core.normalizers.base import NormalizationResult


# ---------------------------------------------------------------------------
# LocalAddressNormalizer
# ---------------------------------------------------------------------------

def test_local_null_like_skipped():
    n = LocalAddressNormalizer()
    r = n.normalize(None)
    assert r.skipped is True


def test_local_parses_address():
    n = LocalAddressNormalizer()
    r = n.normalize("123 Main St, Seattle WA 98101")
    assert r.skipped is False
    assert r.value is not None
    assert r.value["raw_input"] == "123 Main St, Seattle WA 98101"
    assert r.validation_detail == {"provider": "usaddress", "status": "not_attempted"}


def test_local_ambiguous_stores_raw_only():
    """usaddress.RepeatedLabelError → store raw_input only with a warning."""
    n = LocalAddressNormalizer()
    # This input is known to be ambiguous to usaddress
    r = n.normalize("123 Main 456 Oak St")
    assert r.value["raw_input"] is not None
    # May or may not have parsed components — should not raise


# ---------------------------------------------------------------------------
# ExternalAddressNormalizer
# ---------------------------------------------------------------------------

@pytest.fixture
def config():
    return AddressNormalizerConfig(api_key="test-key", run_validation=False)


@pytest.fixture
def config_with_validation():
    return AddressNormalizerConfig(api_key="test-key", run_validation=True)


@pytest.fixture
def external(config):
    return ExternalAddressNormalizer(config)


@pytest.fixture
def external_validate(config_with_validation):
    return ExternalAddressNormalizer(config_with_validation)


async def test_external_null_like_skipped(external):
    r = await external.normalize(None)
    assert r.skipped is True


async def test_external_standardize_success(external):
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "address_line_1": "123 MAIN ST",
        "address_line_2": None,
        "city": "SEATTLE",
        "region": "WA",
        "postal_code": "98101",
        "country": "US",
        "standardized": "123 MAIN ST SEATTLE WA 98101",
        "components": {},
        "warnings": [],
    }
    with patch("httpx.AsyncClient.post", new=AsyncMock(return_value=mock_response)):
        r = await external.normalize("123 Main St, Seattle WA 98101")
    assert r.value["standardized"] == "123 MAIN ST SEATTLE WA 98101"
    assert r.validation_detail["provider"] == "address-validator"


async def test_external_validate_endpoint_used_when_configured(external_validate):
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "address_line_1": "123 MAIN ST",
        "address_line_2": None,
        "city": "SEATTLE",
        "region": "WA",
        "postal_code": "98101",
        "country": "US",
        "validated": "123 MAIN ST  SEATTLE WA 98101",
        "components": {},
        "warnings": [],
        "validation": {
            "status": "confirmed",
            "dpv_match_code": "Y",
            "provider": "usps",
        },
    }
    with patch("httpx.AsyncClient.post", new=AsyncMock(return_value=mock_response)) as mock_post:
        r = await external_validate.normalize("123 Main St, Seattle WA 98101")
    called_url = mock_post.call_args[0][0]
    assert "/validate" in called_url
    assert r.confidence_hint == "confirmed"


async def test_external_429_retries_then_raises(external):
    mock_response = MagicMock()
    mock_response.status_code = 429
    mock_response.headers = {"Retry-After": "0"}  # 0s for fast tests
    with patch("httpx.AsyncClient.post", new=AsyncMock(return_value=mock_response)):
        with pytest.raises(RuntimeError, match="rate limit"):
            await external.normalize("123 Main St, Seattle WA 98101")


# ---------------------------------------------------------------------------
# FallbackAddressNormalizer
# ---------------------------------------------------------------------------

async def test_fallback_uses_external_on_success(config):
    n = FallbackAddressNormalizer(config)
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "address_line_1": "123 MAIN ST",
        "address_line_2": None,
        "city": "SEATTLE",
        "region": "WA",
        "postal_code": "98101",
        "country": "US",
        "standardized": "123 MAIN ST SEATTLE WA 98101",
        "components": {},
        "warnings": [],
    }
    with patch("httpx.AsyncClient.post", new=AsyncMock(return_value=mock_response)):
        r = await n.normalize("123 Main St, Seattle WA 98101")
    assert r.validation_detail["provider"] == "address-validator"


async def test_fallback_uses_local_on_service_error(config):
    n = FallbackAddressNormalizer(config)
    with patch("httpx.AsyncClient.post", new=AsyncMock(side_effect=Exception("timeout"))):
        r = await n.normalize("123 Main St, Seattle WA 98101")
    assert r.validation_detail["provider"] == "usaddress"
    assert "fallback" in r.warnings[0].lower()
```

- [ ] **Step 2: Verify failure**

```bash
uv run pytest tests/core/normalizers/test_address.py -v
```

- [ ] **Step 3: Implement**

Create `src/core/normalizers/address.py`:

```python
"""Address normalizers: local (usaddress), external (address-validator API), and fallback."""

import asyncio
from dataclasses import dataclass, field

import httpx
import usaddress

from src.core.normalizers.base import NormalizationResult, is_null_like

# ValidationResult.status → field_confidence.validation_status
_STATUS_MAP = {
    "confirmed": "confirmed",
    "confirmed_missing_secondary": "confirmed",
    "confirmed_bad_secondary": "confirmed",
    "not_confirmed": "failed",
    "unavailable": "not_attempted",
}


@dataclass
class AddressNormalizerConfig:
    """Configuration for the external address normalizer.

    Args:
        api_key: Value for the X-API-Key header.
        base_url: Base URL of the address-validator service.
        run_validation: If True, call /validate (includes standardization).
                        If False, call /standardize only.
        max_retries: Max 429 retry attempts before giving up.
    """

    api_key: str
    base_url: str = "https://address-validator.exe.xyz:8000"
    run_validation: bool = False
    max_retries: int = 3


@dataclass
class LocalAddressNormalizer:
    """Parses addresses locally using usaddress. Never calls external services.

    Always produces validation_status='not_attempted'.
    """

    def normalize(self, raw: str | None) -> NormalizationResult:
        """Parse *raw* into address components. Skips null-like input."""
        if is_null_like(raw):
            return NormalizationResult(value=None, skipped=True)
        raw = raw.strip()
        result: dict = {"raw_input": raw}
        try:
            tagged, _ = usaddress.tag(raw)
            result.update({
                "address_line_1": _build_line1(tagged),
                "address_line_2": tagged.get("OccupancyType") and _build_line2(tagged),
                "city": tagged.get("PlaceName"),
                "region": tagged.get("StateName"),
                "postal_code": tagged.get("ZipCode"),
                "country": "US",
                "standardized": None,
            })
        except usaddress.RepeatedLabelError:
            return NormalizationResult(
                value=result,
                warnings=["address parse ambiguous; stored raw_input only"],
                validation_detail={"provider": "usaddress", "status": "not_attempted"},
            )
        return NormalizationResult(
            value=result,
            validation_detail={"provider": "usaddress", "status": "not_attempted"},
        )


def _build_line1(tagged: dict) -> str | None:
    parts = [
        tagged.get("AddressNumber"),
        tagged.get("StreetNamePreDirectional"),
        tagged.get("StreetName"),
        tagged.get("StreetNamePostType"),
        tagged.get("StreetNamePostDirectional"),
    ]
    line = " ".join(p for p in parts if p)
    return line or None


def _build_line2(tagged: dict) -> str | None:
    parts = [tagged.get("OccupancyType"), tagged.get("OccupancyIdentifier")]
    line = " ".join(p for p in parts if p)
    return line or None


@dataclass
class ExternalAddressNormalizer:
    """Calls the address-validator API to standardize or validate addresses.

    Endpoint selection:
      - config.run_validation=False → POST /api/v1/standardize
      - config.run_validation=True  → POST /api/v1/validate (includes standardization)

    429 handling: reads Retry-After header, sleeps, retries up to config.max_retries.
    Raises RuntimeError if retry budget is exhausted.
    """

    config: AddressNormalizerConfig

    async def normalize(self, raw: str | None) -> NormalizationResult:
        """Standardize or validate *raw* via the external API."""
        if is_null_like(raw):
            return NormalizationResult(value=None, skipped=True)
        raw = raw.strip()
        endpoint = "validate" if self.config.run_validation else "standardize"
        url = f"{self.config.base_url}/api/v1/{endpoint}"
        payload = {"address": raw, "country": "US"}
        headers = {"X-API-Key": self.config.api_key}

        for attempt in range(self.config.max_retries + 1):
            async with httpx.AsyncClient() as client:
                response = await client.post(url, json=payload, headers=headers)
            if response.status_code == 429:
                if attempt >= self.config.max_retries:
                    raise RuntimeError(
                        f"address-validator rate limit: exhausted {self.config.max_retries} retries"
                    )
                wait = float(response.headers.get("Retry-After", "1"))
                await asyncio.sleep(wait)
                continue
            response.raise_for_status()
            data = response.json()
            return self._parse_response(raw, data)

        raise RuntimeError("address-validator: retry loop exited unexpectedly")

    def _parse_response(self, raw: str, data: dict) -> NormalizationResult:
        result = {
            "raw_input": raw,
            "address_line_1": data.get("address_line_1"),
            "address_line_2": data.get("address_line_2"),
            "city": data.get("city"),
            "region": data.get("region"),
            "postal_code": data.get("postal_code"),
            "country": data.get("country", "US"),
            "standardized": data.get("standardized") or data.get("validated"),
        }
        detail: dict = {"provider": "address-validator"}
        confidence_hint = "unconfirmed"
        if self.config.run_validation and "validation" in data:
            v = data["validation"]
            detail.update({
                "status": v.get("status"),
                "dpv_match_code": v.get("dpv_match_code"),
                "provider": v.get("provider", "address-validator"),
            })
            confidence_hint = _STATUS_MAP.get(v.get("status", ""), "not_attempted")
        detail["warnings"] = data.get("warnings", [])
        warnings = [f"address-validator warning: {w}" for w in data.get("warnings", [])]
        return NormalizationResult(
            value=result,
            warnings=warnings,
            confidence_hint=confidence_hint,
            validation_detail=detail,
        )


@dataclass
class FallbackAddressNormalizer:
    """Tries ExternalAddressNormalizer; falls back to LocalAddressNormalizer on any error.

    Use this in production pipelines. Pass config=None to always use local.
    """

    config: AddressNormalizerConfig | None = None
    _local: LocalAddressNormalizer = field(default_factory=LocalAddressNormalizer, init=False)

    async def normalize(self, raw: str | None) -> NormalizationResult:
        """Normalize *raw* via external service, with local fallback."""
        if self.config is None or is_null_like(raw):
            return self._local.normalize(raw)
        try:
            external = ExternalAddressNormalizer(self.config)
            return await external.normalize(raw)
        except Exception as exc:
            result = self._local.normalize(raw)
            result.warnings.insert(0, f"fallback to local address parser: {exc}")
            return result
```

- [ ] **Step 4: Run tests**

```bash
uv run pytest tests/core/normalizers/test_address.py -v
```

- [ ] **Step 5: Commit**

```bash
git add src/core/normalizers/address.py tests/core/normalizers/test_address.py
git commit -m "#5 feat: add address normalizers (local, external, fallback)"
```

---

### Task 9: Migrate phone/email out of db.py; clean up tests

**Files:**
- Modify: `src/core/db.py`
- Modify: `tests/core/test_db.py`

- [ ] **Step 1: Remove phone/email from db.py**

Remove from `src/core/db.py`:
- The `import phonenumbers` line
- The `from email_validator import ...` lines
- The `normalize_phone()` function and its docstring
- The `validate_email()` function and its docstring
- The corresponding comment blocks (`# Phone normalisation`, `# Email validation`)

- [ ] **Step 2: Remove phone/email tests from test_db.py**

Remove from `tests/core/test_db.py`:
- `normalize_phone` and `validate_email` from the import line
- The `E164_RE` constant
- All `test_normalize_phone_*` functions
- All `test_validate_email_*` functions

Update the import to only import `generate_id`:
```python
from src.core.db import generate_id
```

- [ ] **Step 3: Run all non-integration tests to verify nothing broken**

```bash
uv run pytest tests/ -v
```

Expected: all pass (phone/email tests now live in `tests/core/normalizers/`).

- [ ] **Step 4: Commit**

```bash
git add src/core/db.py tests/core/test_db.py
git commit -m "#5 refactor: move normalize_phone, validate_email to src.core.normalizers"
```

---

### Task 10: Ingestion base types

**Files:**
- Create: `src/core/ingestion/base.py`
- Create: `src/core/ingestion/__init__.py`
- Create: `tests/core/ingestion/test_base.py`

- [ ] **Step 1: Write failing tests**

Create `tests/core/ingestion/test_base.py`:

```python
"""Tests for src.core.ingestion.base."""

from src.core.ingestion.base import ConfidenceRecord, FieldError, RowResult, value_hash


def test_value_hash_deterministic():
    assert value_hash("foo") == value_hash("foo")


def test_value_hash_different_inputs():
    assert value_hash("foo") != value_hash("bar")


def test_value_hash_is_hex_string():
    h = value_hash("test")
    assert len(h) == 64  # SHA-256 hex
    int(h, 16)  # raises if not hex


def test_row_result_ok_no_errors():
    r = RowResult(source_row=1, raw={"Name": "Acme"})
    assert r.ok is True


def test_row_result_ok_false_with_errors():
    r = RowResult(source_row=1, raw={}, errors=[FieldError(field="name", message="required")])
    assert r.ok is False


def test_confidence_record_fields():
    cr = ConfidenceRecord(
        entity_type="organization",
        entity_id="01ABC",
        field_name="phone",
        normalized_value="+12065551234",
        source_reliability=0.8,
        validation_status="unconfirmed",
        assessed_by="import:batch01",
    )
    assert cr.source_reliability == 0.8
```

- [ ] **Step 2: Verify failure**

```bash
uv run pytest tests/core/ingestion/test_base.py -v
```

- [ ] **Step 3: Implement**

Create `src/core/ingestion/__init__.py` (empty).

Create `src/core/ingestion/base.py`:

```python
"""Core types for the EVTL ingestion pipeline."""

import hashlib
from dataclasses import dataclass, field
from typing import Any


def value_hash(value: str) -> str:
    """Return SHA-256 hex digest of *value* (UTF-8 encoded)."""
    return hashlib.sha256(value.encode()).hexdigest()


@dataclass
class FieldError:
    """A validation or normalization failure on a single field."""

    field: str
    message: str
    raw_value: str | None = None


@dataclass
class ConfidenceRecord:
    """Represents one row to be inserted into field_confidence.

    Created during Transform; written to DB during Load (after entity_id is known).
    value_hash is computed at insert time from normalized_value.
    """

    entity_type: str
    entity_id: str
    field_name: str
    normalized_value: str          # the actual stored value; hashed at insert time
    source_reliability: float
    validation_status: str         # 'confirmed'|'unconfirmed'|'failed'|'not_attempted'
    assessed_by: str
    validation_detail: dict | None = None


@dataclass
class RowResult:
    """Envelope for a single CSV row through the EVTL pipeline.

    errors: fatal field errors — if non-empty, entity will not be loaded.
    warnings: non-fatal issues — entity loads, but affected field is skipped.
    transformed: populated by the Transform phase if ok is True.
    """

    source_row: int
    raw: dict[str, str]
    errors: list[FieldError] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    transformed: dict[str, Any] | None = None

    @property
    def ok(self) -> bool:
        """True if the entity can be loaded (no fatal errors)."""
        return not self.errors
```

- [ ] **Step 4: Run tests**

```bash
uv run pytest tests/core/ingestion/test_base.py -v
```

- [ ] **Step 5: Commit**

```bash
git add src/core/ingestion/ tests/core/ingestion/test_base.py
git commit -m "#5 feat: add ingestion base types (RowResult, FieldError, ConfidenceRecord)"
```

---

### Task 11: Org source (extract / validate / transform)

**Files:**
- Create: `src/core/ingestion/sources/__init__.py`
- Create: `src/core/ingestion/sources/csv_org.py`
- Create: `tests/core/ingestion/test_csv_org.py`
- Create: `tests/fixtures/ingestion/orgs_sample.csv`

Column mapping (verify against actual CSV headers before implementing):

| CSV Header | DB target |
|---|---|
| `Name` | `organization_names` (legal, is_canonical=True) — **required** |
| `Parent Organization` | `organizations.parent_id` via name lookup |
| `Acronym` | `organization_names` (acronym, is_canonical=True) |
| `Active?` | `organizations.active` (parse "Yes"/"No"/"TRUE"/"FALSE"/"1"/"0") |
| `UBI` | `identifiers` (slug=org_ubi) |
| `WSLCB License` | `identifiers` (slug=org_wslcb) |
| `WA PDC` | `identifiers` (slug=org_wa_pdc) |
| `SEC Form D` | `urls` (url_type_slug=sec_form_d) |
| `Primary URL` | `urls` (url_type_slug=website, is_canonical=True) |
| `Organization URL` | `urls` (url_type_slug=website, is_canonical=False) |
| `LinkedIn URL` | `social_links` (platform_slug=linkedin) |
| `Twitter URL` | `social_links` (platform_slug=twitter) |
| `Bluesky URL` | `social_links` (platform_slug=bluesky) |
| `Mastodon URL` | `social_links` (platform_slug=mastodon) |
| `Instagram URL` | `social_links` (platform_slug=instagram) |
| `Facebook URL` | `social_links` (platform_slug=facebook) |
| `YouTube URL` | `social_links` (platform_slug=youtube) |
| `Flickr URL` | `social_links` (platform_slug=flickr) |
| `Email Address` | `contact_methods` (contact_type=email) |
| `Phone` | `contact_methods` (contact_type=phone) |
| `Mailing Address` | `addresses` + `entity_addresses` (address_type=mailing) |
| `Notes` | `organizations.notes` |
| `Google Drive` | `urls` (url_type_slug=google_drive) |

- [ ] **Step 1: Create fixture CSV**

Create `tests/fixtures/ingestion/orgs_sample.csv`:

```
Name,Parent Organization,Acronym,Active?,UBI,WSLCB License,WA PDC,SEC Form D,Primary URL,Organization URL,LinkedIn URL,Twitter URL,Bluesky URL,Mastodon URL,Instagram URL,Facebook URL,YouTube URL,Flickr URL,Email Address,Phone,Mailing Address,Notes,Google Drive
Acme Cannabis LLC,,AC,Yes,603 123 456,,,,https://acme.example.com,,,https://twitter.com/acme,,,,,,,,info@acme.example.com,(206) 555-1234,"123 Main St, Seattle WA 98101","Test org",
Child Org,Acme Cannabis LLC,,Yes,,,,,,,,,,,,,,,,,,,"Child of Acme",
Bad Phone Org,,,Yes,,,,,,,,,,,,,,,,not-a-phone,,,
```

- [ ] **Step 2: Write failing tests**

Create `tests/core/ingestion/test_csv_org.py`:

```python
"""Tests for csv_org source module."""

import csv
import io
from pathlib import Path

import pytest

from src.core.ingestion.sources.csv_org import validate_org, transform_org


FIXTURE = Path("tests/fixtures/ingestion/orgs_sample.csv")


def read_fixture(path: Path) -> list[dict[str, str]]:
    rows = []
    with path.open(newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            rows.append({k: v.strip() for k, v in row.items()})
    return rows


def test_validate_org_valid_row():
    rows = read_fixture(FIXTURE)
    result = validate_org(rows[0], source_row=2)
    assert result.ok
    assert result.errors == []


def test_validate_org_missing_name():
    result = validate_org({"Name": "", "Active?": "Yes"}, source_row=99)
    assert not result.ok
    assert any(e.field == "name" for e in result.errors)


def test_validate_org_empty_row():
    result = validate_org({}, source_row=99)
    assert not result.ok


async def test_transform_org_generates_id():
    rows = read_fixture(FIXTURE)
    r = validate_org(rows[0], source_row=2)
    org_index: dict[str, str] = {}
    result = await transform_org(r, org_index=org_index, source_reliability=0.8)
    assert result.ok
    assert "org_id" in result.transformed
    assert len(result.transformed["org_id"]) == 26


async def test_transform_org_canonical_name():
    rows = read_fixture(FIXTURE)
    r = validate_org(rows[0], source_row=2)
    result = await transform_org(r, org_index={}, source_reliability=0.8)
    names = result.transformed["names"]
    legal = next(n for n in names if n["name_type"] == "legal")
    assert legal["name"] == "Acme Cannabis LLC"
    assert legal["is_canonical"] is True


async def test_transform_org_acronym():
    rows = read_fixture(FIXTURE)
    r = validate_org(rows[0], source_row=2)
    result = await transform_org(r, org_index={}, source_reliability=0.8)
    names = result.transformed["names"]
    acronym = next((n for n in names if n["name_type"] == "acronym"), None)
    assert acronym is not None
    assert acronym["name"] == "AC"


async def test_transform_org_parent_resolved():
    parent_id = "01TESTPARENTID00000000000A"
    rows = read_fixture(FIXTURE)
    child_row = rows[1]  # "Child Org" with Parent Organization = "Acme Cannabis LLC"
    r = validate_org(child_row, source_row=3)
    org_index = {"acme cannabis llc": parent_id}
    result = await transform_org(r, org_index=org_index, source_reliability=0.8)
    assert result.transformed["parent_id"] == parent_id


async def test_transform_org_unresolved_parent_warning():
    rows = read_fixture(FIXTURE)
    child_row = rows[1]
    r = validate_org(child_row, source_row=3)
    org_index: dict[str, str] = {}  # empty — parent not found
    result = await transform_org(r, org_index=org_index, source_reliability=0.8)
    assert result.ok  # entity still loads, warning logged
    assert any("parent" in w.lower() for w in result.warnings)
    assert result.transformed["parent_id"] is None


async def test_transform_org_bad_phone_warning():
    rows = read_fixture(FIXTURE)
    bad_row = rows[2]  # "Bad Phone Org"
    r = validate_org(bad_row, source_row=4)
    result = await transform_org(r, org_index={}, source_reliability=0.8)
    assert result.ok
    contact_methods = result.transformed["contact_methods"]
    assert not any(cm["contact_type"] == "phone" for cm in contact_methods)
    assert any("phone" in w.lower() for w in result.warnings)


async def test_transform_org_confidence_records():
    rows = read_fixture(FIXTURE)
    r = validate_org(rows[0], source_row=2)
    result = await transform_org(r, org_index={}, source_reliability=0.8)
    records = result.transformed["confidence_records"]
    assert len(records) > 0
    for rec in records:
        assert rec.source_reliability == pytest.approx(0.8)
        assert rec.entity_id == result.transformed["org_id"]
```

- [ ] **Step 3: Verify failure**

```bash
uv run pytest tests/core/ingestion/test_csv_org.py -v
```

- [ ] **Step 4: Implement**

Create `src/core/ingestion/sources/__init__.py` (empty).

Create `src/core/ingestion/sources/csv_org.py`:

```python
"""Extract, validate, and transform rows from Organizations.csv."""

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from src.core.db import generate_id
from src.core.ingestion.base import ConfidenceRecord, FieldError, RowResult
from src.core.normalizers.address import FallbackAddressNormalizer
from src.core.normalizers.email import EmailNormalizer
from src.core.normalizers.identifier import IdentifierNormalizer
from src.core.normalizers.phone import PhoneNormalizer
from src.core.normalizers.url import UrlNormalizer

_phone = PhoneNormalizer()
_email = EmailNormalizer()
_url = UrlNormalizer()
_identifier = IdentifierNormalizer()
_address = FallbackAddressNormalizer()  # no config → local only; set config for external


class OrgRow(BaseModel):
    """Pydantic model for a single Organizations.csv row.

    Field aliases match the CSV column headers exactly.
    """

    model_config = ConfigDict(populate_by_name=True)

    name: str = Field(alias="Name")
    parent_organization: str | None = Field(None, alias="Parent Organization")
    acronym: str | None = Field(None, alias="Acronym")
    active: str | None = Field(None, alias="Active?")
    ubi: str | None = Field(None, alias="UBI")
    wslcb_license: str | None = Field(None, alias="WSLCB License")
    wa_pdc: str | None = Field(None, alias="WA PDC")
    sec_form_d: str | None = Field(None, alias="SEC Form D")
    primary_url: str | None = Field(None, alias="Primary URL")
    org_url: str | None = Field(None, alias="Organization URL")
    linkedin_url: str | None = Field(None, alias="LinkedIn URL")
    twitter_url: str | None = Field(None, alias="Twitter URL")
    bluesky_url: str | None = Field(None, alias="Bluesky URL")
    mastodon_url: str | None = Field(None, alias="Mastodon URL")
    instagram_url: str | None = Field(None, alias="Instagram URL")
    facebook_url: str | None = Field(None, alias="Facebook URL")
    youtube_url: str | None = Field(None, alias="YouTube URL")
    flickr_url: str | None = Field(None, alias="Flickr URL")
    email: str | None = Field(None, alias="Email Address")
    phone: str | None = Field(None, alias="Phone")
    mailing_address: str | None = Field(None, alias="Mailing Address")
    notes: str | None = Field(None, alias="Notes")
    google_drive: str | None = Field(None, alias="Google Drive")

    @model_validator(mode="before")
    @classmethod
    def strip_all(cls, data: dict) -> dict:
        return {k: (v.strip() if isinstance(v, str) else v) for k, v in data.items()}

    @field_validator("name")
    @classmethod
    def name_required(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("name is required")
        return v.strip()


def validate_org(raw: dict[str, str], source_row: int) -> RowResult:
    """Validate a raw CSV row dict. Returns RowResult with errors if name is missing."""
    result = RowResult(source_row=source_row, raw=raw)
    try:
        validated = OrgRow.model_validate(raw)
        result.transformed = {"_validated": validated}
    except ValidationError as exc:
        for e in exc.errors():
            result.errors.append(FieldError(
                field=str(e["loc"][0]) if e["loc"] else "unknown",
                message=e["msg"],
                raw_value=raw.get(str(e["loc"][0])),
            ))
    return result


async def transform_org(
    result: RowResult,
    org_index: dict[str, str],
    source_reliability: float,
    address_normalizer: FallbackAddressNormalizer | None = None,
) -> RowResult:
    """Transform a validated org row into DB-ready dicts. Calls address normalizer (async)."""
    if not result.ok:
        return result

    validated: OrgRow = result.transformed["_validated"]
    org_id = generate_id()
    warnings: list[str] = []
    confidence_records: list[ConfidenceRecord] = []

    def _add_confidence(field_name: str, normalized_value: str, hint: str, detail: dict | None = None) -> None:
        confidence_records.append(ConfidenceRecord(
            entity_type="organization",
            entity_id=org_id,
            field_name=field_name,
            normalized_value=normalized_value,
            source_reliability=source_reliability,
            validation_status=hint,
            assessed_by="import:pending",  # batch_id filled in by pipeline
            validation_detail=detail,
        ))

    # Active flag
    active = _parse_active(validated.active)

    # Parent org lookup
    parent_id: str | None = None
    if validated.parent_organization:
        parent_id = org_index.get(validated.parent_organization.strip().lower())
        if parent_id is None:
            warnings.append(f"parent org not found: {validated.parent_organization!r}")

    # Names
    names = [{"name": validated.name, "name_type": "legal", "is_canonical": True}]
    if validated.acronym and validated.acronym.strip():
        names.append({"name": validated.acronym.strip(), "name_type": "acronym", "is_canonical": True})

    # Contact methods
    contact_methods: list[dict] = []
    if validated.email:
        try:
            r = _email.normalize(validated.email)
            if not r.skipped:
                contact_methods.append({"contact_type": "email", "value": r.value})
                _add_confidence("email", r.value, r.confidence_hint)
        except ValueError as exc:
            warnings.append(f"email skipped: {exc}")

    if validated.phone:
        try:
            r = _phone.normalize(validated.phone)
            if not r.skipped:
                contact_methods.append({"contact_type": "phone", "value": r.value})
                _add_confidence("phone", r.value, r.confidence_hint)
        except ValueError as exc:
            warnings.append(f"phone skipped: {exc}")

    # URLs
    urls: list[dict] = []
    _url_fields = [
        (validated.primary_url, "website", True),
        (validated.org_url, "website", False),
        (validated.sec_form_d, "sec_form_d", False),
        (validated.google_drive, "google_drive", False),
    ]
    for raw_url, url_type_slug, is_canonical in _url_fields:
        if raw_url:
            try:
                r = _url.normalize(raw_url)
                if not r.skipped:
                    urls.append({"url": r.value, "url_type_slug": url_type_slug, "is_canonical": is_canonical})
                    _add_confidence(f"url:{url_type_slug}", r.value, r.confidence_hint)
            except ValueError as exc:
                warnings.append(f"url skipped ({url_type_slug}): {exc}")

    # Social links
    social_links: list[dict] = []
    _social_fields = [
        (validated.linkedin_url, "linkedin"),
        (validated.twitter_url, "twitter"),
        (validated.bluesky_url, "bluesky"),
        (validated.mastodon_url, "mastodon"),
        (validated.instagram_url, "instagram"),
        (validated.facebook_url, "facebook"),
        (validated.youtube_url, "youtube"),
        (validated.flickr_url, "flickr"),
    ]
    for raw_url, platform_slug in _social_fields:
        if raw_url:
            try:
                r = _url.normalize(raw_url)
                if not r.skipped:
                    social_links.append({"platform_slug": platform_slug, "url": r.value})
                    _add_confidence(f"social:{platform_slug}", r.value, r.confidence_hint)
            except ValueError as exc:
                warnings.append(f"social link skipped ({platform_slug}): {exc}")

    # Identifiers
    identifiers: list[dict] = []
    _id_fields = [
        (validated.ubi, "org_ubi"),
        (validated.wslcb_license, "org_wslcb"),
        (validated.wa_pdc, "org_wa_pdc"),
    ]
    for raw_val, slug in _id_fields:
        if raw_val:
            r = _identifier.normalize(raw_val)
            if not r.skipped:
                identifiers.append({"identifier_type_slug": slug, "value": r.value})
                _add_confidence(f"identifier:{slug}", r.value, r.confidence_hint)

    # Address
    addr_normalizer = address_normalizer or _address
    address: dict | None = None
    if validated.mailing_address:
        addr_result = await addr_normalizer.normalize(validated.mailing_address)
        if not addr_result.skipped:
            address = addr_result.value
            _add_confidence("address", str(address.get("standardized") or address.get("raw_input")),
                            addr_result.confidence_hint, addr_result.validation_detail)
            warnings.extend(addr_result.warnings)

    result.transformed = {
        "org_id": org_id,
        "active": active,
        "parent_id": parent_id,
        "notes": validated.notes,
        "names": names,
        "contact_methods": contact_methods,
        "urls": urls,
        "social_links": social_links,
        "identifiers": identifiers,
        "address": address,
        "confidence_records": confidence_records,
    }
    result.warnings = warnings
    return result


def _parse_active(raw: str | None) -> bool:
    if raw is None:
        return True
    return raw.strip().lower() in ("yes", "true", "1", "y")
```

- [ ] **Step 5: Run tests**

```bash
uv run pytest tests/core/ingestion/test_csv_org.py -v
```

- [ ] **Step 6: Commit**

```bash
git add src/core/ingestion/sources/ tests/core/ingestion/test_csv_org.py tests/fixtures/
git commit -m "#5 feat: add org source (validate_org, transform_org)"
```

---

### Task 12: Person source

**Files:**
- Create: `src/core/ingestion/sources/csv_person.py`
- Create: `tests/core/ingestion/test_csv_person.py`
- Create: `tests/fixtures/ingestion/people_sample.csv`

Column mapping:

| CSV Header | DB target |
|---|---|
| `Name` | `person_names` (legal, is_canonical=True) — **required** |
| `Former Name` | `person_names` (former, is_canonical=True) |
| `Personal Email` | `contact_methods` (contact_type=email) |
| `Personal Phone` | `contact_methods` (contact_type=phone) |
| `Personal URL` | `urls` (url_type_slug=website, is_canonical=True) |
| `LinkedIn URL` | `social_links` (platform_slug=linkedin) |
| `Twitter URL` | `social_links` (platform_slug=twitter) |
| `Mastodon URL` | `social_links` (platform_slug=mastodon) |
| `Instagram URL` | `social_links` (platform_slug=instagram) |
| `Wikipedia URL` | `urls` (url_type_slug=wikipedia) |
| `WA PDC` | `identifiers` (slug=person_wa_pdc) |
| `Personal Pronouns` | `people.personal_pronouns` |
| `Notes` | `people.notes` |

- [ ] **Step 1: Create fixture CSV**

Create `tests/fixtures/ingestion/people_sample.csv`:

```
Name,Former Name,Personal Email,Personal Phone,Personal URL,LinkedIn URL,Twitter URL,Mastodon URL,Instagram URL,Wikipedia URL,WA PDC,Personal Pronouns,Notes
Jane Smith,,jane@example.com,(206) 555-5678,https://janesmith.example.com,,https://twitter.com/janesmith,,,,,she/her,Test person
Bob Jones,Robert Jones,bob@example.com,,,,,,,,,,
Bad Email Person,,,,,,,,,,,, bad@,,,
```

- [ ] **Step 2: Write tests**

Create `tests/core/ingestion/test_csv_person.py` following the same pattern as `test_csv_org.py`:

```python
"""Tests for csv_person source module."""

import csv
from pathlib import Path

import pytest

from src.core.ingestion.sources.csv_person import validate_person, transform_person


FIXTURE = Path("tests/fixtures/ingestion/people_sample.csv")


def read_fixture(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8-sig") as f:
        return [{k: v.strip() for k, v in row.items()} for row in csv.DictReader(f)]


def test_validate_person_valid():
    rows = read_fixture(FIXTURE)
    r = validate_person(rows[0], source_row=2)
    assert r.ok


def test_validate_person_missing_name():
    r = validate_person({"Name": ""}, source_row=99)
    assert not r.ok
    assert any(e.field == "name" for e in r.errors)


async def test_transform_person_generates_id():
    rows = read_fixture(FIXTURE)
    r = validate_person(rows[0], source_row=2)
    result = await transform_person(r, source_reliability=0.8)
    assert result.ok
    assert len(result.transformed["person_id"]) == 26


async def test_transform_person_former_name():
    rows = read_fixture(FIXTURE)
    r = validate_person(rows[1], source_row=3)  # Bob Jones / Robert Jones
    result = await transform_person(r, source_reliability=0.8)
    names = result.transformed["names"]
    former = next((n for n in names if n["name_type"] == "former"), None)
    assert former is not None
    assert former["name"] == "Robert Jones"


async def test_transform_person_bad_email_warning():
    r = validate_person({"Name": "Test", "Personal Email": "bad@"}, source_row=99)
    result = await transform_person(r, source_reliability=0.8)
    assert result.ok
    assert not any(cm["contact_type"] == "email" for cm in result.transformed["contact_methods"])
    assert any("email" in w.lower() for w in result.warnings)
```

- [ ] **Step 3: Verify failure**

```bash
uv run pytest tests/core/ingestion/test_csv_person.py -v
```

- [ ] **Step 4: Implement**

Create `src/core/ingestion/sources/csv_person.py` following the same pattern as `csv_org.py`. Key differences:
- Pydantic model has `personal_pronouns` field
- Names include `former` type
- No `active`, `parent_id`, or `acronym`
- Identifier is `person_wa_pdc`
- `transform_person` signature: `async def transform_person(result, source_reliability, address_normalizer=None) -> RowResult`
- Transformed dict keys: `person_id`, `personal_pronouns`, `notes`, `names`, `contact_methods`, `urls`, `social_links`, `identifiers`, `address`, `confidence_records`

- [ ] **Step 5: Run tests**

```bash
uv run pytest tests/core/ingestion/test_csv_person.py -v
```

- [ ] **Step 6: Commit**

```bash
git add src/core/ingestion/sources/csv_person.py tests/core/ingestion/test_csv_person.py tests/fixtures/ingestion/people_sample.csv
git commit -m "#5 feat: add person source (validate_person, transform_person)"
```

---

### Task 13: Role source

**Files:**
- Create: `src/core/ingestion/sources/csv_role.py`
- Create: `tests/core/ingestion/test_csv_role.py`
- Create: `tests/fixtures/ingestion/roles_sample.csv`

Column mapping:

| CSV Header | DB target |
|---|---|
| `Name` | person lookup via person_index — **required** |
| `Organization` | org lookup via org_index — **required** |
| `Title` | `roles.title` — **required** |
| `Current Role?` | `role_assignments.is_current` (parse same as Active?) |
| `Organization Email` | `contact_methods` on role_assignment (contact_type=email) |
| `Organization Profile URL` | `urls` on role_assignment (url_type_slug=profile, is_canonical=True) |
| `Bluesky URL` | `social_links` on role_assignment (platform_slug=bluesky) |
| `Twitter URL` | `social_links` on role_assignment (platform_slug=twitter) |
| `Facebook URL` | `social_links` on role_assignment (platform_slug=facebook) |
| `WA PDC` | `identifiers` on role_assignment (slug=role_wa_pdc) |
| `Work Phone` | `contact_methods` on role_assignment (contact_type=phone) |
| `Notes` | `roles.notes` |

Note: `roles` is the position definition (org + title); `role_assignments` links a person to a role. The transform produces both a role record and a role_assignment record. Deduplication: look up `(org_id, title.lower())` in `role_index` — reuse existing role_id if found; otherwise create new.

- [ ] **Step 1: Create fixture**

Create `tests/fixtures/ingestion/roles_sample.csv`:

```
Name,Organization,Title,Current Role?,Organization Email,Organization Profile URL,Bluesky URL,Twitter URL,Facebook URL,WA PDC,Work Phone,Notes
Jane Smith,Acme Cannabis LLC,CEO,Yes,ceo@acme.example.com,https://acme.example.com/team/jane,,,,,,(206) 555-9999,
Jane Smith,Acme Cannabis LLC,Board Member,No,,,,,,,,
Bob Jones,Acme Cannabis LLC,COO,Yes,,,,,,,bad-phone,
```

- [ ] **Step 2: Write tests**

Create `tests/core/ingestion/test_csv_role.py`:

```python
"""Tests for csv_role source module."""

import csv
from pathlib import Path

import pytest

from src.core.ingestion.sources.csv_role import validate_role, transform_role


FIXTURE = Path("tests/fixtures/ingestion/roles_sample.csv")

ORG_INDEX = {"acme cannabis llc": "01ORGID00000000000000000001"}
PERSON_INDEX = {
    "jane smith": "01PERSONID0000000000000001",
    "bob jones": "01PERSONID0000000000000002",
}


def read_fixture(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8-sig") as f:
        return [{k: v.strip() for k, v in row.items()} for row in csv.DictReader(f)]


def test_validate_role_valid():
    rows = read_fixture(FIXTURE)
    r = validate_role(rows[0], source_row=2)
    assert r.ok


def test_validate_role_missing_title():
    r = validate_role({"Name": "Jane", "Organization": "Acme Cannabis LLC", "Title": ""}, source_row=99)
    assert not r.ok
    assert any(e.field == "title" for e in r.errors)


def test_transform_role_creates_role_and_assignment():
    rows = read_fixture(FIXTURE)
    r = validate_role(rows[0], source_row=2)
    result = transform_role(r, org_index=ORG_INDEX, person_index=PERSON_INDEX,
                            role_index={}, source_reliability=0.8)
    assert result.ok
    t = result.transformed
    assert "role_id" in t
    assert "assignment_id" in t
    assert t["person_id"] == PERSON_INDEX["jane smith"]
    assert t["org_id"] == ORG_INDEX["acme cannabis llc"]


def test_transform_role_reuses_existing_role():
    rows = read_fixture(FIXTURE)
    r = validate_role(rows[0], source_row=2)
    existing_role_id = "01EXISTINGROLE000000000001"
    role_index = {(ORG_INDEX["acme cannabis llc"], "ceo"): existing_role_id}
    result = transform_role(r, org_index=ORG_INDEX, person_index=PERSON_INDEX,
                            role_index=role_index, source_reliability=0.8)
    assert result.transformed["role_id"] == existing_role_id
    assert result.transformed["role_action"] == "matched"


def test_transform_role_unresolved_org_error():
    rows = read_fixture(FIXTURE)
    r = validate_role(rows[0], source_row=2)
    result = transform_role(r, org_index={}, person_index=PERSON_INDEX,
                            role_index={}, source_reliability=0.8)
    assert not result.ok
    assert any("org" in e.message.lower() for e in result.errors)


def test_transform_role_is_current():
    rows = read_fixture(FIXTURE)
    r = validate_role(rows[0], source_row=2)  # "Yes"
    result = transform_role(r, org_index=ORG_INDEX, person_index=PERSON_INDEX,
                            role_index={}, source_reliability=0.8)
    assert result.transformed["is_current"] is True

    r2 = validate_role(rows[1], source_row=3)  # "No"
    result2 = transform_role(r2, org_index=ORG_INDEX, person_index=PERSON_INDEX,
                             role_index={}, source_reliability=0.8)
    assert result2.transformed["is_current"] is False
```

- [ ] **Step 3: Verify failure**

```bash
uv run pytest tests/core/ingestion/test_csv_role.py -v
```

- [ ] **Step 4: Implement**

Create `src/core/ingestion/sources/csv_role.py`. Key points:
- `RoleRow` Pydantic model with aliases for each CSV column header; `name`, `organization`, `title` are required
- `validate_role(raw, source_row)` → `RowResult`
- `transform_role(result, org_index, person_index, role_index, source_reliability)` → `RowResult` (sync — no address normalization needed)
- Transformed dict: `role_id`, `role_action` (`created`/`matched`), `assignment_id`, `org_id`, `person_id`, `title`, `is_current`, `notes`, `contact_methods`, `urls`, `social_links`, `identifiers`, `confidence_records`
- Unresolved org or person → fatal `FieldError` (entity cannot load without these)
- Role dedup: look up `(org_id, title.lower())` in `role_index`; if found, set `role_action='matched'` and use existing id; if not, generate new id and set `role_action='created'`

- [ ] **Step 5: Run tests**

```bash
uv run pytest tests/core/ingestion/test_csv_role.py -v
```

- [ ] **Step 6: Commit**

```bash
git add src/core/ingestion/sources/csv_role.py tests/core/ingestion/test_csv_role.py tests/fixtures/ingestion/roles_sample.csv
git commit -m "#5 feat: add role source (validate_role, transform_role)"
```

---

### Task 14: Pipeline coordinator

**Files:**
- Create: `src/core/ingestion/pipeline.py`
- Create: `tests/core/ingestion/test_pipeline.py`

The pipeline coordinator:
1. Pre-loads reference data (url_type_ids, platform_ids, identifier_type_ids) from DB
2. Runs multi-pass EVTL across the three CSV files
3. Writes `import_batches` once, `import_provenance` per entity, `field_confidence` per field
4. All entity INSERTs + provenance + confidence in a single transaction per entity

- [ ] **Step 1: Write failing integration test**

Create `tests/core/ingestion/test_pipeline.py`:

```python
"""Integration tests for the import pipeline."""

import asyncio
import json
from pathlib import Path

import asyncpg
import pytest

from src.core.db import apply_schema
from src.core.ingestion.pipeline import ImportConfig, run_import

ORGS_FIXTURE = Path("tests/fixtures/ingestion/orgs_sample.csv")
PEOPLE_FIXTURE = Path("tests/fixtures/ingestion/people_sample.csv")
ROLES_FIXTURE = Path("tests/fixtures/ingestion/roles_sample.csv")


@pytest.fixture
async def db(tmp_path):
    """Integration DB fixture with rollback — requires DATABASE_URL env var."""
    import os
    conn = await asyncpg.connect(os.environ["DATABASE_URL"])
    await apply_schema(conn)
    tr = conn.transaction()
    await tr.start()
    yield conn
    await tr.rollback()
    await conn.close()


@pytest.mark.integration
async def test_run_import_creates_orgs(db):
    config = ImportConfig(
        orgs_csv=ORGS_FIXTURE,
        people_csv=PEOPLE_FIXTURE,
        roles_csv=ROLES_FIXTURE,
        imported_by="test",
        source_reliability=0.8,
    )
    summary = await run_import(db, config)
    assert summary["orgs_loaded"] >= 1
    count = await db.fetchval("SELECT count(*) FROM organizations")
    assert count >= 1


@pytest.mark.integration
async def test_run_import_provenance_written(db):
    config = ImportConfig(
        orgs_csv=ORGS_FIXTURE,
        people_csv=PEOPLE_FIXTURE,
        roles_csv=ROLES_FIXTURE,
        imported_by="test",
        source_reliability=0.8,
    )
    summary = await run_import(db, config)
    count = await db.fetchval(
        "SELECT count(*) FROM import_provenance WHERE batch_id = $1",
        summary["batch_id"],
    )
    assert count >= 1


@pytest.mark.integration
async def test_run_import_field_confidence_written(db):
    config = ImportConfig(
        orgs_csv=ORGS_FIXTURE,
        people_csv=PEOPLE_FIXTURE,
        roles_csv=ROLES_FIXTURE,
        imported_by="test",
        source_reliability=0.8,
    )
    await run_import(db, config)
    count = await db.fetchval("SELECT count(*) FROM field_confidence")
    assert count >= 1
    row = await db.fetchrow("SELECT * FROM field_confidence LIMIT 1")
    assert row["source_reliability"] == pytest.approx(0.8)


@pytest.mark.integration
async def test_run_import_idempotent(db):
    """Running the same import twice yields matched, not duplicated, entities."""
    config = ImportConfig(
        orgs_csv=ORGS_FIXTURE,
        people_csv=PEOPLE_FIXTURE,
        roles_csv=ROLES_FIXTURE,
        imported_by="test",
        source_reliability=0.8,
    )
    await run_import(db, config)
    count_before = await db.fetchval("SELECT count(*) FROM organizations")
    await run_import(db, config)
    count_after = await db.fetchval("SELECT count(*) FROM organizations")
    assert count_before == count_after
    matched = await db.fetchval(
        "SELECT count(*) FROM import_provenance WHERE action = 'matched'"
    )
    assert matched >= 1
```

- [ ] **Step 2: Verify failure**

```bash
export $(cat ../../env | xargs)
uv run pytest tests/core/ingestion/test_pipeline.py -m integration -v
```

- [ ] **Step 3: Implement**

Create `src/core/ingestion/pipeline.py`:

```python
"""Multi-pass import pipeline coordinator."""

import csv
import hashlib
import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import asyncpg

from src.core.db import generate_id
from src.core.ingestion.base import ConfidenceRecord, value_hash
from src.core.ingestion.sources.csv_org import transform_org, validate_org
from src.core.ingestion.sources.csv_person import transform_person, validate_person
from src.core.ingestion.sources.csv_role import transform_role, validate_role
from src.core.logging import get_logger

logger = get_logger(__name__)


@dataclass
class ImportConfig:
    orgs_csv: Path
    people_csv: Path
    roles_csv: Path
    imported_by: str = "import"
    source_reliability: float = 0.8
    notes: str | None = None


@dataclass
class ReferenceData:
    """Lookup dicts loaded from DB at pipeline start."""
    url_type_ids: dict[str, str] = field(default_factory=dict)    # slug → id
    platform_ids: dict[str, str] = field(default_factory=dict)    # slug → id
    identifier_type_ids: dict[str, str] = field(default_factory=dict)  # slug → id


async def _load_reference_data(conn: asyncpg.Connection) -> ReferenceData:
    ref = ReferenceData()
    for row in await conn.fetch("SELECT id, slug FROM url_types"):
        ref.url_type_ids[row["slug"]] = row["id"]
    for row in await conn.fetch("SELECT id, slug FROM platforms"):
        ref.platform_ids[row["slug"]] = row["id"]
    for row in await conn.fetch("SELECT id, slug FROM entity_identifier_types"):
        ref.identifier_type_ids[row["slug"]] = row["id"]
    return ref


def _file_hash(path: Path) -> str:
    h = hashlib.sha256()
    h.update(path.read_bytes())
    return h.hexdigest()


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8-sig") as f:
        return [{k: v.strip() for k, v in row.items()} for row in csv.DictReader(f)]


async def _write_provenance(
    conn: asyncpg.Connection,
    batch_id: str,
    source_row: int,
    entity_type: str,
    entity_id: str,
    action: str,
    raw: dict,
    errors: list | None = None,
) -> None:
    await conn.execute(
        """INSERT INTO import_provenance
               (id, batch_id, source_row, entity_type, entity_id, action, error_detail, raw_data)
           VALUES ($1, $2, $3, $4, $5, $6, $7, $8)""",
        generate_id(), batch_id, source_row, entity_type, entity_id,
        action,
        json.dumps([{"field": e.field, "message": e.message} for e in errors]) if errors else None,
        json.dumps(raw),
    )


async def _write_confidence(conn: asyncpg.Connection, records: list[ConfidenceRecord]) -> None:
    for rec in records:
        await conn.execute(
            """INSERT INTO field_confidence
                   (id, entity_type, entity_id, field_name, value_hash,
                    source_reliability, validation_status, validation_detail, assessed_by)
               VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)""",
            generate_id(), rec.entity_type, rec.entity_id, rec.field_name,
            value_hash(rec.normalized_value),
            rec.source_reliability, rec.validation_status,
            json.dumps(rec.validation_detail) if rec.validation_detail else None,
            rec.assessed_by,
        )


async def run_import(conn: asyncpg.Connection, config: ImportConfig) -> dict[str, Any]:
    """Run the full multi-pass import. Returns a summary dict."""
    start = time.monotonic()
    ref = await _load_reference_data(conn)

    combined_hash = hashlib.sha256(
        (_file_hash(config.orgs_csv) + _file_hash(config.people_csv) + _file_hash(config.roles_csv)).encode()
    ).hexdigest()

    # Check for existing batch with same file hashes (idempotency)
    existing = await conn.fetchrow(
        "SELECT id FROM import_batches WHERE file_hash = $1", combined_hash
    )
    batch_id = existing["id"] if existing else generate_id()
    is_rerun = existing is not None

    org_rows = _read_csv(config.orgs_csv)
    person_rows = _read_csv(config.people_csv)
    role_rows = _read_csv(config.roles_csv)

    summary: dict[str, Any] = {
        "batch_id": batch_id,
        "orgs_loaded": 0, "orgs_matched": 0, "orgs_error": 0,
        "people_loaded": 0, "people_matched": 0, "people_error": 0,
        "roles_loaded": 0, "roles_matched": 0, "roles_error": 0,
    }

    org_index: dict[str, str] = {}
    person_index: dict[str, str] = {}
    role_index: dict[tuple, str] = {}

    # -------------------------------------------------------------------------
    # Pass 1 & 2: Organizations
    # -------------------------------------------------------------------------
    for i, raw in enumerate(org_rows, start=2):
        result = validate_org(raw, source_row=i)
        if not result.ok:
            summary["orgs_error"] += 1
            for e in result.errors:
                logger.warning("org row %d field error: %s = %s", i, e.field, e.message)
            continue
        result = await transform_org(result, org_index=org_index,
                                     source_reliability=config.source_reliability)
        for w in result.warnings:
            logger.warning("org row %d warning: %s", i, w)

        t = result.transformed
        name_lower = next(n["name"] for n in t["names"] if n["name_type"] == "legal").lower()

        # Dedup check
        existing_org = await conn.fetchrow(
            """SELECT o.id FROM organizations o
               JOIN organization_names n ON n.organization_id = o.id
               WHERE lower(n.name) = $1 AND n.name_type = 'legal' AND n.is_canonical = true""",
            name_lower,
        )
        if existing_org:
            org_index[name_lower] = existing_org["id"]
            await _write_provenance(conn, batch_id, i, "organization", existing_org["id"], "matched", raw)
            summary["orgs_matched"] += 1
            logger.debug("org row %d matched: %s", i, name_lower)
            continue

        async with conn.transaction():
            await conn.execute(
                "INSERT INTO organizations (id, active, parent_id, notes) VALUES ($1, $2, $3, $4)",
                t["org_id"], t["active"], t["parent_id"], t["notes"],
            )
            for n in t["names"]:
                await conn.execute(
                    "INSERT INTO organization_names (id, organization_id, name, name_type, is_canonical) VALUES ($1, $2, $3, $4, $5)",
                    generate_id(), t["org_id"], n["name"], n["name_type"], n["is_canonical"],
                )
            for cm in t["contact_methods"]:
                await conn.execute(
                    "INSERT INTO contact_methods (id, entity_type, entity_id, contact_type, value) VALUES ($1, $2, $3, $4, $5)",
                    generate_id(), "organization", t["org_id"], cm["contact_type"], cm["value"],
                )
            for u in t["urls"]:
                url_type_id = ref.url_type_ids.get(u["url_type_slug"])
                if url_type_id:
                    await conn.execute(
                        "INSERT INTO urls (id, entity_type, entity_id, url, url_type_id, is_canonical) VALUES ($1, $2, $3, $4, $5, $6) ON CONFLICT DO NOTHING",
                        generate_id(), "organization", t["org_id"], u["url"], url_type_id, u["is_canonical"],
                    )
            for sl in t["social_links"]:
                platform_id = ref.platform_ids.get(sl["platform_slug"])
                if platform_id:
                    await conn.execute(
                        "INSERT INTO social_links (id, entity_type, entity_id, platform_id, url) VALUES ($1, $2, $3, $4, $5)",
                        generate_id(), "organization", t["org_id"], platform_id, sl["url"],
                    )
            for ident in t["identifiers"]:
                type_id = ref.identifier_type_ids.get(ident["identifier_type_slug"])
                if type_id:
                    await conn.execute(
                        "INSERT INTO identifiers (id, entity_id, entity_identifier_type_id, value) VALUES ($1, $2, $3, $4)",
                        generate_id(), t["org_id"], type_id, ident["value"],
                    )
            if t["address"]:
                addr_id = generate_id()
                a = t["address"]
                await conn.execute(
                    """INSERT INTO addresses (id, raw_input, standardized, address_line_1,
                           address_line_2, city, region, postal_code, country)
                       VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)""",
                    addr_id, a.get("raw_input"), a.get("standardized"),
                    a.get("address_line_1"), a.get("address_line_2"),
                    a.get("city"), a.get("region"), a.get("postal_code"),
                    a.get("country", "US"),
                )
                await conn.execute(
                    "INSERT INTO entity_addresses (id, entity_type, entity_id, address_id, address_type) VALUES ($1, $2, $3, $4, $5)",
                    generate_id(), "organization", t["org_id"], addr_id, "mailing",
                )
            # Update assessed_by with real batch_id
            for rec in t["confidence_records"]:
                rec.assessed_by = f"import:{batch_id}"
            await _write_confidence(conn, t["confidence_records"])
            await _write_provenance(conn, batch_id, i, "organization", t["org_id"], "created", raw)

        org_index[name_lower] = t["org_id"]
        summary["orgs_loaded"] += 1
        logger.debug("org row %d created: %s", i, t["org_id"])

    # -------------------------------------------------------------------------
    # Pass 3 & 4: People  (same pattern as orgs — abbreviated for brevity)
    # -------------------------------------------------------------------------
    for i, raw in enumerate(person_rows, start=2):
        result = validate_person(raw, source_row=i)
        if not result.ok:
            summary["people_error"] += 1
            for e in result.errors:
                logger.warning("person row %d field error: %s = %s", i, e.field, e.message)
            continue
        result = await transform_person(result, source_reliability=config.source_reliability)
        for w in result.warnings:
            logger.warning("person row %d warning: %s", i, w)

        t = result.transformed
        name_lower = next(n["name"] for n in t["names"] if n["name_type"] == "legal").lower()

        existing_person = await conn.fetchrow(
            """SELECT p.id FROM people p
               JOIN person_names n ON n.person_id = p.id
               WHERE lower(n.name) = $1 AND n.name_type = 'legal' AND n.is_canonical = true""",
            name_lower,
        )
        if existing_person:
            person_index[name_lower] = existing_person["id"]
            await _write_provenance(conn, batch_id, i, "person", existing_person["id"], "matched", raw)
            summary["people_matched"] += 1
            continue

        async with conn.transaction():
            await conn.execute(
                "INSERT INTO people (id, personal_pronouns, notes) VALUES ($1, $2, $3)",
                t["person_id"], t.get("personal_pronouns"), t["notes"],
            )
            for n in t["names"]:
                await conn.execute(
                    "INSERT INTO person_names (id, person_id, name, name_type, is_canonical) VALUES ($1, $2, $3, $4, $5)",
                    generate_id(), t["person_id"], n["name"], n["name_type"], n["is_canonical"],
                )
            for cm in t["contact_methods"]:
                await conn.execute(
                    "INSERT INTO contact_methods (id, entity_type, entity_id, contact_type, value) VALUES ($1, $2, $3, $4, $5)",
                    generate_id(), "person", t["person_id"], cm["contact_type"], cm["value"],
                )
            for u in t["urls"]:
                url_type_id = ref.url_type_ids.get(u["url_type_slug"])
                if url_type_id:
                    await conn.execute(
                        "INSERT INTO urls (id, entity_type, entity_id, url, url_type_id, is_canonical) VALUES ($1, $2, $3, $4, $5, $6) ON CONFLICT DO NOTHING",
                        generate_id(), "person", t["person_id"], u["url"], url_type_id, u["is_canonical"],
                    )
            for sl in t["social_links"]:
                platform_id = ref.platform_ids.get(sl["platform_slug"])
                if platform_id:
                    await conn.execute(
                        "INSERT INTO social_links (id, entity_type, entity_id, platform_id, url) VALUES ($1, $2, $3, $4, $5)",
                        generate_id(), "person", t["person_id"], platform_id, sl["url"],
                    )
            for ident in t["identifiers"]:
                type_id = ref.identifier_type_ids.get(ident["identifier_type_slug"])
                if type_id:
                    await conn.execute(
                        "INSERT INTO identifiers (id, entity_id, entity_identifier_type_id, value) VALUES ($1, $2, $3, $4)",
                        generate_id(), t["person_id"], type_id, ident["value"],
                    )
            if t.get("address"):
                addr_id = generate_id()
                a = t["address"]
                await conn.execute(
                    """INSERT INTO addresses (id, raw_input, standardized, address_line_1,
                           address_line_2, city, region, postal_code, country)
                       VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)""",
                    addr_id, a.get("raw_input"), a.get("standardized"),
                    a.get("address_line_1"), a.get("address_line_2"),
                    a.get("city"), a.get("region"), a.get("postal_code"),
                    a.get("country", "US"),
                )
                await conn.execute(
                    "INSERT INTO entity_addresses (id, entity_type, entity_id, address_id, address_type) VALUES ($1, $2, $3, $4, $5)",
                    generate_id(), "person", t["person_id"], addr_id, "mailing",
                )
            for rec in t["confidence_records"]:
                rec.assessed_by = f"import:{batch_id}"
            await _write_confidence(conn, t["confidence_records"])
            await _write_provenance(conn, batch_id, i, "person", t["person_id"], "created", raw)

        person_index[name_lower] = t["person_id"]
        summary["people_loaded"] += 1

    # -------------------------------------------------------------------------
    # Pass 5, 6 & 7: Roles + Assignments
    # -------------------------------------------------------------------------
    for i, raw in enumerate(role_rows, start=2):
        result = validate_role(raw, source_row=i)
        if not result.ok:
            summary["roles_error"] += 1
            continue
        result = transform_role(result, org_index=org_index, person_index=person_index,
                                role_index=role_index, source_reliability=config.source_reliability)
        if not result.ok:
            summary["roles_error"] += 1
            for e in result.errors:
                logger.warning("role row %d error: %s", i, e.message)
            await _write_provenance(conn, batch_id, i, "role_assignment",
                                    generate_id(), "error", raw, result.errors)
            continue
        for w in result.warnings:
            logger.warning("role row %d warning: %s", i, w)

        t = result.transformed

        # Dedup role_assignment
        existing_ra = await conn.fetchrow(
            "SELECT id FROM role_assignments WHERE person_id = $1 AND role_id = $2",
            t["person_id"], t["role_id"],
        )
        if existing_ra:
            await _write_provenance(conn, batch_id, i, "role_assignment",
                                    existing_ra["id"], "matched", raw)
            summary["roles_matched"] += 1
            continue

        async with conn.transaction():
            if t["role_action"] == "created":
                await conn.execute(
                    "INSERT INTO roles (id, organization_id, title, notes) VALUES ($1, $2, $3, $4)",
                    t["role_id"], t["org_id"], t["title"], t["notes"],
                )
            await conn.execute(
                "INSERT INTO role_assignments (id, person_id, role_id, is_current) VALUES ($1, $2, $3, $4)",
                t["assignment_id"], t["person_id"], t["role_id"], t["is_current"],
            )
            for cm in t["contact_methods"]:
                await conn.execute(
                    "INSERT INTO contact_methods (id, entity_type, entity_id, contact_type, value) VALUES ($1, $2, $3, $4, $5)",
                    generate_id(), "role_assignment", t["assignment_id"], cm["contact_type"], cm["value"],
                )
            for u in t["urls"]:
                url_type_id = ref.url_type_ids.get(u["url_type_slug"])
                if url_type_id:
                    await conn.execute(
                        "INSERT INTO urls (id, entity_type, entity_id, url, url_type_id, is_canonical) VALUES ($1, $2, $3, $4, $5, $6) ON CONFLICT DO NOTHING",
                        generate_id(), "role_assignment", t["assignment_id"], u["url"], url_type_id, u["is_canonical"],
                    )
            for sl in t["social_links"]:
                platform_id = ref.platform_ids.get(sl["platform_slug"])
                if platform_id:
                    await conn.execute(
                        "INSERT INTO social_links (id, entity_type, entity_id, platform_id, url) VALUES ($1, $2, $3, $4, $5)",
                        generate_id(), "role_assignment", t["assignment_id"], platform_id, sl["url"],
                    )
            for ident in t["identifiers"]:
                type_id = ref.identifier_type_ids.get(ident["identifier_type_slug"])
                if type_id:
                    await conn.execute(
                        "INSERT INTO identifiers (id, entity_id, entity_identifier_type_id, value) VALUES ($1, $2, $3, $4)",
                        generate_id(), t["assignment_id"], type_id, ident["value"],
                    )
            for rec in t["confidence_records"]:
                rec.assessed_by = f"import:{batch_id}"
            await _write_confidence(conn, t["confidence_records"])
            await _write_provenance(conn, batch_id, i, "role_assignment",
                                    t["assignment_id"], "created", raw)

        role_key = (t["org_id"], t["title"].lower())
        role_index[role_key] = t["role_id"]
        summary["roles_loaded"] += 1

    # -------------------------------------------------------------------------
    # Write / update import_batches
    # -------------------------------------------------------------------------
    total = len(org_rows) + len(person_rows) + len(role_rows)
    loaded = summary["orgs_loaded"] + summary["people_loaded"] + summary["roles_loaded"]
    matched = summary["orgs_matched"] + summary["people_matched"] + summary["roles_matched"]
    errors = summary["orgs_error"] + summary["people_error"] + summary["roles_error"]

    if not is_rerun:
        await conn.execute(
            """INSERT INTO import_batches
                   (id, source_file, file_hash, imported_by, row_count, loaded_count, error_count, notes)
               VALUES ($1, $2, $3, $4, $5, $6, $7, $8)""",
            batch_id,
            f"{config.orgs_csv.name},{config.people_csv.name},{config.roles_csv.name}",
            combined_hash,
            config.imported_by,
            total,
            loaded + matched,
            errors,
            config.notes,
        )

    elapsed = time.monotonic() - start
    logger.info(
        "import complete: batch=%s loaded=%d matched=%d errors=%d elapsed=%.1fs",
        batch_id, loaded, matched, errors, elapsed,
    )
    summary["elapsed_s"] = elapsed
    return summary
```

- [ ] **Step 4: Run integration tests**

```bash
export $(cat ../../env | xargs)
uv run pytest tests/core/ingestion/test_pipeline.py -m integration -v
```

- [ ] **Step 5: Commit**

```bash
git add src/core/ingestion/pipeline.py tests/core/ingestion/test_pipeline.py
git commit -m "#5 feat: add pipeline coordinator with multi-pass EVTL import"
```

---

### Task 15: CLI script

**Files:**
- Create: `scripts/import_cannabis_observer.py`

- [ ] **Step 1: Implement**

Create `scripts/import_cannabis_observer.py`:

```python
#!/usr/bin/env python3
"""CLI entry point: import Cannabis Observer CSV exports into PostgreSQL.

Usage:
    uv run python scripts/import_cannabis_observer.py \\
        --orgs   data/cannabis_observer/Organizations.csv \\
        --people data/cannabis_observer/People.csv \\
        --roles  data/cannabis_observer/Roles.csv

Environment variables required:
    DATABASE_URL — PostgreSQL DSN (written by scripts/setup-db.sh)
    ADDRESS_VALIDATOR_API_KEY — optional; only needed if --validate-addresses is set
"""

import argparse
import asyncio
import os
from pathlib import Path

import asyncpg

from src.core.db import apply_schema
from src.core.ingestion.pipeline import ImportConfig, run_import
from src.core.logging import configure_logging, get_logger

logger = get_logger(__name__)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Import Cannabis Observer CSV data into PostgreSQL.")
    parser.add_argument("--orgs",   type=Path, required=True, help="Path to Organizations.csv")
    parser.add_argument("--people", type=Path, required=True, help="Path to People.csv")
    parser.add_argument("--roles",  type=Path, required=True, help="Path to Roles.csv")
    parser.add_argument("--source-reliability", type=float, default=0.8,
                        help="Source reliability score (0.0–1.0). Default: 0.8")
    parser.add_argument("--validate-addresses", action="store_true",
                        help="Call /validate endpoint (rate-limited). Requires ADDRESS_VALIDATOR_API_KEY.")
    parser.add_argument("--imported-by", default="cannabis-observer-csv-import")
    return parser.parse_args()


async def main() -> None:
    configure_logging()
    args = parse_args()

    for path in (args.orgs, args.people, args.roles):
        if not path.exists():
            raise SystemExit(f"File not found: {path}")

    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        raise SystemExit("DATABASE_URL is not set. Run: export $(cat env | xargs)")

    conn = await asyncpg.connect(dsn)
    try:
        await apply_schema(conn)
        config = ImportConfig(
            orgs_csv=args.orgs,
            people_csv=args.people,
            roles_csv=args.roles,
            imported_by=args.imported_by,
            source_reliability=args.source_reliability,
        )
        summary = await run_import(conn, config)
        logger.info("import summary: %s", summary)
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
```

- [ ] **Step 2: Make executable and do a dry-run check (no DB)**

```bash
chmod +x scripts/import_cannabis_observer.py
uv run python scripts/import_cannabis_observer.py --help
```

Expected: argparse help text, no errors.

- [ ] **Step 3: Run full test suite**

```bash
uv run pytest tests/ -v
```

Expected: all non-integration tests pass.

- [ ] **Step 4: Commit**

```bash
git add scripts/import_cannabis_observer.py
git commit -m "#5 feat: add import_cannabis_observer CLI script"
```

---

## Running the Full Import

Once all tasks are complete and CSV files are in place:

```bash
cd .worktrees/feature/csv-import
export $(cat ../../env | xargs)
uv run python scripts/import_cannabis_observer.py \
    --orgs   data/cannabis_observer/Organizations.csv \
    --people data/cannabis_observer/People.csv \
    --roles  data/cannabis_observer/Roles.csv
```

To run integration tests against the real DB:

```bash
uv run pytest tests/ -m integration -v
```

---

## Verification Checklist

After the import completes:

```sql
SELECT count(*) FROM organizations;
SELECT count(*) FROM people;
SELECT count(*) FROM role_assignments;
SELECT count(*) FROM import_provenance;
SELECT action, count(*) FROM import_provenance GROUP BY action;
SELECT count(*) FROM field_confidence;
SELECT field_name, count(*) FROM field_confidence GROUP BY field_name ORDER BY count DESC;
SELECT * FROM import_batches;
```
