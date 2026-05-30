"""Pydantic response models for the public API v1."""

from datetime import datetime

from pydantic import BaseModel, field_serializer


def _fmt_ts(v: datetime | None) -> str | None:
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
        return _fmt_ts(v)


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
        return _fmt_ts(v)


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
