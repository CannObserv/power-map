"""Shared helpers for entity address CRUD routers (orgs, people, and jurisdictions)."""

from dataclasses import dataclass
from datetime import date

from src.core.normalizers.address_meta import get_country_format

DATE_FORMAT_ERROR = "Dates must be YYYY-MM-DD."
VALIDITY_ORDER_ERROR = "Valid from must be on or before valid until."


@dataclass(frozen=True)
class ConfirmPersist:
    """Signal from ``_maybe_confirm`` to persist directly on a non-HTMX submit (#280).

    A JS-disabled client cannot render the confirm modal, so a ``mode="confirm"``
    submit has no follow-up ``mode="save"`` round trip. Rather than redirect away
    and silently drop the address, ``_maybe_confirm`` returns this marker carrying
    the normalizer's DB-ready values so the route inserts/updates the row and
    redirects as a genuine success.

    **Auto-accept decision (#280, CR item 1):** the values carried here are the
    normalizer's *standardized* output — i.e. the non-HTMX path implicitly takes
    the modal's "Accept standardized" branch on the curator's behalf, because a
    JS-disabled client can't be shown the accept-standardized-vs-keep-as-entered
    choice the modal offers. This is a deliberate trade: silent data *loss* (the
    old bug) is worse than silently applying standardization, and an interactive
    (HTMX) client still gets the choice. If curator intent must instead be
    preserved verbatim on the non-HTMX path, build this from the raw submitted
    values rather than ``normalized_ctx`` at the ``_maybe_confirm`` call sites.
    """

    address_line_1: str | None
    address_line_2: str | None
    city: str | None
    region: str | None
    postal_code: str | None
    country: str
    standardized: str | None
    latitude: float | None
    longitude: float | None
    components: str | None

    def as_address_columns(
        self,
    ) -> tuple[
        str | None,
        str | None,
        str | None,
        str | None,
        str | None,
        str,
        str | None,
        float | None,
        float | None,
        str | None,
    ]:
        """The 10 ``addresses`` column values in INSERT/UPDATE order (#280, CR item 3).

        Centralizes column ordering so the persist path's field unpacking lives
        in one place instead of being duplicated across the six create/edit
        routes. Order: ``address_line_1, address_line_2, city, region,
        postal_code, country, standardized, latitude, longitude, components``.
        """
        return (
            self.address_line_1,
            self.address_line_2,
            self.city,
            self.region,
            self.postal_code,
            self.country,
            self.standardized,
            self.latitude,
            self.longitude,
            self.components,
        )


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

    def as_row(self) -> dict[str, str | None]:
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
