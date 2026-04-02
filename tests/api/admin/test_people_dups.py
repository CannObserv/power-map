"""Unit tests for people-duplicate detection logic (cache, count, dep)."""

from unittest.mock import AsyncMock, MagicMock

import pytest

# This import will fail until people_dups.py exists — that's the failing test.
from src.api.admin.people_dups import (
    count_person_duplicates,
    get_person_dup_count,
    invalidate_dup_count_cache,
)
