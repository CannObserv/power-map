"""Pydantic response models for the public API v1."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, field_serializer, model_validator


def fmt_ts(v: datetime | None) -> str | None:
    """Serialize a UTC datetime to ISO 8601 with Z suffix."""
    return v.isoformat().replace("+00:00", "Z") if v else None


def make_etag(entity_id: str, updated_at: datetime) -> str:
    """Return a strong ETag for a detail resource: ``"<id>-<updated_at_ms>"``."""
    ts_ms = int(updated_at.timestamp() * 1000)
    return f'"{entity_id}-{ts_ms}"'


class OrgSearchResult(BaseModel):
    """Single item in a search response."""

    id: str
    name: str | None = None
    acronym: str | None = None
    slug: str | None = None
    parent_id: str | None = None
    archived_at: datetime | None = None

    @field_serializer("archived_at")
    def _serialize_archived_at(self, v: datetime | None) -> str | None:
        return fmt_ts(v)


class SearchMeta(BaseModel):
    """Pagination metadata shared by all search responses."""

    limit: int
    offset: int
    count: int
    has_more: bool


class OrgSearchResponse(BaseModel):
    """Envelope for paginated org search results."""

    data: list[OrgSearchResult]
    meta: SearchMeta


class OrgName(BaseModel):
    """A single name variant for an organization."""

    id: str
    name: str
    name_type: str
    is_canonical: bool


class OrgAcronym(BaseModel):
    """A single acronym for an organization."""

    id: str
    acronym: str
    is_canonical: bool


class OrgIdentifier(BaseModel):
    """An external identifier attached to an organization."""

    id: str
    type_id: str
    type_slug: str
    value: str


class OrgDetail(OrgSearchResult):
    """Full org record including name variants, acronyms, and identifiers."""

    names: list[OrgName] = []
    acronyms: list[OrgAcronym] = []
    identifiers: list[OrgIdentifier] = []


class PersonSearchResult(BaseModel):
    """Single item in a people search response."""

    id: str
    display_name: str | None = None
    archived_at: datetime | None = None

    @field_serializer("archived_at")
    def _serialize_archived_at(self, v: datetime | None) -> str | None:
        return fmt_ts(v)


class PersonSearchResponse(BaseModel):
    """Envelope for paginated people search results."""

    data: list[PersonSearchResult]
    meta: SearchMeta


class PersonName(BaseModel):
    """A single public name variant for a person."""

    id: str
    name: str
    name_type: str
    locale: str | None = None
    is_canonical: bool


class PersonIdentifier(BaseModel):
    """An external identifier attached to a person."""

    id: str
    type_id: str
    type_slug: str
    value: str


class PersonDetail(PersonSearchResult):
    """Full person record including public name variants and identifiers."""

    names: list[PersonName] = []
    identifiers: list[PersonIdentifier] = []


class LinkType(BaseModel):
    """A link type used to categorise web URLs attached to entities."""

    id: str
    slug: str
    display_name: str
    is_social: bool


class LinkTypesResponse(BaseModel):
    """Unpaginated list of all link types.

    Intentionally omits ``meta`` pagination — link_types is a small, stable
    lookup table returned in full. No limit/offset parameters are accepted.
    """

    data: list[LinkType]


class ObservationNameParts(BaseModel):
    """Structured name parts supplied by upstream source (pre-parsed, not auto-decomposed)."""

    given_names: list[str] = []
    family_names: list[str] = []
    additional_names: list[str] = []
    honorific_prefix: str | None = None
    honorific_suffix: str | None = None
    primary_identifier: Literal["family", "given", "patronymic", "mononym"] | None = None


class ObservationName(BaseModel):
    """A name claim included in an observation."""

    name: str
    name_type: Literal[
        "legal",
        "preferred",
        "alias",
        "former",
        "initials",
        "maiden",
        "religious",
        "stage",
        "deadname",
        "reading",
        "romanization",
        "mrz",
        "variant",
    ] = "legal"
    locale: str | None = None  # BCP 47
    script: str | None = None  # ISO 15924
    sort_as: str | None = None
    parts: ObservationNameParts | None = None  # upstream-supplied structure only


class ObservationLink(BaseModel):
    """A web URL claim included in an observation."""

    url: str
    link_type_id: str | None = None  # XOR with link_type_slug
    link_type_slug: str | None = None

    @model_validator(mode="after")
    def _xor_link_type(self) -> "ObservationLink":
        has_id = self.link_type_id is not None
        has_slug = self.link_type_slug is not None
        if has_id and has_slug:
            raise ValueError("Specify link_type_id or link_type_slug, not both")
        if not has_id and not has_slug:
            raise ValueError("One of link_type_id or link_type_slug is required")
        return self


class ObservationContactMethod(BaseModel):
    """A contact method claim (email or phone)."""

    contact_type: Literal["email", "phone"]
    value: str  # raw value — normalized before storage
    display_label: str | None = None


class ObservationAddress(BaseModel):
    """An address claim included in an observation."""

    raw_input: str
    address_type: Literal["mailing", "physical", "other"] = "other"
    display_name: str | None = None  # optional label, e.g. "Seattle Office"


class ObservationRoleAssignment(BaseModel):
    """A role assignment claim — references an existing role by its power-map ID."""

    role_id: str  # power-map ULID
    start_date: str | None = None  # ISO 8601 date string, YYYY-MM-DD
    end_date: str | None = None


class ObservationAdditionalIdentifier(BaseModel):
    """An additional identifier claim to attach to the resolved entity."""

    identifier_type_slug: str
    identifier_value: str


class ObservationRequest(BaseModel):
    """Payload sent to POST /api/v1/observations."""

    # Required
    identifier_type: str
    identifier_value: str

    # Optional attribute claims — names
    names: list[ObservationName] = []

    # Optional attribute claims — links
    links: list[ObservationLink] = []

    # Optional attribute claims — contact methods
    contact_methods: list[ObservationContactMethod] = []

    # Optional attribute claims — addresses
    addresses: list[ObservationAddress] = []

    # Optional attribute claims — org only
    org_acronyms: list[str] = []
    organization_parent_id: str | None = None
    organization_parent_name: str | None = None
    organization_parent_acronym: str | None = None

    # Optional attribute claims — person only
    personal_pronouns: str | None = None

    # Optional attribute claims — role assignments
    role_assignments: list[ObservationRoleAssignment] = []

    # Optional attribute claims — additional identifiers (same-type conflict → rejected)
    additional_identifiers: list[ObservationAdditionalIdentifier] = []

    @model_validator(mode="after")
    def _xor_org_parent(self) -> "ObservationRequest":
        parent_fields = [
            self.organization_parent_id,
            self.organization_parent_name,
            self.organization_parent_acronym,
        ]
        non_none = sum(1 for f in parent_fields if f is not None)
        if non_none > 1:
            raise ValueError(
                "Specify at most one of organization_parent_id, "
                "organization_parent_name, organization_parent_acronym"
            )
        return self


class ObservationResponse(BaseModel):
    """Response returned by POST /api/v1/observations."""

    disposition: str  # 'auto-attached', 'new', or 'rejected'
    entity_id: str | None = None  # None only when disposition == 'rejected'
    entity_type: str | None = None  # 'person' or 'organization'; None when rejected


class ChangeItem(BaseModel):
    """A single entry in the change feed — updated or deleted entity."""

    entity_type: Literal["person", "organization"]
    entity_id: str
    changed_at: datetime
    change_kind: Literal["updated", "deleted"]
    archived_at: datetime | None = None

    @field_serializer("changed_at", "archived_at")
    def _serialize_ts(self, v: datetime | None) -> str | None:
        return fmt_ts(v)


class ChangeMeta(BaseModel):
    """Pagination metadata for the change feed."""

    limit: int
    count: int
    has_more: bool
    next_since: str  # ISO 8601 Z — pass as ?since= on the next poll


class ChangeFeedResponse(BaseModel):
    """Response envelope for GET /api/v1/changes."""

    data: list[ChangeItem]
    meta: ChangeMeta
