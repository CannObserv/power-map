"""Pydantic response models for the public API v1."""

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field, field_serializer, model_validator

EntityType = Literal["person", "organization", "jurisdiction", "role", "role_assignment"]


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

    created_at: datetime
    updated_at: datetime
    names: list[OrgName] = Field(default_factory=list)
    acronyms: list[OrgAcronym] = Field(default_factory=list)
    identifiers: list[OrgIdentifier] = Field(default_factory=list)

    @field_serializer("created_at", "updated_at")
    def _serialize_ts(self, v: datetime) -> str:
        return v.isoformat().replace("+00:00", "Z")


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

    created_at: datetime
    updated_at: datetime
    names: list[PersonName] = Field(default_factory=list)
    identifiers: list[PersonIdentifier] = Field(default_factory=list)
    voice_embeddings_count: int = 0

    @field_serializer("created_at", "updated_at")
    def _serialize_ts(self, v: datetime) -> str:
        return v.isoformat().replace("+00:00", "Z")


# ---------------------------------------------------------------------------
# Voice embeddings
# ---------------------------------------------------------------------------


class EmbeddingSource(BaseModel):
    """Source provenance for a voice embedding observation."""

    service: str
    job_id: str
    segment: int
    recorded_at: datetime

    @field_serializer("recorded_at")
    def _serialize_recorded_at(self, v: datetime) -> str:
        return v.isoformat().replace("+00:00", "Z")


class EmbeddingWriteRequest(BaseModel):
    """Request body for POST /api/v1/people/{id}/embeddings."""

    model_id: str
    embedding: list[float]
    activity_ms: int = Field(ge=0)
    audio_sample_rate_hz: int = Field(gt=0)
    source: EmbeddingSource


class EmbeddingWriteResponse(BaseModel):
    """Response for a successful embedding write (new or idempotent duplicate)."""

    embedding_id: str
    person_id: str
    created_at: datetime

    @field_serializer("created_at")
    def _serialize_created_at(self, v: datetime) -> str | None:
        return fmt_ts(v)


class EmbeddingArchiveResponse(BaseModel):
    """Response for single soft-delete and restore."""

    embedding_id: str
    archived_at: datetime | None

    @field_serializer("archived_at")
    def _serialize_archived_at(self, v: datetime | None) -> str | None:
        return fmt_ts(v)


class EmbeddingBatchArchiveResponse(BaseModel):
    """Response for batch soft-delete by source_job_id."""

    archived_count: int


class EmbeddingListItem(BaseModel):
    """Single row in an embedding listing response."""

    embedding_id: str
    model_id: str
    source_job_id: str
    source_segment: int
    recorded_at: datetime
    activity_ms: int
    archived_at: datetime | None
    created_at: datetime

    @field_serializer("recorded_at", "created_at", "archived_at")
    def _serialize_ts(self, v: datetime | None) -> str | None:
        return fmt_ts(v)


class EmbeddingListResponse(BaseModel):
    """Paginated listing of voice embeddings for a person."""

    data: list[EmbeddingListItem]
    meta: SearchMeta


class IdentifyRequest(BaseModel):
    """Request body for POST /api/v1/people/identify."""

    model_id: str
    embedding: list[float]
    top_k: int = 5


class IdentifyMatch(BaseModel):
    """A single candidate match returned by the identify endpoint."""

    person_id: str
    person_name: str | None
    similarity: float
    embedding_id: str
    source_job_id: str
    recorded_at: datetime

    @field_serializer("recorded_at")
    def _serialize_recorded_at(self, v: datetime) -> str | None:
        return fmt_ts(v)


class IdentifyResponse(BaseModel):
    """Response envelope for POST /api/v1/people/identify."""

    matches: list[IdentifyMatch]


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


class EntityEventType(BaseModel):
    """An entity event type used to classify life/organisational events."""

    id: str
    slug: str
    display_name: str
    applies_to: Literal["person", "organization", "both"]
    requires_year: bool
    requires_linked_entity: bool


class EntityEventTypesResponse(BaseModel):
    """Unpaginated list of all entity event types.

    Intentionally omits ``meta`` pagination — entity_event_types is a small,
    stable lookup table returned in full. No limit/offset parameters are accepted.
    """

    data: list[EntityEventType]


class ObservationNameParts(BaseModel):
    """Structured name parts supplied by upstream source (pre-parsed, not auto-decomposed)."""

    given_names: list[str] = Field(default_factory=list)
    family_names: list[str] = Field(default_factory=list)
    additional_names: list[str] = Field(default_factory=list)
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


