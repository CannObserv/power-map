"""Unit tests for ObservationRequest and related schemas."""

import pytest
from pydantic import ValidationError

from src.api.public.schemas import (
    ObservationLink,
    ObservationRequest,
    ObservationResponse,
)


def test_valid_minimal_request():
    """Minimal request with only required fields constructs OK."""
    req = ObservationRequest(
        identifier_type="person_wa_legislature_member_id",
        identifier_value="26142",
    )
    assert req.identifier_type == "person_wa_legislature_member_id"
    assert req.identifier_value == "26142"
    assert req.names == []
    assert req.links == []
    assert req.contact_methods == []
    assert req.addresses == []
    assert req.role_assignments == []
    assert req.org_acronyms == []
    assert req.organization_parent_id is None
    assert req.organization_parent_name is None
    assert req.organization_parent_acronym is None
    assert req.personal_pronouns is None


def test_observation_link_both_type_fields_raises():
    """ObservationLink with both link_type_id and link_type_slug → ValidationError."""
    with pytest.raises(ValidationError):
        ObservationLink(
            url="https://example.com",
            link_type_id="01ABC",
            link_type_slug="website",
        )


def test_observation_link_neither_type_field_raises():
    """ObservationLink with neither link_type_id nor link_type_slug → ValidationError."""
    with pytest.raises(ValidationError):
        ObservationLink(url="https://example.com")


def test_observation_link_id_only_ok():
    """ObservationLink with link_type_id only → OK."""
    link = ObservationLink(url="https://example.com", link_type_id="01ABC")
    assert link.link_type_id == "01ABC"
    assert link.link_type_slug is None


def test_observation_link_slug_only_ok():
    """ObservationLink with link_type_slug only → OK."""
    link = ObservationLink(url="https://example.com", link_type_slug="website")
    assert link.link_type_slug == "website"
    assert link.link_type_id is None


def test_request_two_org_parent_fields_raises():
    """ObservationRequest with two org parent fields → ValidationError."""
    with pytest.raises(ValidationError):
        ObservationRequest(
            identifier_type="org_type",
            identifier_value="123",
            organization_parent_id="01ABC",
            organization_parent_name="Parent Org",
        )


def test_request_one_org_parent_field_ok():
    """ObservationRequest with exactly one org parent field → OK."""
    req = ObservationRequest(
        identifier_type="org_type",
        identifier_value="123",
        organization_parent_name="Parent Org",
    )
    assert req.organization_parent_name == "Parent Org"
    assert req.organization_parent_id is None
    assert req.organization_parent_acronym is None


def test_observation_response_rejected_entity_id_none():
    """ObservationResponse with disposition='rejected', entity_id=None → OK."""
    resp = ObservationResponse(
        disposition="rejected",
        entity_id=None,
        entity_type=None,
    )
    assert resp.disposition == "rejected"
    assert resp.entity_id is None
    assert resp.entity_type is None
