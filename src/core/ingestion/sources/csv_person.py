"""Extract, validate, and transform rows from a people CSV."""

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from src.core.db import generate_id
from src.core.ingestion.base import ConfidenceRecord, FieldError, RowResult
from src.core.normalizers.email import EmailNormalizer
from src.core.normalizers.identifier import IdentifierNormalizer
from src.core.normalizers.phone import PhoneNormalizer
from src.core.normalizers.url import UrlNormalizer

_phone = PhoneNormalizer()
_email = EmailNormalizer()
_url = UrlNormalizer()
_identifier = IdentifierNormalizer()


class PersonRow(BaseModel):
    """Pydantic model for a single people CSV row.

    Field aliases match the CSV column headers exactly.
    """

    model_config = ConfigDict(populate_by_name=True)

    name: str = Field(alias="Name")
    former_name: str | None = Field(None, alias="Former Name")
    personal_email: str | None = Field(None, alias="Personal Email")
    personal_phone: str | None = Field(None, alias="Personal Phone")
    personal_url: str | None = Field(None, alias="Personal URL")
    linkedin_url: str | None = Field(None, alias="LinkedIn URL")
    twitter_url: str | None = Field(None, alias="Twitter URL")
    mastodon_url: str | None = Field(None, alias="Mastodon URL")
    instagram_url: str | None = Field(None, alias="Instagram URL")
    wikipedia_url: str | None = Field(None, alias="Wikipedia URL")
    wa_pdc: str | None = Field(None, alias="WA PDC")
    personal_pronouns: str | None = Field(None, alias="Personal Pronouns")
    notes: str | None = Field(None, alias="Notes")

    @model_validator(mode="before")
    @classmethod
    def strip_all(cls, data: dict) -> dict:
        """Strip whitespace from all string values before field validation."""
        return {k: (v.strip() if isinstance(v, str) else v) for k, v in data.items()}

    @field_validator("name")
    @classmethod
    def name_required(cls, v: str) -> str:
        """Reject empty or whitespace-only name."""
        if not v or not v.strip():
            raise ValueError("name is required")
        return v.strip()


# Map CSV alias → Python field name for fields that tests check by name
_ALIAS_MAP: dict[str, str] = {
    "Name": "name",
    "Former Name": "former_name",
    "Personal Email": "personal_email",
    "Personal Phone": "personal_phone",
    "Personal URL": "personal_url",
    "LinkedIn URL": "linkedin_url",
    "Twitter URL": "twitter_url",
    "Mastodon URL": "mastodon_url",
    "Instagram URL": "instagram_url",
    "Wikipedia URL": "wikipedia_url",
    "WA PDC": "wa_pdc",
    "Personal Pronouns": "personal_pronouns",
    "Notes": "notes",
}


def _alias_to_field(loc: str) -> str:
    """Convert a Pydantic error location string to the Python field name."""
    return _ALIAS_MAP.get(loc, loc)


def validate_person(raw: dict[str, str], source_row: int) -> RowResult:
    """Validate a raw CSV row dict. Returns RowResult with errors if name is missing."""
    result = RowResult(source_row=source_row, raw=raw)
    try:
        validated = PersonRow.model_validate(raw)
        result.transformed = {"_validated": validated}
    except ValidationError as exc:
        for e in exc.errors():
            loc = e["loc"]
            raw_loc = str(loc[0]) if loc else "unknown"
            field_name = _alias_to_field(raw_loc)
            result.errors.append(FieldError(
                field=field_name,
                message=e["msg"],
                raw_value=raw.get(raw_loc),
            ))
    return result


async def transform_person(
    result: RowResult,
    source_reliability: float,
) -> RowResult:
    """Transform a validated person row into DB-ready dicts."""
    if not result.ok:
        return result

    validated: PersonRow = result.transformed["_validated"]
    person_id = generate_id()
    warnings: list[str] = []
    confidence_records: list[ConfidenceRecord] = []

    def _add_confidence(
        field_name: str, normalized_value: str, hint: str, detail: dict | None = None
    ) -> None:
        confidence_records.append(ConfidenceRecord(
            entity_type="person",
            entity_id=person_id,
            field_name=field_name,
            normalized_value=normalized_value,
            source_reliability=source_reliability,
            validation_status=hint,
            assessed_by="import:pending",
            validation_detail=detail,
        ))

    # Names
    names = [{"name": validated.name, "name_type": "legal", "is_canonical": True}]
    if validated.former_name and validated.former_name.strip():
        names.append({"name": validated.former_name.strip(), "name_type": "former",
                      "is_canonical": True})

    # Contact methods
    contact_methods: list[dict] = []
    if validated.personal_email:
        try:
            r = _email.normalize(validated.personal_email)
            if not r.skipped:
                contact_methods.append({"contact_type": "email", "value": r.value})
                _add_confidence("email", r.value, r.confidence_hint)
        except ValueError as exc:
            warnings.append(f"email skipped: {exc}")

    if validated.personal_phone:
        try:
            r = _phone.normalize(validated.personal_phone)
            if not r.skipped:
                contact_methods.append({"contact_type": "phone", "value": r.value})
                _add_confidence("phone", r.value, r.confidence_hint)
        except ValueError as exc:
            warnings.append(f"phone skipped: {exc}")

    # URLs
    urls: list[dict] = []
    _url_fields = [
        (validated.personal_url, "website", True),
        (validated.wikipedia_url, "wikipedia", False),
    ]
    for raw_url, url_type_slug, is_canonical in _url_fields:
        if raw_url:
            try:
                r = _url.normalize(raw_url)
                if not r.skipped:
                    urls.append({
                        "url": r.value, "url_type_slug": url_type_slug, "is_canonical": is_canonical
                    })
                    _add_confidence(f"url:{url_type_slug}", r.value, r.confidence_hint)
            except ValueError as exc:
                warnings.append(f"url skipped ({url_type_slug}): {exc}")

    # Social links
    social_links: list[dict] = []
    _social_fields = [
        (validated.linkedin_url, "linkedin"),
        (validated.twitter_url, "twitter"),
        (validated.mastodon_url, "mastodon"),
        (validated.instagram_url, "instagram"),
    ]
    for raw_url, platform_slug in _social_fields:
        if raw_url:
            try:
                r = _url.normalize(raw_url)
                if not r.skipped:
                    social_links.append({"platform_slug": platform_slug, "url": r.value})
                    _add_confidence(f"social:{platform_slug}", r.value, r.confidence_hint)
            except ValueError as exc:
                warnings.append(f"social link skipped ({platform_slug}): {exc}")

    # Identifiers
    identifiers: list[dict] = []
    if validated.wa_pdc:
        r = _identifier.normalize(validated.wa_pdc)
        if not r.skipped:
            identifiers.append({"identifier_type_slug": "person_wa_pdc", "value": r.value})
            _add_confidence("identifier:person_wa_pdc", r.value, r.confidence_hint)

    result.transformed = {
        "person_id": person_id,
        "personal_pronouns": validated.personal_pronouns,
        "notes": validated.notes,
        "names": names,
        "contact_methods": contact_methods,
        "urls": urls,
        "social_links": social_links,
        "identifiers": identifiers,
        "address": None,
        "confidence_records": confidence_records,
    }
    result.warnings = warnings
    return result
