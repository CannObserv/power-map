"""Shared type aliases — keep DB enum-like constants in one place.

Mirrors the DB CHECK constraint values so admin forms, ingestion code,
and tests can reference a single source of truth. If the DB constraint
changes, update the alias here and the schema.sql CHECK in lockstep.
"""

from typing import Literal

# person_names_visibility_check (src/core/schema.sql)
PersonNameVisibility = Literal["public", "legal_only", "hidden"]
