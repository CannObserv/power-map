# CSV Import & Data Ingestion Infrastructure Design

**Date:** 2026-03-18
**Goal:** Import three Cannabis Observer CSV exports into PostgreSQL and, in doing so, establish durable ingestion infrastructure for normalizing, validating, and tracking confidence in data across the application.

---

## Goal

Import `Organizations.csv`, `People.csv`, and `Roles.csv` into the normalized schema while building reusable ingestion infrastructure that will serve future data sources. Data quality handling is a first-class concern: every field error is captured, logged, and traceable back to its source row; no data is silently dropped or corrupted.

---

## Approved Approach

### EVTL Pipeline Pattern

Each CSV goes through four discrete phases, implemented as pure functions operating on a single row:

1. **Extract** — `csv.DictReader` yields `dict[str, str]`; all values stripped; `source_row` (1-based line number) and `raw` dict attached to every downstream object
2. **Validate** — Pydantic v2 model per entity type; `model_validate()` inside `try/except ValidationError` accumulates all field errors in one pass; returns a `RowResult`
3. **Transform** — runs only if validation passed; generates ULIDs, calls normalizers, resolves entity references via in-memory lookup dicts built from prior passes
4. **Load** — writes to DB; no business logic; DB constraint violations treated as a distinct error class

### Row-Level Result Envelope

```python
@dataclass
class FieldError:
    field: str
    message: str
    raw_value: str | None = None

@dataclass
class RowResult:
    source_row: int
    raw: dict[str, str]
    errors: list[FieldError] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    transformed: dict[str, Any] | None = None

    @property
    def ok(self) -> bool:
        return not self.errors
```

Entities with field errors still load if all *required* fields are valid (no field is required except `name`). Errors are written to `import_provenance.error_detail` as JSONB and logged at WARNING. Entities with a failed *required* field are skipped entirely; their row is written to `import_provenance` with `action='error'`.

### Multi-Pass Loading Order

Dependencies flow one way: orgs must exist before roles can reference them; people must exist before role assignments can reference them.

```
Pass 1: Extract + Validate + Transform organizations
Pass 2: Load organizations → build {canonical_name → org_id} index
Pass 3: Extract + Validate + Transform people
Pass 4: Load people → build {canonical_name → person_id} index
Pass 5: Extract + Validate + Transform roles (resolve org + person refs via indices)
Pass 6: Load roles → build {(org_id, title_normalized) → role_id} index
Pass 7: Load role_assignments
```

Unresolvable references (org/person name not in index) are validation errors on the consuming pass, not FK violations from Postgres. This yields actionable error messages.

### Deduplication (Idempotency)

Even though this is a one-time migration, each pass checks for an existing entity before inserting:

- **Organization:** match on lowercased canonical name
- **Person:** match on lowercased canonical name (+ WA PDC identifier if present)
- **Role:** match on `(org_id, title.lower())`
- **Role assignment:** match on `(person_id, role_id)`

If found, record `action='matched'` in provenance; reuse existing ID. `ON CONFLICT DO NOTHING` as a last-resort safety net.

---

## Schema Additions

Three new tables added to `schema.sql`:

### `import_batches`

```sql
CREATE TABLE IF NOT EXISTS import_batches (
    id           TEXT        PRIMARY KEY,
    source_file  TEXT        NOT NULL,
    file_hash    TEXT        NOT NULL,   -- SHA-256; idempotency key
    imported_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    imported_by  TEXT,
    row_count    INTEGER     NOT NULL,
    loaded_count INTEGER     NOT NULL,
    error_count  INTEGER     NOT NULL,
    notes        TEXT
);
```

### `import_provenance`

```sql
CREATE TABLE IF NOT EXISTS import_provenance (
    id              TEXT        PRIMARY KEY,
    batch_id        TEXT        NOT NULL REFERENCES import_batches(id),
    source_row      INTEGER     NOT NULL,
    entity_type     TEXT        NOT NULL,  -- 'organization', 'person', 'role_assignment'
    entity_id       TEXT        NOT NULL,
    action          TEXT        NOT NULL CHECK (action IN ('created','matched','skipped','error')),
    error_detail    JSONB,                 -- structured FieldError list if action='error'
    raw_data        JSONB       NOT NULL   -- original CSV row, always preserved
);
```

### `field_confidence`

Append-only. Never updated; new rows are always inserted to preserve history.

```sql
CREATE TABLE IF NOT EXISTS field_confidence (
    id                  TEXT        PRIMARY KEY,
    entity_type         TEXT        NOT NULL,
    entity_id           TEXT        NOT NULL,
    field_name          TEXT        NOT NULL,
    value_hash          TEXT        NOT NULL,  -- SHA-256 of field value at assessment time
    source_reliability  REAL        NOT NULL CHECK (source_reliability BETWEEN 0.0 AND 1.0),
    validation_status   TEXT        NOT NULL CHECK (validation_status IN (
                            'confirmed', 'unconfirmed', 'failed', 'not_attempted')),
    validation_detail   JSONB,
    assessed_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    assessed_by         TEXT        -- 'import:<batch_id>', 'api:address-validator', 'manual'
);
```

`value_hash` enables distinguishing re-validation of the same value (same hash, new row) from post-update re-assessment (different hash, new row).

Latest assessment query: `ORDER BY assessed_at DESC LIMIT 1`.

---

## `src/core/` Module Structure

