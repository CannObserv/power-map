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
"""

from datetime import datetime
from types import MappingProxyType
from typing import Final

from fastapi import Request, Response

# RFC 9110 §5.6.7 IMF-fixdate — the only format a Last-Modified header may use.
HTTP_DATE_FMT: Final = "%a, %d %b %Y %H:%M:%S GMT"

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
        headers["Last-Modified"] = last_modified.strftime(HTTP_DATE_FMT)
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
