"""Pydantic response models for the public API v1."""

from datetime import datetime

from pydantic import BaseModel, field_serializer


def _fmt_ts(v: datetime | None) -> str | None:
    """Serialize a UTC datetime to ISO 8601 with Z suffix."""
    return v.isoformat().replace("+00:00", "Z") if v else None


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


class OrgSearchMeta(BaseModel):
    """Pagination metadata for search responses."""

    limit: int
    offset: int
    count: int
    has_more: bool


class OrgSearchResponse(BaseModel):
    """Envelope for paginated org search results."""

    data: list[OrgSearchResult]
    meta: OrgSearchMeta


class OrgName(BaseModel):
    id: str
    name: str
    name_type: str
    is_canonical: bool


class OrgAcronym(BaseModel):
    id: str
    acronym: str
    is_canonical: bool


class OrgIdentifier(BaseModel):
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
    meta: OrgSearchMeta


class PersonName(BaseModel):
    id: str
    name: str
    name_type: str
    locale: str | None = None
    is_canonical: bool


class PersonIdentifier(BaseModel):
    id: str
    type_id: str
    type_slug: str
    value: str


class PersonDetail(PersonSearchResult):
    """Full person record including public name variants and identifiers."""

    names: list[PersonName] = []
    identifiers: list[PersonIdentifier] = []
