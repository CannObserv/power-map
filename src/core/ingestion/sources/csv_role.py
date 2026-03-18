"""Extract, validate, and transform rows from Roles.csv."""

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


class RoleRow(BaseModel):
    """Pydantic model for a single Roles.csv row."""

    model_config = ConfigDict(populate_by_name=True)

    name: str = Field(alias="Name")
    organization: str = Field(alias="Organization")
    title: str = Field(alias="Title")
    current_role: str | None = Field(None, alias="Current Role?")
    org_email: str | None = Field(None, alias="Organization Email")
    org_profile_url: str | None = Field(None, alias="Organization Profile URL")
    bluesky_url: str | None = Field(None, alias="Bluesky URL")
    twitter_url: str | None = Field(None, alias="Twitter URL")
    facebook_url: str | None = Field(None, alias="Facebook URL")
    wa_pdc: str | None = Field(None, alias="WA PDC")
    work_phone: str | None = Field(None, alias="Work Phone")
    notes: str | None = Field(None, alias="Notes")

    @model_validator(mode="before")
    @classmethod
    def strip_all(cls, data: dict) -> dict:
        """Strip whitespace from all string values before field validation."""
        return {k: (v.strip() if isinstance(v, str) else v) for k, v in data.items()}

    @field_validator("name", "organization", "title")
    @classmethod
    def required_fields(cls, v: str, info) -> str:
        """Reject empty or whitespace-only required fields."""
        if not v or not v.strip():
            raise ValueError(f"{info.field_name} is required")
        return v.strip()


_ALIAS_MAP: dict[str, str] = {
    "Name": "name",
    "Organization": "organization",
    "Title": "title",
    "Current Role?": "current_role",
    "Organization Email": "org_email",
    "Organization Profile URL": "org_profile_url",
    "Bluesky URL": "bluesky_url",
    "Twitter URL": "twitter_url",
    "Facebook URL": "facebook_url",
    "WA PDC": "wa_pdc",
    "Work Phone": "work_phone",
    "Notes": "notes",
}


def _alias_to_field(loc: str) -> str:
    """Convert a Pydantic error location string to the Python field name."""
    return _ALIAS_MAP.get(loc, loc)


def validate_role(raw: dict[str, str], source_row: int) -> RowResult:
    """Validate a raw CSV row dict. Returns RowResult with errors if required fields are missing."""
    result = RowResult(source_row=source_row, raw=raw)
    try:
        validated = RoleRow.model_validate(raw)
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


def transform_role(
    result: RowResult,
    org_index: dict[str, str],
    person_index: dict[str, str],
    role_index: dict[tuple, str],
    source_reliability: float,
) -> RowResult:
    """Transform a validated role row into DB-ready dicts (sync — no address normalization)."""
    if not result.ok:
        return result

    validated: RoleRow = result.transformed["_validated"]
    warnings: list[str] = []
    confidence_records: list[ConfidenceRecord] = []

    # Resolve org
    org_id = org_index.get(validated.organization.strip().lower())
    if org_id is None:
        result.errors.append(FieldError(
            field="organization",
            message=f"org not found in index: {validated.organization!r}",
            raw_value=validated.organization,
        ))
        return result

    # Resolve person
    person_id = person_index.get(validated.name.strip().lower())
    if person_id is None:
        result.errors.append(FieldError(
            field="name",
            message=f"person not found in index: {validated.name!r}",
            raw_value=validated.name,
        ))
        return result

    # Role dedup
    role_key = (org_id, validated.title.strip().lower())
    existing_role_id = role_index.get(role_key)
    if existing_role_id:
        role_id = existing_role_id
        role_action = "matched"
    else:
        role_id = generate_id()
        role_action = "created"

    assignment_id = generate_id()
    is_current = _parse_current(validated.current_role)

    def _add_confidence(
        field_name: str, normalized_value: str, hint: str, detail: dict | None = None
    ) -> None:
        confidence_records.append(ConfidenceRecord(
            entity_type="role_assignment",
            entity_id=assignment_id,
            field_name=field_name,
            normalized_value=normalized_value,
            source_reliability=source_reliability,
            validation_status=hint,
            assessed_by="import:pending",
            validation_detail=detail,
        ))

    # Contact methods
    contact_methods: list[dict] = []
    if validated.org_email:
        try:
            r = _email.normalize(validated.org_email)
            if not r.skipped:
                contact_methods.append({"contact_type": "email", "value": r.value})
                _add_confidence("email", r.value, r.confidence_hint)
        except ValueError as exc:
            warnings.append(f"email skipped: {exc}")

    if validated.work_phone:
        try:
            r = _phone.normalize(validated.work_phone)
            if not r.skipped:
                contact_methods.append({"contact_type": "phone", "value": r.value})
                _add_confidence("phone", r.value, r.confidence_hint)
        except ValueError as exc:
            warnings.append(f"phone skipped: {exc}")

    # URLs
    urls: list[dict] = []
    if validated.org_profile_url:
        try:
            r = _url.normalize(validated.org_profile_url)
            if not r.skipped:
                urls.append({"url": r.value, "url_type_slug": "profile", "is_canonical": True})
                _add_confidence("url:profile", r.value, r.confidence_hint)
        except ValueError as exc:
            warnings.append(f"url skipped (profile): {exc}")

    # Social links
    social_links: list[dict] = []
    for raw_url, platform_slug in [
        (validated.bluesky_url, "bluesky"),
        (validated.twitter_url, "twitter"),
        (validated.facebook_url, "facebook"),
    ]:
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
            identifiers.append({"identifier_type_slug": "role_wa_pdc", "value": r.value})
            _add_confidence("identifier:role_wa_pdc", r.value, r.confidence_hint)

    result.transformed = {
        "role_id": role_id,
        "role_action": role_action,
        "assignment_id": assignment_id,
        "org_id": org_id,
        "person_id": person_id,
        "title": validated.title.strip(),
        "is_current": is_current,
        "notes": validated.notes,
        "contact_methods": contact_methods,
        "urls": urls,
        "social_links": social_links,
        "identifiers": identifiers,
        "confidence_records": confidence_records,
    }
    result.warnings = warnings
    return result


def _parse_current(raw: str | None) -> bool:
    """Parse 'Current Role?' field to bool. Defaults True if absent."""
    if raw is None:
        return True
    return raw.strip().lower() in ("yes", "true", "1", "y")
