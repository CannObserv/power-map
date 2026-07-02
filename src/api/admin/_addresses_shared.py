"""Shared helpers for entity address CRUD routers (orgs and people)."""

from dataclasses import dataclass
from datetime import date

from src.core.normalizers.address_meta import get_country_format

DATE_FORMAT_ERROR = "Dates must be YYYY-MM-DD."
VALIDITY_ORDER_ERROR = "Valid from must be on or before valid until."


@dataclass(frozen=True)
class AddressEchoParams:
    """In-progress structured-field values echoed back on a country change (#258).

    Consumed as a FastAPI query-param dependency (``Depends()``) on the
    ``country-format`` routes: ``hx-include="closest form"`` sends the form's
    current values, and ``as_row()`` reshapes them into the ``a`` context the
    fields partial expects so the swap re-labels fields without blanking them.
    """

    address_line_1: str = ""
    address_line_2: str = ""
    city: str = ""
    region: str = ""
    postal_code: str = ""
    addr_id: str = ""

    def as_row(self) -> dict:
        """Shape as the partial's ``a`` context; blank ``addr_id`` → ``id=None`` (new row)."""
        return {
            "id": self.addr_id or None,
            "address_line_1": self.address_line_1,
            "address_line_2": self.address_line_2,
            "city": self.city,
            "region": self.region,
            "postal_code": self.postal_code,
        }


async def field_context(country: str | None) -> dict:
    """Return field_labels and field_visible template context for a country code.

    Normalizes raw form/query input (strip + upper); blank or None falls back to US.
    """
    code = (country or "").strip().upper() or "US"
    fmt = await get_country_format(code)
    return {
        "field_labels": {f["key"]: f["label"] for f in fmt.get("fields", [])},
        "field_visible": {f["key"] for f in fmt.get("fields", [])},
    }


def parse_validity(valid_from: str, valid_until: str) -> tuple[date | None, date | None]:
    """Parse validity window form fields; blank = open-ended on that side.

    Raises ValueError carrying a user-facing message: DATE_FORMAT_ERROR on
    non-ISO input, VALIDITY_ORDER_ERROR on an inverted range.
    """
    try:
        vf = date.fromisoformat(valid_from.strip()) if valid_from.strip() else None
        vu = date.fromisoformat(valid_until.strip()) if valid_until.strip() else None
    except ValueError:
        raise ValueError(DATE_FORMAT_ERROR) from None
    if vf and vu and vf > vu:
        raise ValueError(VALIDITY_ORDER_ERROR)
    return vf, vu
