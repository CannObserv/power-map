"""Extract, validate, and transform rows from Organizations.csv."""

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from src.core.db import generate_id
from src.core.ingestion.base import ConfidenceRecord, FieldError, RowResult
from src.core.normalizers.address import FallbackAddressNormalizer
from src.core.normalizers.email import EmailNormalizer
from src.core.normalizers.identifier import IdentifierNormalizer
from src.core.normalizers.phone import PhoneNormalizer
from src.core.normalizers.url import UrlNormalizer

_phone = PhoneNormalizer()
_email = EmailNormalizer()
_url = UrlNormalizer()
_identifier = IdentifierNormalizer()
_address = FallbackAddressNormalizer()  # no config → local only; set config for external


class OrgRow(BaseModel):
    """Pydantic model for a single Organizations.csv row.

    Field aliases match the CSV column headers exactly.
    """

    model_config = ConfigDict(populate_by_name=True)

    name: str = Field(alias="Name")
    parent_organization: str | None = Field(None, alias="Parent Organization")
    acronym: str | None = Field(None, alias="Acronym")
    active: str | None = Field(None, alias="Active?")
    ubi: str | None = Field(None, alias="UBI")
    wslcb_license: str | None = Field(None, alias="WSLCB License")
    wa_pdc: str | None = Field(None, alias="WA PDC")
    sec_form_d: str | None = Field(None, alias="SEC Form D")
    primary_url: str | None = Field(None, alias="Primary URL")
    org_url: str | None = Field(None, alias="Organization URL")
    linkedin_url: str | None = Field(None, alias="LinkedIn URL")
    twitter_url: str | None = Field(None, alias="Twitter URL")
    bluesky_url: str | None = Field(None, alias="Bluesky URL")
    mastodon_url: str | None = Field(None, alias="Mastodon URL")
    instagram_url: str | None = Field(None, alias="Instagram URL")
    facebook_url: str | None = Field(None, alias="Facebook URL")
    youtube_url: str | None = Field(None, alias="YouTube URL")
    flickr_url: str | None = Field(None, alias="Flickr URL")
    email: str | None = Field(None, alias="Email Address")
    phone: str | None = Field(None, alias="Phone")
    mailing_address: str | None = Field(None, alias="Mailing Address")
    notes: str | None = Field(None, alias="Notes")
    google_drive: str | None = Field(None, alias="Google Drive")

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


def validate_org(raw: dict[str, str], source_row: int) -> RowResult:
    """Validate a raw CSV row dict. Returns RowResult with errors if name is missing."""
    result = RowResult(source_row=source_row, raw=raw)
    try:
        validated = OrgRow.model_validate(raw)
        result.transformed = {"_validated": validated}
    except ValidationError as exc:
        for e in exc.errors():
            loc = e["loc"]
            # loc[0] may be the alias (e.g. "Name") or the Python field name (e.g. "name")
            raw_loc = str(loc[0]) if loc else "unknown"
            # Normalize alias → Python field name for consistent error reporting
            field_name = _alias_to_field(raw_loc)
            result.errors.append(FieldError(
                field=field_name,
                message=e["msg"],
                raw_value=raw.get(raw_loc),
            ))
    return result


# Map CSV alias → Python field name for fields that tests check by name
_ALIAS_MAP: dict[str, str] = {
    "Name": "name",
    "Parent Organization": "parent_organization",
    "Acronym": "acronym",
    "Active?": "active",
    "UBI": "ubi",
    "WSLCB License": "wslcb_license",
    "WA PDC": "wa_pdc",
    "SEC Form D": "sec_form_d",
    "Primary URL": "primary_url",
    "Organization URL": "org_url",
    "LinkedIn URL": "linkedin_url",
    "Twitter URL": "twitter_url",
    "Bluesky URL": "bluesky_url",
    "Mastodon URL": "mastodon_url",
    "Instagram URL": "instagram_url",
    "Facebook URL": "facebook_url",
    "YouTube URL": "youtube_url",
    "Flickr URL": "flickr_url",
    "Email Address": "email",
    "Phone": "phone",
    "Mailing Address": "mailing_address",
    "Notes": "notes",
    "Google Drive": "google_drive",
}


def _alias_to_field(loc: str) -> str:
    """Convert a Pydantic error location string to the Python field name."""
    return _ALIAS_MAP.get(loc, loc)


