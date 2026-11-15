"""Unit tests for the SQL fragments in src/api/public/identifier_reads.py."""

import pytest

from src.api.public.identifier_reads import identifier_holders, identifier_match


@pytest.mark.parametrize("entity_type", ["person", "organization"])
def test_identifier_holders_selects_entity_ids_for_its_type(entity_type):
    """#811: one entity id per matching row, scoped to the type and both params."""
    sql = identifier_holders(entity_type, 1, 2)
    assert sql.startswith("SELECT i.entity_id FROM identifiers i")
    assert f"t.entity_type = '{entity_type}'" in sql
    assert "t.slug = $1 AND i.value = $2" in sql


@pytest.mark.parametrize("entity_type", ["role", "role_assignment", "person; DROP", ""])
def test_identifier_holders_refuses_other_types(entity_type):
    """The type is interpolated into SQL, so only the two search types pass (#811)."""
    with pytest.raises(ValueError, match="no identifier holder lookup"):
        identifier_holders(entity_type, 1, 2)


@pytest.mark.parametrize("entity_type", ["person", "organization"])
def test_identifier_match_still_refuses_search_types(entity_type):
    """The list-route predicate stays role/assignment-only (#808/#809)."""
    with pytest.raises(ValueError, match="no identifier lookup"):
        identifier_match(entity_type, "x.id", 1, 2)
