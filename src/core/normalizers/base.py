"""Base types for the normalizer hierarchy."""

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

# Strings treated as absent / unknown regardless of case
NULL_LIKE: frozenset[str] = frozenset(
    {
        "",
        "n/a",
        "na",
        "none",
        "null",
        "unknown",
        "tbd",
        "-",
        "--",
        "n.a.",
        "not available",
    }
)

# Strings treated as truthy regardless of case. Covers the union of every
# free-text boolean source in the codebase: CSV ingestion flags
# ("Yes"/"No", "Y"/"N"), HTMX query-string confirmation tokens
# ("1", "true"), and abbreviated single-letter forms ("y", "t").
TRUTHY_LIKE: frozenset[str] = frozenset(
    {
        "1",
        "t",
        "true",
        "y",
        "yes",
        "on",
    }
)


def is_null_like(raw: str | None) -> bool:
    """Return True if *raw* is absent or a known null-like sentinel."""
    return raw is None or raw.strip().lower() in NULL_LIKE


def is_truthy_like(raw: str | None) -> bool:
    """Return True if *raw* is a known truthy-like string.

    Case-insensitive; whitespace-stripped. None and unrecognised strings
    are treated as False — callers that need a default-True policy must
    handle the None case before calling.
    """
    return raw is not None and raw.strip().lower() in TRUTHY_LIKE


@dataclass
class NormalizationResult:
    """Output of a single normalizer call."""

    value: Any | None  # normalized output; None when skipped
    skipped: bool = False  # True when input was absent/null-like
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