class ObservationEventItem(BaseModel):
    """A lifecycle event claim included in an observation."""

    event_type_id: str | None = None
    event_type_slug: str | None = None  # XOR with event_type_id

    event_year: int | None = None
    event_month: int | None = None
    event_day: int | None = None
    event_hour: int | None = None
    event_minute: int | None = None
    event_second: int | None = None

    event_place_text: str | None = None
    event_place_address_id: str | None = None
    linked_entity_type: Literal["person", "organization"] | None = None
    linked_entity_id: str | None = None
    notes: str | None = None
    visibility: Literal["public", "legal_only", "hidden"] = "public"

    @model_validator(mode="after")
    def _xor_event_type(self) -> "ObservationEventItem":
        has_id = self.event_type_id is not None
        has_slug = self.event_type_slug is not None
        if has_id and has_slug:
            raise ValueError("Specify event_type_id or event_type_slug, not both")
        if not has_id and not has_slug:
            raise ValueError("One of event_type_id or event_type_slug is required")
        return self

    @model_validator(mode="after")
    def _linked_entity_pair(self) -> "ObservationEventItem":
        has_type = self.linked_entity_type is not None
        has_id = self.linked_entity_id is not None
        if has_type != has_id:
            raise ValueError(
                "linked_entity_type and linked_entity_id must both be present or both absent"
            )
        return self


class PeopleObservationRequest(BaseModel):
    """Payload for POST /api/v1/people/observations."""

    identifier_type: str
    identifier_value: str

    names: list[ObservationName] = Field(default_factory=list)
    personal_pronouns: str | None = None
    role_assignments: list[ObservationRoleAssignment] = Field(default_factory=list)
    links: list[ObservationLink] = Field(default_factory=list)
    contact_methods: list[ObservationContactMethod] = Field(default_factory=list)
    addresses: list[ObservationAddress] = Field(default_factory=list)
    additional_identifiers: list[ObservationAdditionalIdentifier] = Field(default_factory=list)
    events: list[ObservationEventItem] = Field(default_factory=list)


class OrganizationObservationRequest(BaseModel):
    """Payload for POST /api/v1/orgs/observations."""

    identifier_type: str
    identifier_value: str

    names: list[ObservationName] = Field(default_factory=list)
    org_acronyms: list[str] = Field(default_factory=list)
    organization_parent_id: str | None = None
    organization_parent_name: str | None = None
    organization_parent_acronym: str | None = None
    links: list[ObservationLink] = Field(default_factory=list)
    contact_methods: list[ObservationContactMethod] = Field(default_factory=list)
    addresses: list[ObservationAddress] = Field(default_factory=list)
    additional_identifiers: list[ObservationAdditionalIdentifier] = Field(default_factory=list)
    events: list[ObservationEventItem] = Field(default_factory=list)

    @model_validator(mode="after")
    def _xor_org_parent(self) -> "OrganizationObservationRequest":
        parent_fields = [
            self.organization_parent_id,
            self.organization_parent_name,
            self.organization_parent_acronym,
        ]
        if sum(1 for f in parent_fields if f is not None) > 1:
            raise ValueError(
                "Specify at most one of organization_parent_id, "
                "organization_parent_name, organization_parent_acronym"
            )
        return self


class ObservationResponse(BaseModel):
    """Response returned by POST /api/v1/observations."""

    disposition: str  # 'auto-attached', 'new', or 'rejected'
    entity_id: str | None = None  # None only when disposition == 'rejected'
    entity_type: EntityType | None = None  # None when rejected


class JurisdictionObservationRequest(BaseModel):
    """Payload for POST /api/v1/jurisdictions/observations."""

    # Required: identifier used for match-or-create
    identifier_type: str
    identifier_value: str

    # Required for NEW disposition; ignored on AUTO_ATTACHED
    jurisdiction_slug: str | None = None
    jurisdiction_name: str | None = None
    jurisdiction_type_slug: str | None = None

    # Applied on NEW only; silently ignored on AUTO_ATTACHED.
    # These are core entity fields on the jurisdiction row itself — unlike
    # attribute tables (links, contact_methods, etc.) they are not appended
    # on AUTO_ATTACHED, to preserve the integrity of first-submitted data.
    jurisdiction_valid_from: date | None = None
    jurisdiction_valid_until: date | None = None
    jurisdiction_notes: str | None = None

    # Generic attribute claims (same as other entity types)
    links: list[ObservationLink] = Field(default_factory=list)
    contact_methods: list[ObservationContactMethod] = Field(default_factory=list)
    addresses: list[ObservationAddress] = Field(default_factory=list)
    additional_identifiers: list[ObservationAdditionalIdentifier] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_valid_range(self) -> "JurisdictionObservationRequest":
        if (
            self.jurisdiction_valid_from is not None
            and self.jurisdiction_valid_until is not None
            and self.jurisdiction_valid_from > self.jurisdiction_valid_until
        ):
            raise ValueError("jurisdiction_valid_from must be <= jurisdiction_valid_until")
        return self

    @model_validator(mode="after")
    def _check_jur_slug_consistency(self) -> "JurisdictionObservationRequest":
        if (
            self.identifier_type == "jur_slug"
            and self.jurisdiction_slug is not None
            and self.identifier_value != self.jurisdiction_slug
        ):
            raise ValueError(
                "identifier_value must equal jurisdiction_slug when identifier_type is 'jur_slug'"
            )
        return self