```
src/core/
  db.py                         — pool, apply_schema, generate_id (unchanged)
  normalizers/
    __init__.py
    base.py                     — NormalizationResult dataclass; Normalizer Protocol
    phone.py                    — normalize_phone() (moved from db.py)
    email.py                    — validate_email() (moved from db.py)
    url.py                      — UrlNormalizer (validate + canonicalize)
    identifier.py               — IdentifierNormalizer (UBI, WA PDC, SEC, etc.)
    address.py                  — ExternalAddressNormalizer, LocalAddressNormalizer,
                                   FallbackAddressNormalizer
  ingestion/
    __init__.py
    base.py                     — RowResult, FieldError, EVTL base classes
    pipeline.py                 — multi-pass coordinator; import_batches writer
    sources/
      csv_org.py                — org extract/validate/transform
      csv_person.py             — person extract/validate/transform
      csv_role.py               — role + role_assignment extract/validate/transform
```

### `NormalizationResult`

```python
@dataclass
class NormalizationResult:
    value: Any | None           # normalized output value
    skipped: bool = False       # True for empty/null-like inputs
    warnings: list[str] = field(default_factory=list)
    confidence_hint: str = "unconfirmed"  # feeds validation_status in field_confidence
    validation_detail: dict | None = None
```

### `Normalizer` Protocol

```python
class Normalizer(Protocol):
    def normalize(self, raw: str | None) -> NormalizationResult: ...
```

All normalizers implement this interface; composable via `FallbackNormalizer(primary, fallback)`.

---

## Address Normalizer Detail

### Configuration

```python
@dataclass
class ExternalAddressNormalizerConfig:
    api_key: str
    base_url: str = "https://address-validator.exe.xyz:8000"
    run_validation: bool = False    # default off; /validate is rate-limited
    max_retries: int = 3
```

API key stored in `env` file as `ADDRESS_VALIDATOR_API_KEY`.

### Endpoint selection

- `run_validation=False` → `POST /api/v1/standardize` (always)
- `run_validation=True` → `POST /api/v1/validate` only (already includes standardization; no need to call `/standardize` separately)

### 429 handling

Read `Retry-After` header (seconds). Sleep and retry up to `max_retries`. If budget exhausted, fall back to `LocalAddressNormalizer` and log a WARNING. Any 5xx or timeout also falls back.

### Confidence mapping (when `run_validation=True`)

| `ValidationResult.status`         | `validation_status`        |
|------------------------------------|----------------------------|
| `confirmed`                        | `confirmed`                |
| `confirmed_missing_secondary`      | `confirmed`                |
| `confirmed_bad_secondary`          | `confirmed`                |
| `not_confirmed`                    | `failed`                   |
| `unavailable`                      | `not_attempted`            |

`validation_detail` stores: `{"status": ..., "dpv_match_code": ..., "provider": ..., "warnings": [...]}`.

### Local fallback

Uses `usaddress.tag()`. Catches `RepeatedLabelError` and stores `raw_input` only. Always produces `validation_status='not_attempted'`.

---

## Phone Ingestion Example (EVTL trace)

Input: `"(206) 555-1234"`

1. **Extract**: `raw = "(206) 555-1234"` → stripped
2. **Validate**: `PhoneNormalizer.normalize(raw)` called
   - Empty / null-like (`""`, `"N/A"`, `"TBD"`) → `NormalizationResult(value=None, skipped=True)` — not an error
   - `phonenumbers.parse(raw, default_region="US")` → raises `NumberParseException` → `FieldError`
   - `phonenumbers.is_valid_number(parsed)` → False → `FieldError` (e.g. local number without area code)
   - Valid → continue to Transform
3. **Transform**: `phonenumbers.format_number(parsed, E164)` → `"+12065551234"`
4. **Load**: insert into `contact_methods(entity_type, entity_id, method_type, value)`
5. **Confidence**: insert into `field_confidence`:
   - `value_hash = sha256("+12065551234")`
   - `source_reliability = 0.8` (Cannabis Observer batch constant)
   - `validation_status = "unconfirmed"` (no authoritative phone validation service)
   - `assessed_by = "import:<batch_id>"`

Phone is never a required field; a `FieldError` on phone does not prevent the entity from being created. The error is stored in `import_provenance.error_detail` and logged at WARNING.

---

## Logging

Per `src/core/logging` convention, `configure_logging()` called once at the import script entry point.

| Level   | Content |
|---------|---------|
| DEBUG   | Per-row: `source_row`, `entity_type`, `action`, `entity_id`, any warnings |
| INFO    | Batch summary: total rows, loaded, skipped, errored, duration, `batch_id` |
| WARNING | Each `FieldError`: field name, raw value, message, source_row |
| ERROR   | DB connection failures, schema mismatch, file not found |

---

## Testing Strategy

**Unit tests** (no DB) — `tests/ingestion/`:
- `test_transform_org.py`, `test_transform_person.py`, `test_transform_role.py`
- `test_normalizer_phone.py`, `test_normalizer_address.py`, etc.
- Each normalizer/transformer takes `dict[str, str]` → returns `RowResult`; fully pure
- `io.StringIO`-based CSV fixtures for small inline data; `tests/fixtures/ingestion/*.csv` for larger sets

**Integration tests** (marked `integration`, require `DATABASE_URL`):
- Full multi-pass pipeline against real test DB
- Verify entities land in DB; provenance rows written; `field_confidence` rows written
- Re-running same CSV → `action='matched'`, no duplicate entities
- FK constraint violations handled gracefully

---

## Cannabis Observer Import Constants

- `source_reliability = 0.8` for all `field_confidence` rows in this batch
- `imported_by = "cannabis-observer-csv-import"`
- `run_validation = False` (address validation off by default)

---

## Out of Scope

- Fuzzy name deduplication (exact match after normalization only; fuzzy as future opt-in)
- Address existence verification beyond what `/validate` provides
- Phone number deliverability validation
- Syncing updates from future CSV revisions (one-time migration)
- UI or API surface for ingestion; CLI script only