async def transform_org(
    result: RowResult,
    org_index: dict[str, str],
    source_reliability: float,
    address_normalizer: FallbackAddressNormalizer | None = None,
) -> RowResult:
    """Transform a validated org row into DB-ready dicts. Calls address normalizer (async)."""
    if not result.ok:
        return result

    validated: OrgRow = result.transformed["_validated"]
    org_id = generate_id()
    warnings: list[str] = []
    confidence_records: list[ConfidenceRecord] = []

    def _add_confidence(
        field_name: str, normalized_value: str, hint: str, detail: dict | None = None
    ) -> None:
        confidence_records.append(ConfidenceRecord(
            entity_type="organization",
            entity_id=org_id,
            field_name=field_name,
            normalized_value=normalized_value,
            source_reliability=source_reliability,
            validation_status=hint,
            assessed_by="import:pending",  # batch_id filled in by pipeline
            validation_detail=detail,
        ))

    # Active flag
    active = _parse_active(validated.active)

    # Parent org lookup
    parent_id: str | None = None
    if validated.parent_organization:
        parent_id = org_index.get(validated.parent_organization.strip().lower())
        if parent_id is None:
            warnings.append(f"parent org not found: {validated.parent_organization!r}")

    # Names (legal only; acronym goes to organization_acronyms)
    names = [{"name": validated.name, "name_type": "legal", "is_canonical": True}]
    acronym: str | None = validated.acronym or None

    # Contact methods
    contact_methods: list[dict] = []
    if validated.email:
        try:
            r = _email.normalize(validated.email)
            if not r.skipped:
                contact_methods.append({"contact_type": "email", "value": r.value})
                _add_confidence("email", r.value, r.confidence_hint)
        except ValueError as exc:
            warnings.append(f"email skipped: {exc}")

    if validated.phone:
        try:
            r = _phone.normalize(validated.phone)
            if not r.skipped:
                contact_methods.append({"contact_type": "phone", "value": r.value})
                _add_confidence("phone", r.value, r.confidence_hint)
        except ValueError as exc:
            warnings.append(f"phone skipped: {exc}")

    # Links (unified: URLs + social)
    links: list[dict] = []
    _url_fields = [
        (validated.primary_url, "website"),
        (validated.org_url, "website"),
        (validated.sec_form_d, "sec_form_d"),
        (validated.google_drive, "google_drive"),
    ]
    for raw_url, link_type_slug in _url_fields:
        if raw_url:
            try:
                r = _url.normalize(raw_url)
                if not r.skipped:
                    links.append({
                        "url": r.value,
                        "link_type_slug": link_type_slug,
                    })
                    _add_confidence(f"url:{link_type_slug}", r.value, r.confidence_hint)
            except ValueError as exc:
                warnings.append(f"url skipped ({link_type_slug}): {exc}")

    _social_fields = [
        (validated.linkedin_url, "linkedin"),
        (validated.twitter_url, "twitter"),
        (validated.bluesky_url, "bluesky"),
        (validated.mastodon_url, "mastodon"),
        (validated.instagram_url, "instagram"),
        (validated.facebook_url, "facebook"),
        (validated.youtube_url, "youtube"),
        (validated.flickr_url, "flickr"),
    ]
    for raw_url, link_type_slug in _social_fields:
        if raw_url:
            try:
                r = _url.normalize(raw_url)
                if not r.skipped:
                    links.append({
                        "url": r.value, "link_type_slug": link_type_slug,
                    })
                    _add_confidence(f"social:{link_type_slug}", r.value, r.confidence_hint)
            except ValueError as exc:
                warnings.append(f"social link skipped ({link_type_slug}): {exc}")

    # Identifiers
    identifiers: list[dict] = []
    _id_fields = [
        (validated.ubi, "org_ubi"),
        (validated.wslcb_license, "org_wslcb"),
        (validated.wa_pdc, "org_wa_pdc"),
    ]
    for raw_val, slug in _id_fields:
        if raw_val:
            r = _identifier.normalize(raw_val)
            if not r.skipped:
                identifiers.append({"identifier_type_slug": slug, "value": r.value})
                _add_confidence(f"identifier:{slug}", r.value, r.confidence_hint)

    # Address
    addr_normalizer = address_normalizer or _address
    address: dict | None = None
    if validated.mailing_address:
        addr_result = await addr_normalizer.normalize(validated.mailing_address)
        if not addr_result.skipped:
            address = addr_result.value
            _add_confidence("address", str(address.get("standardized") or address.get("raw_input")),
                            addr_result.confidence_hint, addr_result.validation_detail)
            warnings.extend(addr_result.warnings)

    result.transformed = {
        "org_id": org_id,
        "active": active,
        "parent_id": parent_id,
        "notes": validated.notes,
        "names": names,
        "acronym": acronym,
        "contact_methods": contact_methods,
        "links": links,
        "identifiers": identifiers,
        "address": address,
        "confidence_records": confidence_records,
    }
    result.warnings = warnings
    return result


def _parse_active(raw: str | None) -> bool:
    """Parse active flag from CSV string to bool."""
    if raw is None:
        return True
    return raw.strip().lower() in ("yes", "true", "1", "y")