class EventTypeInline(BaseModel):
    """Event type embedded in event list items."""

    id: str
    slug: str
    display_name: str


class PartialDate(BaseModel):
    """Partial date/time with explicit precision."""

    year: int | None = None
    month: int | None = None
    day: int | None = None
    hour: int | None = None
    minute: int | None = None
    second: int | None = None
    at: str | None = None  # ISO 8601 with Z suffix when event_at is populated


class EventPlaceAddress(BaseModel):
    """Structured address linked to an event place."""

    id: str
    city: str | None = None
    region: str | None = None
    standardized: str | None = None
    precision: str | None = None


class EntityEvent(BaseModel):
    """Single event item in a list response."""

    id: str
    event_type: EventTypeInline
    date: PartialDate
    event_place_text: str | None = None
    event_place_address: EventPlaceAddress | None = None
    linked_entity_type: Literal["person", "organization"] | None = None
    linked_entity_id: str | None = None
    notes: str | None = None
    visibility: Literal["public", "legal_only", "hidden"]
    verified_at: datetime | None = None
    created_at: datetime

    @field_serializer("verified_at", "created_at")
    def _serialize_ts(self, v: datetime | None) -> str | None:
        return fmt_ts(v)


class EntityEventsResponse(BaseModel):
    """Paginated list of entity events."""

    data: list[EntityEvent]
    meta: SearchMeta


class ChangeItem(BaseModel):
    """A single entry in the change feed — updated or deleted entity."""

    entity_type: EntityType
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


# ---------------------------------------------------------------------------
# Jurisdiction schemas (#168)
# ---------------------------------------------------------------------------


class JurisdictionType(BaseModel):
    """A jurisdiction type from the lookup table."""

    id: str
    slug: str
    display_name: str


class JurisdictionRelationshipType(BaseModel):
    """A jurisdiction relationship type from the lookup table."""

    id: str
    slug: str
    display_name: str
    category: str
    is_symmetric: bool


class JurisdictionIdentifier(BaseModel):
    """An external identifier attached to a jurisdiction."""

    id: str
    type_id: str
    type_slug: str
    value: str


class JurisdictionListItem(BaseModel):
    """Single item in a jurisdiction list response."""

    id: str
    slug: str
    name: str
    type: JurisdictionType
    valid_from: date | None = None
    valid_until: date | None = None
    recorded_at: datetime
    superseded_at: datetime | None = None
    created_at: datetime
    updated_at: datetime
    archived_at: datetime | None = None

    @field_serializer("recorded_at", "superseded_at", "created_at", "updated_at", "archived_at")
    def _serialize_ts(self, v: datetime | None) -> str | None:
        return fmt_ts(v)


class JurisdictionResponse(JurisdictionListItem):
    """Full jurisdiction record including identifiers."""

    identifiers: list[JurisdictionIdentifier] = Field(default_factory=list)


class JurisdictionListResponse(BaseModel):
    """Paginated list of jurisdictions."""

    data: list[JurisdictionListItem]
    meta: SearchMeta


class JurisdictionRelationship(BaseModel):
    """A typed edge in the jurisdiction graph."""

    id: str
    from_id: str
    to_id: str
    rel_type: JurisdictionRelationshipType
    valid_from: date | None = None
    valid_until: date | None = None
    recorded_at: datetime
    superseded_at: datetime | None = None
    created_at: datetime

    @field_serializer("recorded_at", "superseded_at", "created_at")
    def _serialize_ts(self, v: datetime | None) -> str | None:
        return fmt_ts(v)


class JurisdictionRelationshipsResponse(BaseModel):
    """Paginated list of jurisdiction relationships."""

    data: list[JurisdictionRelationship]
    meta: SearchMeta


