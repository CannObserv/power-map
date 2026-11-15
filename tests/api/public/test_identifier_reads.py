"""Unit tests for the SQL fragments in src/api/public/identifier_reads.py."""

import pytest

from src.api.public.identifier_reads import identifier_holders, identifier_match

_LOOKUP_TYPES = ["person", "organization", "role", "role_assignment"]


@pytest.mark.parametrize("entity_type", _LOOKUP_TYPES)
def test_identifier_holders_selects_entity_ids_for_its_type(entity_type):
    """One entity id per matching row, scoped to the type and both params (#811)."""
    sql = identifier_holders(entity_type, 1, 2)
    assert sql.startswith("SELECT i.entity_id FROM identifiers i")
    assert f"t.entity_type = '{entity_type}'" in sql
    assert "t.slug = $1 AND i.value = $2" in sql


@pytest.mark.parametrize("entity_type", _LOOKUP_TYPES)
def test_identifier_match_is_the_holder_lookup_or_no_filter(entity_type):
    """The list-route predicate reuses the holder subquery, so all four read alike."""
    sql = identifier_match(entity_type, "x.id", 5, 6)
    assert sql == f"($5::TEXT IS NULL OR x.id IN ({identifier_holders(entity_type, 5, 6)}))"


@pytest.mark.parametrize("entity_type", ["district", "person; DROP", ""])
def test_lookups_refuse_other_types(entity_type):
    """The type is interpolated into SQL, so only the four lookup types pass."""
    with pytest.raises(ValueError, match="no identifier lookup"):
        identifier_holders(entity_type, 1, 2)
    with pytest.raises(ValueError, match="no identifier lookup"):
        identifier_match(entity_type, "x.id", 1, 2)
