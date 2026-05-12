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
    normalized_value: str  # the actual stored value; hashed at insert time
    source_reliability: float
    validation_status: str  # 'confirmed'|'unconfirmed'|'failed'|'not_attempted'
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
