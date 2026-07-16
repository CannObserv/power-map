"""Tests for shared jurisdiction domain helpers (src.core.jurisdictions).

Covers the relationship-category display-label mapping (#278): the mapping
must stay in lockstep with the ``category`` CHECK enum in ``schema.sql`` so
templates never fall back to title-casing a slug the mapping doesn't know.
"""

import re
from pathlib import Path

from src.core.jurisdictions import (
    RELATIONSHIP_CATEGORY_LABELS,
    relationship_category_label,
)

SCHEMA = Path("src/core/schema.sql")


def _schema_category_enum() -> set[str]:
    """Parse the category CHECK enum out of schema.sql."""
    sql = SCHEMA.read_text()
    match = re.search(r"CHECK \(category IN \(([^)]*)\)\)", sql)
    assert match, "category CHECK enum not found in schema.sql"
    return {value.strip().strip("'") for value in match.group(1).split(",")}


def test_labels_cover_schema_category_enum_exactly():
    """The mapping's keys must exactly match the schema CHECK enum.

    A key missing here means a category renders via the fallback instead of
    its curated label; an extra key means the schema dropped a category the
    code still advertises. Either way: fix the mapping, not this test.
    """
    assert set(RELATIONSHIP_CATEGORY_LABELS) == _schema_category_enum()


def test_known_slugs_map_to_curated_labels():
    assert relationship_category_label("spatial") == "Spatial"
    assert relationship_category_label("governance") == "Governance"
    assert relationship_category_label("functional") == "Functional"
    assert relationship_category_label("lineage") == "Lineage"


def test_unknown_slug_falls_back_to_title_cased_words():
    """Graceful degradation if the enum grows before the mapping does."""
    assert relationship_category_label("multi_word_new") == "Multi Word New"
