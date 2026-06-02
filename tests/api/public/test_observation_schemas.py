"""Unit tests for observation request/response schemas."""

import pytest
from pydantic import ValidationError

from src.api.public.schemas import (
    ObservationAddress,
    ObservationContactMethod,
    ObservationLink,
    ObservationNameParts,
    ObservationResponse,
    OrganizationObservationRequest,
    PeopleObservationRequest,
)

# ---------------------------------------------------------------------------
# ObservationLink
# ---------------------------------------------------------------------------


def test_observation_link_both_type_fields_raises():
    with pytest.raises(ValidationError):
        ObservationLink(
            url="https://example.com",
            link_type_id="01ABC",
            link_type_slug="website",
        )


def test_observation_link_neither_type_field_raises():
    with pytest.raises(ValidationError):
        ObservationLink(url="https://example.com")


def test_observation_link_id_only_ok():
    link = ObservationLink(url="https://example.com", link_type_id="01ABC")
    assert link.link_type_id == "01ABC"
    assert link.link_type_slug is None


def test_observation_link_slug_only_ok():
    link = ObservationLink(url="https://example.com", link_type_slug="website")
    assert link.link_type_slug == "website"
    assert link.link_type_id is None


# ---------------------------------------------------------------------------
# ObservationResponse
# ---------------------------------------------------------------------------


def test_observation_response_rejected_entity_id_none():
    resp = ObservationResponse(disposition="rejected", entity_id=None, entity_type=None)
    assert resp.disposition == "rejected"
    assert resp.entity_id is None
    assert resp.entity_type is None


# ---------------------------------------------------------------------------
# ObservationContactMethod / ObservationAddress / ObservationNameParts
# ---------------------------------------------------------------------------


def test_contact_method_invalid_type_rejected():
    with pytest.raises(ValidationError):
        ObservationContactMethod(contact_type="fax", value="12345")


def test_address_invalid_type_rejected():
    with pytest.raises(ValidationError):
        ObservationAddress(raw_input="123 Main St", address_type="postal")


def test_name_parts_invalid_primary_identifier_rejected():
    with pytest.raises(ValidationError):
        ObservationNameParts(primary_identifier="nickname")


# ---------------------------------------------------------------------------
# PeopleObservationRequest
# ---------------------------------------------------------------------------


def test_people_request_minimal_ok():
    req = PeopleObservationRequest(
        identifier_type="person_wa_pdc",
        identifier_value="12345",
    )
    assert req.identifier_type == "person_wa_pdc"
    assert req.names == []
    assert req.role_assignments == []
    assert req.personal_pronouns is None


def test_people_request_no_org_fields():
    """PeopleObservationRequest has no org-specific fields."""
    assert not hasattr(PeopleObservationRequest, "org_acronyms")
    assert not hasattr(PeopleObservationRequest.model_fields, "org_acronyms")
    assert not hasattr(PeopleObservationRequest.model_fields, "organization_parent_id")


# ---------------------------------------------------------------------------
# OrganizationObservationRequest
# ---------------------------------------------------------------------------


def test_org_request_minimal_ok():
    req = OrganizationObservationRequest(
        identifier_type="org_ubi",
        identifier_value="999",
    )
    assert req.identifier_type == "org_ubi"
    assert req.org_acronyms == []
    assert req.organization_parent_id is None


def test_org_request_xor_parent_two_fields_raises():
    with pytest.raises(ValidationError):
        OrganizationObservationRequest(
            identifier_type="org_ubi",
            identifier_value="123",
            organization_parent_id="01ABC",
            organization_parent_name="Parent Org",
        )


def test_org_request_xor_parent_one_field_ok():
    req = OrganizationObservationRequest(
        identifier_type="org_ubi",
        identifier_value="123",
        organization_parent_name="Parent Org",
    )
    assert req.organization_parent_name == "Parent Org"
    assert req.organization_parent_id is None
    assert req.organization_parent_acronym is None


def test_org_request_no_person_fields():
    """OrganizationObservationRequest has no person-specific fields."""
    assert "personal_pronouns" not in OrganizationObservationRequest.model_fields
    assert "role_assignments" not in OrganizationObservationRequest.model_fields
