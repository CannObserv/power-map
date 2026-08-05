"""Shared conditional-GET machinery for the public API (#292, #392).

One home for everything a route needs to answer ``If-None-Match``: the strong
detail-resource tag (:func:`make_etag`), the response header set
(:func:`cache_headers`), the RFC 9110 header match (:func:`if_none_match_matches`),
and the route-facing wrapper that ties them together
(:func:`conditional_response`).

Routes must never read the ``if-none-match`` header themselves — the pre-#392
sites each did raw string equality against the whole header, which fails a
comma-separated list, a ``W/``-prefixed weak tag, and ``*``. Fine for a direct
server-to-server client, wrong the moment a proxy is interposed; and a client
that learns list syntax works on one endpoint but not another is a nastier bug
than uniform strictness. ``tests/api/public/test_conditional_get.py`` sweeps for
re-implementations.

Revalidation is **ETag-only by decision**: ``If-Modified-Since`` is not honored,
so a request carrying it (and no matching ``If-None-Match``) gets a full 200.
The ``Last-Modified`` we emit is informational — a second, date-based
revalidation path is a second thing to keep correct, and no consumer has asked
for one. Documented in ``docs/PUBLIC_API.md`` § Conditional requests.
"""

import hashlib
from collections.abc import Iterable
from datetime import UTC, datetime
from email.utils import format_datetime
from types import MappingProxyType
from typing import Any, Final

from fastapi import Request, Response

# OpenAPI declaration for endpoints supporting If-None-Match revalidation
# (#292 CR): spread into the route decorator via ``responses=NOT_MODIFIED``.
# Immutable (MappingProxyType + Final): shared by every conditional GET route —
# an in-place mutation anywhere would silently change them all.
NOT_MODIFIED: Final = MappingProxyType(
    {304: {"description": "Not modified — the If-None-Match ETag still matches."}}
)


def make_etag(entity_id: str, updated_at: datetime) -> str:
    """Return a strong ETag for a detail resource: ``"<id>-<updated_at_ms>"``."""
    ts_ms = int(updated_at.timestamp() * 1000)
    return f'"{entity_id}-{ts_ms}"'


def _encode_param(value: object) -> str:
    """Render one filter param as an unambiguous ETag token.

    ``-`` is the tag's own separator, so it is escaped inside a value — without
    that, ``(field="a", limit=1)`` and ``(field="a-1", limit=None)`` could fuse
    into the same tag and a filter change would revalidate as unchanged.
    ``None`` (filter absent) gets its own token so it can never be confused with
    the empty string.
    """
    if value is None:
        return "~"
    if isinstance(value, bool):
        return "T" if value else "F"
    return str(value).replace("%", "%25").replace("-", "%2D")


def collection_etag(prefix: str, count: int, last: datetime | None, *params: object) -> str:
    """Watermark validator for a filtered collection: count + max(updated_at).

    Count catches a row entering or leaving the *filtered* set — an archived or
    hidden row's own ``updated_at`` bump is invisible once the filter excludes
    it — and the watermark catches an in-place edit. Every param that changes
    the response body (filters *and* the pagination window) is baked in, so a
    different window can never revalidate against another's tag.

    ``last=None`` (empty collection) renders a ``0`` watermark: still stable,
    still revalidatable — the dominant poll case is exactly the empty/unchanged
    one. Callers must compute *count* and *last* over the same ``WHERE`` clause
    the body uses, minus ``LIMIT``/``OFFSET``.
    """
    last_ms = int(last.timestamp() * 1000) if last is not None else 0
    tail = "".join(f"-{_encode_param(p)}" for p in params)
    return f'"{prefix}-{last_ms}-{count}{tail}"'


def catalog_validator(rows: Iterable[Any]) -> str:
    """Content-hash validator for a small, fully-materialized resource.

    For a table with no ``updated_at`` to watermark (`role_types`, `link_types`,
    `entity_event_types`): a ``count(*)`` + ``max(created_at)`` tag would be
    *stable across an in-place rename*, and `link_types` is admin-editable —
    a 304ing consumer would hold the stale ``display_name`` indefinitely.

    Hashes the fetched rows, so it is exact by construction. The honest
    trade-off: this saves serialization and transfer, **not** the query — the
    rows must be fetched to compute it. On a catalog of tens of rows that is the
    whole win anyway. Keys are hashed alongside values so a column rename is
    caught, and ``repr`` keeps ``None``/``""`` and ``True``/``1`` distinct.

    Row order is part of the representation (every caller has an ``ORDER BY``).
    No prefix is needed: ETags are scoped per-URL, so two catalogs cannot
    collide even on identical content.
    """
    digest = hashlib.sha256()
    for row in rows:
        digest.update(repr(tuple(row.items())).encode())
        digest.update(b"\x1e")
    return f'"{digest.hexdigest()[:32]}"'


