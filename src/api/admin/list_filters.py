"""Shared HX-Current-URL list-filter parser for admin list + merge flows.

A merge POST carries no query string of its own, so the list-flow merge
branches re-derive the user's active filters from the ``HX-Current-URL`` header
(the URL the user is currently on). People and Orgs share the parsing logic and
differ only in their valid status set — this module is the single source of
truth so the two can't drift (the same rationale behind ``people_queries.py`` /
``orgs_queries.py`` and the ``merge-mode.js`` factory).
"""

from urllib.parse import parse_qs, urlsplit

from fastapi import Request

_PAGE_SIZE_MIN = 10
_PAGE_SIZE_MAX = 500


def parse_list_filters(
    request: Request, *, valid_statuses: set[str], default_page_size: int = 50
) -> dict:
    """Parse ``q`` / ``status`` / ``page`` / ``page_size`` out of HX-Current-URL.

    Falls back to defaults silently on a missing or malformed header — the merge
    already succeeded; surfacing a parse error here would be needlessly noisy.

    Args:
        request: the merge POST request; only ``request.headers`` is read.
        valid_statuses: the entity's allowed ``status`` values. An unknown or
            empty status falls back to ``"active"``. (People is two-valued;
            Orgs adds the org-only ``"inactive"``.)
        default_page_size: used as the default and as the fallback when the
            parsed value is missing, non-numeric, or outside [10, 500].
    """
    defaults = {"q": "", "status": "active", "page": 1, "page_size": default_page_size}
    raw = request.headers.get("HX-Current-URL", "")
    if not raw:
        return defaults
    try:
        params = parse_qs(urlsplit(raw).query)
    except ValueError:
        return defaults

    q = (params.get("q", [""])[0] or "").strip()
    status = (params.get("status", ["active"])[0] or "active").lower()
    if status not in valid_statuses:
        status = "active"
    try:
        page = max(1, int(params.get("page", ["1"])[0]))
    except (TypeError, ValueError):
        page = 1
    try:
        page_size = int(params.get("page_size", [str(default_page_size)])[0])
    except (TypeError, ValueError):
        page_size = default_page_size
    if not _PAGE_SIZE_MIN <= page_size <= _PAGE_SIZE_MAX:
        page_size = default_page_size
    return {"q": q, "status": status, "page": page, "page_size": page_size}
