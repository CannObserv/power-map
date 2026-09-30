"""Shared helpers for entity address CRUD routers (orgs, people, and jurisdictions)."""

from dataclasses import dataclass
from datetime import date

from src.core.normalizers.address_meta import get_country_format
from src.core.normalizers.base import NormalizationResult

DATE_FORMAT_ERROR = "Dates must be YYYY-MM-DD."
VALIDITY_ORDER_ERROR = "Valid from must be on or before valid until."

# FallbackAddressNormalizer's validation_detail["fallback"] → flash-body suffix (#589).
_FALLBACK_NOTICES = {
    "unavailable": "Not standardized: the address service is unavailable.",
    "rejected": "Not standardized: the address service couldn't read it.",
}


@dataclass(frozen=True)
class ConfirmPersist:
    """Signal from ``_maybe_confirm`` to persist directly on a non-HTMX submit (#280).

    A non-HTMX client gets no confirm modal rendered, so a ``mode="confirm"``
    submit has no follow-up ``mode="save"`` round trip. Rather than redirect away
    and silently drop the address, ``_maybe_confirm`` returns this marker carrying
    the normalizer's DB-ready values so the route inserts/updates the row and
    redirects as a genuine success.

    This serves non-HTMX *clients* — the tests, ``curl``, any server-to-server
    caller — not JS-disabled browsers: the address form is itself only reachable
    via ``hx-get .../addresses/new-row/``, so a browser without JS never gets
    here at all. The admin requires JS by policy (#287, see
    ``docs/ADMIN.md § JavaScript is required``).

    **Auto-accept decision (#280, CR item 1):** the values carried here are the
    normalizer's *standardized* output — i.e. the non-HTMX path implicitly takes
    the modal's "Accept standardized" branch on the curator's behalf, because a
    non-HTMX client can't be shown the accept-standardized-vs-keep-as-entered
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
class NothingToConfirm:
    """Signal from ``_maybe_confirm``: no standardized form, so save as submitted.

    ``notice`` is set when that is because the normalizer fell back to the local
    parser (#589), so the save's flash can say the address went in unstandardized.
    """

    notice: str | None = None


def fallback_notice(result: NormalizationResult) -> str | None:
    """Curator-facing sentence for a normalizer fallback, or None if none happened.

    A config-less (local-only) run and a validator that answered without a
    standardized form both carry no ``fallback`` marker, so neither gets a notice.
    """
    detail = result.validation_detail or {}
    return _FALLBACK_NOTICES.get(detail.get("fallback"))


def saved_flash_body(done: str, notice: str | None) -> str:
    """HX-Trigger flash body for a create/edit: ``done``, plus the fallback notice."""
    return f"{done} {notice}" if notice else done


def saved_flash_key(notice: str | None) -> str:
    """Non-HTMX ``with_flash`` key for a create/edit: ``saved`` or its unstandardized twin.

    The static ``?flash=`` key can't carry the reason, so both reasons share one key.
    """
    return "saved_unstandardized" if notice else "saved"


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
