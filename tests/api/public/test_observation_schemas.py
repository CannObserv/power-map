"""Unit tests for observation request/response schemas."""

from datetime import date

import pytest
from pydantic import ValidationError

from src.api.public.schemas import (
    ObservationAcronym,
    ObservationAddress,
    ObservationContactMethod,
    ObservationLink,
    ObservationOrgName,
    ObservationPersonName,
    ObservationPersonNameParts,
    ObservationResponse,
    OrganizationObservationRequest,
    PeopleObservationRequest,
    RoleObservationRequest,
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


def test_observation_response_provenance_claimed_is_additive_and_optional():
    """#478: `null`/absent unless this observation stamped provenance.

    Same contract as `attached_archived` (#477) — never `false` on a healthy
    response, but always present as a key so a producer can probe for support
    rather than infer it from an absent field.
    """
    field = ObservationResponse.model_fields.get("provenance_claimed")
    assert field is not None, "ObservationResponse does not expose provenance_claimed (#478)"
    assert field.default is None
    dumped = ObservationResponse(disposition="auto-attached", entity_id="01ABC").model_dump()
    assert "provenance_claimed" in dumped
    assert dumped["provenance_claimed"] is None


# ---------------------------------------------------------------------------
# ObservationContactMethod / ObservationAddress / ObservationPersonNameParts
# ---------------------------------------------------------------------------


def test_contact_method_invalid_type_rejected():
    with pytest.raises(ValidationError):
        ObservationContactMethod(contact_type="fax", value="12345")


def test_contact_method_empty_display_label_normalized_to_none():
    cm = ObservationContactMethod(contact_type="phone", value="555-1234", display_label="")
    assert cm.display_label is None


def test_contact_method_nonempty_display_label_preserved():
    cm = ObservationContactMethod(contact_type="phone", value="555-1234", display_label="Main")
    assert cm.display_label == "Main"


def test_address_invalid_type_rejected():
    with pytest.raises(ValidationError):
        ObservationAddress(raw_input="123 Main St", address_type="postal")


def test_address_empty_display_name_normalized_to_none():
    addr = ObservationAddress(raw_input="123 Main St", display_name="")
    assert addr.display_name is None


def test_address_nonempty_display_name_preserved():
    addr = ObservationAddress(raw_input="123 Main St", display_name="HQ")
    assert addr.display_name == "HQ"


def test_address_dateless_defaults_to_none():
    addr = ObservationAddress(raw_input="123 Main St")
    assert addr.valid_from is None
    assert addr.valid_until is None


def test_address_iso_date_strings_parsed_to_date():
    addr = ObservationAddress(
        raw_input="123 Main St", valid_from="2020-01-01", valid_until="2021-12-31"
    )
    assert addr.valid_from == date(2020, 1, 1)
    assert addr.valid_until == date(2021, 12, 31)


def test_address_valid_from_equal_until_ok():
    addr = ObservationAddress(
        raw_input="123 Main St", valid_from="2020-01-01", valid_until="2020-01-01"
    )
    assert addr.valid_from == addr.valid_until


def test_address_open_ended_windows_ok():
    frm = ObservationAddress(raw_input="123 Main St", valid_from="2020-01-01")
    assert frm.valid_from == date(2020, 1, 1)
    assert frm.valid_until is None
    until = ObservationAddress(raw_input="123 Main St", valid_until="2021-12-31")
    assert until.valid_from is None
    assert until.valid_until == date(2021, 12, 31)


def test_address_valid_from_after_until_rejected():
    with pytest.raises(ValidationError):
        ObservationAddress(
            raw_input="123 Main St", valid_from="2021-01-01", valid_until="2020-01-01"
        )


def test_name_parts_invalid_primary_identifier_rejected():
    with pytest.raises(ValidationError):
        ObservationPersonNameParts(primary_identifier="nickname")


# ---------------------------------------------------------------------------
# ObservationPersonName
# ---------------------------------------------------------------------------


def test_observation_person_name_is_canonical_defaults_false():
    n = ObservationPersonName(name="Jane Doe", name_type="legal")
    assert n.is_canonical is False


def test_observation_person_name_is_canonical_true():
    n = ObservationPersonName(name="Jane Doe", name_type="legal", is_canonical=True)
    assert n.is_canonical is True


# ---------------------------------------------------------------------------
# ObservationOrgName
# ---------------------------------------------------------------------------


def test_observation_org_name_is_canonical_defaults_false():
    n = ObservationOrgName(name="Acme Corp", name_type="legal")
    assert n.is_canonical is False


def test_observation_org_name_is_canonical_true():
    n = ObservationOrgName(name="Acme Corp", name_type="legal", is_canonical=True)
    assert n.is_canonical is True


def test_observation_org_name_accepts_effective_dates():
    n = ObservationOrgName(
        name="Committee on Old Government",
        name_type="former",
        effective_start=date(2019, 1, 1),
        effective_end=date(2023, 1, 9),
    )
    assert n.effective_start == date(2019, 1, 1)
    assert n.effective_end == date(2023, 1, 9)


def test_observation_org_name_rejects_reversed_effective_dates():
    """effective_start > effective_end is rejected at the request boundary (#239),
    mirroring RoleObservationRequest._check_date_order."""
    with pytest.raises(ValidationError):
        ObservationOrgName(
            name="Backwards Interval",
            name_type="former",
            effective_start=date(2023, 1, 9),
            effective_end=date(2019, 1, 1),
        )


def test_observation_org_name_accepts_equal_effective_dates():
    """Boundary: a single-day interval (start == end) is valid — guards the
    validator against a `>` → `>=` regression (#239)."""
    n = ObservationOrgName(
        name="One Day Committee",
        name_type="former",
        effective_start=date(2023, 1, 9),
        effective_end=date(2023, 1, 9),
    )
    assert n.effective_start == n.effective_end == date(2023, 1, 9)


# ---------------------------------------------------------------------------
# ObservationAcronym
# ---------------------------------------------------------------------------


def test_observation_acronym_defaults():
    a = ObservationAcronym(acronym="ACME")
    assert a.acronym == "ACME"
    assert a.is_canonical is False


def test_observation_acronym_canonical():
    a = ObservationAcronym(acronym="ACME", is_canonical=True)
    assert a.is_canonical is True


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


def test_people_request_single_canonical_name_ok():
    req = PeopleObservationRequest(
        identifier_type="person_wa_pdc",
        identifier_value="12345",
        names=[
            ObservationPersonName(name="Jane Doe", name_type="legal", is_canonical=True),
            ObservationPersonName(name="Jane", name_type="preferred"),
        ],
    )
    assert req.names[0].is_canonical is True
    assert req.names[1].is_canonical is False


def test_people_request_multiple_canonical_names_raises():
    with pytest.raises(ValidationError):
        PeopleObservationRequest(
            identifier_type="person_wa_pdc",
            identifier_value="12345",
            names=[
                ObservationPersonName(name="Jane Doe", name_type="legal", is_canonical=True),
                ObservationPersonName(name="Jane", name_type="preferred", is_canonical=True),
            ],
        )


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


def test_org_request_active_defaults_none():
    """active (#240) defaults to None — omitted means 'leave the flag unchanged'."""
    req = OrganizationObservationRequest(identifier_type="org_ubi", identifier_value="999")
    assert req.active is None


def test_org_request_active_accepts_bool():
    req = OrganizationObservationRequest(
        identifier_type="org_ubi", identifier_value="999", active=False
    )
    assert req.active is False


def test_org_request_acronyms_are_observation_acronym_objects():
    req = OrganizationObservationRequest(
        identifier_type="org_ubi",
        identifier_value="999",
        org_acronyms=[ObservationAcronym(acronym="ACME")],
    )
    assert len(req.org_acronyms) == 1
    assert req.org_acronyms[0].acronym == "ACME"
    assert req.org_acronyms[0].is_canonical is False


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


def test_org_request_single_canonical_name_ok():
    req = OrganizationObservationRequest(
        identifier_type="org_ubi",
        identifier_value="999",
        names=[
            ObservationOrgName(name="Acme Corp", name_type="legal", is_canonical=True),
            ObservationOrgName(name="Acme", name_type="dba"),
        ],
    )
    assert req.names[0].is_canonical is True
    assert req.names[1].is_canonical is False


def test_org_request_multiple_canonical_names_raises():
    with pytest.raises(ValidationError):
        OrganizationObservationRequest(
            identifier_type="org_ubi",
            identifier_value="999",
            names=[
                ObservationOrgName(name="Acme Corp", name_type="legal", is_canonical=True),
                ObservationOrgName(name="Acme", name_type="dba", is_canonical=True),
            ],
        )


def test_org_request_single_canonical_acronym_ok():
    req = OrganizationObservationRequest(
        identifier_type="org_ubi",
        identifier_value="999",
        org_acronyms=[
            ObservationAcronym(acronym="ACME", is_canonical=True),
            ObservationAcronym(acronym="ACM"),
        ],
    )
    assert req.org_acronyms[0].is_canonical is True
    assert req.org_acronyms[1].is_canonical is False


def test_org_request_multiple_canonical_acronyms_raises():
    with pytest.raises(ValidationError):
        OrganizationObservationRequest(
            identifier_type="org_ubi",
            identifier_value="999",
            org_acronyms=[
                ObservationAcronym(acronym="ACME", is_canonical=True),
                ObservationAcronym(acronym="ACM", is_canonical=True),
            ],
        )


# ---------------------------------------------------------------------------
# RoleObservationRequest — qualifier normalization (#261)
# ---------------------------------------------------------------------------


def test_role_request_qualifier_stripped():
    req = RoleObservationRequest(
        organization_id="org1",
        title="State Representative",
        role_type="state_representative",
        jurisdiction_id="jur1",
        qualifier="  Position 1  ",
    )
    assert req.qualifier == "Position 1"


def test_role_request_whitespace_qualifier_collapses_to_none():
    """A whitespace-only qualifier collapses to None, so it needs no jurisdiction."""
    req = RoleObservationRequest(
        organization_id="org1",
        title="Speaker",
        qualifier="   ",
    )
    assert req.qualifier is None


def test_role_request_qualifier_without_jurisdiction_raises():
    with pytest.raises(ValidationError):
        RoleObservationRequest(
            organization_id="org1",
            title="State Representative",
            qualifier="Position 1",
        )


# ---------------------------------------------------------------------------
# RoleObservationRequest — title optional with a jurisdiction (#267)
# ---------------------------------------------------------------------------


def test_role_request_structural_mode_title_optional():
    """An observation with a jurisdiction_id may omit title — PM synthesizes it."""
    req = RoleObservationRequest(
        organization_id="org1",
        role_type="state_senator",
        jurisdiction_id="jur1",
    )
    assert req.title is None


def test_role_request_structural_mode_with_qualifier_title_optional():
    req = RoleObservationRequest(
        organization_id="org1",
        role_type="state_representative",
        jurisdiction_id="jur1",
        qualifier="Position 2",
    )
    assert req.title is None
    assert req.qualifier == "Position 2"


def test_role_request_non_structural_still_requires_title():
    """No jurisdiction_id (role without a jurisdiction) → title remains required."""
    with pytest.raises(ValidationError):
        RoleObservationRequest(organization_id="org1")


def test_role_request_structural_mode_still_requires_org():
    with pytest.raises(ValidationError):
        RoleObservationRequest(role_type="state_senator", jurisdiction_id="jur1")