class JurisdictionLineageResponse(BaseModel):
    """Ordered chain of jurisdictions returned by the lineage endpoint."""

    data: list[JurisdictionListItem]


# ---------------------------------------------------------------------------
# Role schemas (#176)
# ---------------------------------------------------------------------------


class RoleListItem(BaseModel):
    """Single item in a role list response."""

    id: str
    organization_id: str
    title: str
    notes: str | None = None
    established_on: date | None = None
    abolished_on: date | None = None
    archived_at: datetime | None = None
    created_at: datetime
    updated_at: datetime

    @field_serializer("archived_at", "created_at", "updated_at")
    def _serialize_ts(self, v: datetime | None) -> str | None:
        return fmt_ts(v)


class RoleListResponse(BaseModel):
    """Paginated list of roles."""

    data: list[RoleListItem]
    meta: SearchMeta


class RoleObservationRequest(BaseModel):
    """Payload for POST /api/v1/roles/observations."""

    organization_id: str
    title: str

    notes: str | None = None
    established_on: date | None = None
    abolished_on: date | None = None

    links: list[ObservationLink] = Field(default_factory=list)
    contact_methods: list[ObservationContactMethod] = Field(default_factory=list)
    addresses: list[ObservationAddress] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_date_order(self) -> "RoleObservationRequest":
        if (
            self.established_on is not None
            and self.abolished_on is not None
            and self.established_on > self.abolished_on
        ):
            raise ValueError("established_on must be <= abolished_on")
        return self


class RoleLink(BaseModel):
    """A link attached to a role."""

    id: str
    url: str
    link_type_id: str
    link_type_slug: str
    link_type_name: str
    is_active: bool


class RoleContactMethod(BaseModel):
    """A contact method attached to a role."""

    id: str
    contact_type: str
    value: str


class RoleAddress(BaseModel):
    """An address attached to a role."""

    id: str
    address_id: str
    address_type: str
    raw_input: str | None = None
    standardized: str | None = None


class RoleDetail(RoleListItem):
    """Full role record including links, contact methods, and addresses."""

    links: list[RoleLink] = Field(default_factory=list)
    contact_methods: list[RoleContactMethod] = Field(default_factory=list)
    addresses: list[RoleAddress] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Assignments
# ---------------------------------------------------------------------------


class AssignmentListItem(BaseModel):
    """Single item in an assignment list response."""

    id: str
    person_id: str
    role_id: str
    is_current: bool
    start_date: date | None = None
    end_date: date | None = None
    notes: str | None = None
    archived_at: datetime | None = None
    created_at: datetime
    updated_at: datetime

    @field_serializer("archived_at", "created_at", "updated_at")
    def _serialize_ts(self, v: datetime | None) -> str | None:
        return fmt_ts(v)


class AssignmentListResponse(BaseModel):
    """Paginated list of assignments."""

    data: list[AssignmentListItem]
    meta: SearchMeta


class AssignmentLink(BaseModel):
    """A link attached to a role assignment."""

    id: str
    url: str
    link_type_id: str
    link_type_slug: str
    link_type_name: str
    is_active: bool


class AssignmentContactMethod(BaseModel):
    """A contact method attached to a role assignment."""

    id: str
    contact_type: str
    value: str


class AssignmentAddress(BaseModel):
    """An address attached to a role assignment."""

    id: str
    address_id: str
    address_type: str
    raw_input: str | None = None
    standardized: str | None = None


class AssignmentDetail(AssignmentListItem):
    """Full assignment record including links, contact methods, and addresses."""

    links: list[AssignmentLink] = Field(default_factory=list)
    contact_methods: list[AssignmentContactMethod] = Field(default_factory=list)
    addresses: list[AssignmentAddress] = Field(default_factory=list)


class AssignmentObservationRequest(BaseModel):
    """Payload for POST /api/v1/assignments/observations."""

    person_id: str
    role_id: str
    start_date: date | None = None
    end_date: date | None = None
    is_current: bool = False
    notes: str | None = None

    links: list[ObservationLink] = Field(default_factory=list)
    contact_methods: list[ObservationContactMethod] = Field(default_factory=list)
    addresses: list[ObservationAddress] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_constraints(self) -> "AssignmentObservationRequest":
        if self.is_current and self.end_date is not None:
            raise ValueError("is_current cannot be True when end_date is set")
        if (
            self.start_date is not None
            and self.end_date is not None
            and self.start_date > self.end_date
        ):
            raise ValueError("start_date must be <= end_date")
        return self