def http_date(value: datetime) -> str:
    """Format *value* as an RFC 9110 §5.6.7 IMF-fixdate.

    Not ``strftime("%a, %d %b …")``: ``%a`` and ``%b`` are **locale-dependent**,
    so a single ``setlocale(LC_TIME, …)`` anywhere in the process would emit an
    invalid HTTP-date from every conditional GET at once (CR #392). Nothing here
    calls it today — but the formatter is single-sourced, so harden it once.
    ``email.utils.format_datetime`` is locale-independent by construction.

    A naive input is *stamped* UTC rather than converted, per the project's
    all-UTC rule — ``astimezone`` alone would silently read it as host-local.
    An offset-aware input is converted, not relabelled: the pre-CR code wrote
    the wall clock of whatever offset it was handed and appended ``GMT``.
    """
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return format_datetime(value.astimezone(UTC), usegmt=True)


def cache_headers(etag: str, last_modified: datetime | None = None) -> dict[str, str]:
    """Header set every conditional GET carries, on both the 200 and the 304.

    ``Last-Modified`` is omitted when *last_modified* is None — an empty
    collection was never modified, yet its ETag still revalidates (the dominant
    poll case is exactly the unchanged/empty one).
    """
    headers = {
        "ETag": etag,
        "Cache-Control": "no-cache",
        "Vary": "X-API-Key",
    }
    if last_modified is not None:
        headers["Last-Modified"] = http_date(last_modified)
    return headers


def _split_etags(header: str) -> list[str]:
    """Split an entity-tag list on commas that sit outside a quoted string.

    ``etagc`` (RFC 9110 §8.8.3) admits a comma, so ``"a,b"`` is one tag, not
    two. The grammar has no escape sequence inside the quoted part, so a plain
    quote-toggle is a complete reading.
    """
    tags: list[str] = []
    buf: list[str] = []
    in_quotes = False
    for ch in header:
        if ch == '"':
            in_quotes = not in_quotes
            buf.append(ch)
        elif ch == "," and not in_quotes:
            tags.append("".join(buf).strip())
            buf = []
        else:
            buf.append(ch)
    tags.append("".join(buf).strip())
    return [t for t in tags if t]


def _opaque(tag: str) -> str:
    """Strip the weak-validator prefix — GET uses the weak comparison function."""
    return tag[2:] if tag.startswith("W/") else tag


def if_none_match_matches(header: str | None, etag: str) -> bool:
    """Does *header* revalidate *etag* under RFC 9110 §13.1.2?

    Handles the three forms raw equality misses: a comma-separated tag list,
    ``W/``-prefixed weak tags (``If-None-Match`` on GET compares weakly, so
    ``W/"x"`` and ``"x"`` are a match), and ``*`` (matches any current
    representation — every caller here has already established the resource
    exists by the time it asks).
    """
    if not header:
        return False
    header = header.strip()
    if header == "*":
        return True
    target = _opaque(etag)
    return any(_opaque(tag) == target for tag in _split_etags(header))


def conditional_response(
    request: Request,
    response: Response,
    etag: str,
    last_modified: datetime | None = None,
) -> Response | None:
    """Return a 304 when the request revalidates, else stamp *response* and return None.

    Route usage::

        etag = make_etag(row["id"], row["updated_at"])
        cached = conditional_response(request, response, etag, row["updated_at"])
        if cached is not None:
            return cached

    On a miss the headers are stamped onto the injected *response* (FastAPI
    merges them into the serialized body response) and the route goes on to do
    its detail fetches — the point of the early return is skipping exactly that
    work.
    """
    headers = cache_headers(etag, last_modified)
    if if_none_match_matches(request.headers.get("if-none-match"), etag):
        return Response(status_code=304, headers=headers)
    for key, value in headers.items():
        response.headers[key] = value
    return None
