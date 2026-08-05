"""Shared conditional-GET machinery: tolerant ``If-None-Match`` parsing (#392).

Three tiers:

1. Pure-unit tests over the header parser / matcher — the RFC 9110 grammar
   (comma-separated lists, ``W/`` weak tags, ``*``, commas *inside* a quoted
   tag) that the pre-#392 ``request.headers.get(...) == etag`` equality missed.
2. Unit tests over ``cache_headers`` / ``conditional_response``.
3. Endpoint tests proving the tolerant forms reach 304 through a real route,
   plus a source sweep asserting no public route re-implements the check.
"""

import hashlib
import os
from datetime import UTC, datetime
from pathlib import Path

import pytest
import pytest_asyncio
from fastapi import Request, Response

from src.api.public.etag import (
    cache_headers,
    conditional_response,
    if_none_match_matches,
    make_etag,
)
from src.core.db import generate_id

# ---------------------------------------------------------------------------
# if_none_match_matches — RFC 9110 §13.1.2
# ---------------------------------------------------------------------------

_ETAG = '"01ABC-1700000000000"'


@pytest.mark.parametrize(
    "header",
    [
        _ETAG,
        f"  {_ETAG}  ",
        f'"other", {_ETAG}',
        f'{_ETAG}, "other"',
        f'"a","b", {_ETAG} ,"c"',
        f"W/{_ETAG}",  # weak header tag vs strong resource tag — weak comparison
        f'"other", W/{_ETAG}',
        "*",
        " * ",
    ],
)
def test_if_none_match_matches_accepted_forms(header):
    assert if_none_match_matches(header, _ETAG) is True


@pytest.mark.parametrize(
    "header",
    [
        None,
        "",
        "   ",
        '"other"',
        '"other", "another"',
        'W/"other"',
        # A prefix/suffix of the real tag must not match.
        '"01ABC-170000000000"',
        '"01ABC-17000000000000"',
    ],
)
def test_if_none_match_matches_rejected_forms(header):
    assert if_none_match_matches(header, _ETAG) is False


def test_weak_resource_tag_compared_weakly():
    """A ``W/``-prefixed resource tag matches a strong header tag, and vice versa."""
    assert if_none_match_matches('"x-1"', 'W/"x-1"') is True
    assert if_none_match_matches('W/"x-1"', 'W/"x-1"') is True


def test_comma_inside_quoted_tag_is_not_a_separator():
    """``etagc`` admits a comma, so splitting must respect quoting."""
    weird = '"a,b"'
    assert if_none_match_matches(weird, weird) is True
    # The naive ``header.split(",")`` reading yields the fragments '"a' and 'b"',
    # neither of which is a tag — and must not match either fragment as a tag.
    assert if_none_match_matches(weird, '"a') is False
    assert if_none_match_matches(f"{weird}, {_ETAG}", _ETAG) is True


def test_star_matches_any_tag():
    assert if_none_match_matches("*", '"anything"') is True


# ---------------------------------------------------------------------------
# cache_headers
# ---------------------------------------------------------------------------


def test_cache_headers_with_last_modified():
    last = datetime(2026, 8, 5, 12, 30, 45, tzinfo=UTC)
    headers = cache_headers(_ETAG, last)
    assert headers == {
        "ETag": _ETAG,
        "Last-Modified": "Wed, 05 Aug 2026 12:30:45 GMT",
        "Cache-Control": "no-cache",
        "Vary": "X-API-Key",
    }


def test_cache_headers_without_last_modified():
    """An empty collection was never modified — no Last-Modified, ETag still revalidates."""
    headers = cache_headers(_ETAG, None)
    assert "Last-Modified" not in headers
    assert headers["ETag"] == _ETAG
    assert headers["Cache-Control"] == "no-cache"
    assert headers["Vary"] == "X-API-Key"


def test_make_etag_is_quoted_and_millisecond_precision():
    etag = make_etag("01ABC", datetime(2026, 8, 5, 12, 30, 45, 500000, tzinfo=UTC))
    assert etag.startswith('"') and etag.endswith('"')
    assert etag.endswith('500"')


# ---------------------------------------------------------------------------
# conditional_response
# ---------------------------------------------------------------------------


def _request(if_none_match: str | None) -> Request:
    headers = []
    if if_none_match is not None:
        headers.append((b"if-none-match", if_none_match.encode()))
    return Request({"type": "http", "method": "GET", "path": "/", "headers": headers})


def test_conditional_response_miss_stamps_headers_and_returns_none():
    last = datetime(2026, 8, 5, 12, 30, 45, tzinfo=UTC)
    response = Response()
    assert conditional_response(_request(None), response, _ETAG, last) is None
    assert response.headers["etag"] == _ETAG
    assert response.headers["last-modified"] == "Wed, 05 Aug 2026 12:30:45 GMT"
    assert response.headers["cache-control"] == "no-cache"
    assert response.headers["vary"] == "X-API-Key"


def test_conditional_response_hit_returns_304_with_headers():
    last = datetime(2026, 8, 5, 12, 30, 45, tzinfo=UTC)
    response = Response()
    hit = conditional_response(_request(f'"other", {_ETAG}'), response, _ETAG, last)
    assert hit is not None
    assert hit.status_code == 304
    assert hit.body == b""
    assert hit.headers["etag"] == _ETAG
    assert hit.headers["cache-control"] == "no-cache"


def test_conditional_response_hit_leaves_outer_response_unstamped():
    """The 304 carries the headers; the injected Response is discarded by the route."""
    response = Response()
    assert conditional_response(_request(_ETAG), response, _ETAG, None) is not None
    assert "etag" not in response.headers


# ---------------------------------------------------------------------------
# Sweep: no public route re-implements the check
# ---------------------------------------------------------------------------


def test_no_public_route_parses_if_none_match_directly():
    """``if-none-match`` may only be read inside the shared helper (#392).

    A route doing its own ``request.headers.get("if-none-match") == etag`` is
    strict where the helper is tolerant — a client that learns list syntax works
    on one endpoint and not another is worse than uniform strictness.
    """
    public = Path(__file__).resolve().parents[3] / "src" / "api" / "public"
    offenders = [
        path.name
        for path in sorted(public.glob("*.py"))
        if path.name != "etag.py" and "if-none-match" in path.read_text().lower()
    ]
    assert not offenders, f"modules reading If-None-Match outside etag.py: {offenders}"


# ---------------------------------------------------------------------------
# Endpoint tier — the tolerant forms reach 304 through real routes
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture(loop_scope="session")
async def api_key(db):
    uid = generate_id()
    kid = generate_id()
    raw_key = "pm_" + os.urandom(16).hex()
    await db.execute("INSERT INTO app_users (id, email) VALUES ($1,$2)", uid, "cond-get@test.com")
    await db.execute(
        "INSERT INTO api_keys (id, user_id, label, key_prefix, key_hash) VALUES ($1,$2,$3,$4,$5)",
        kid,
        uid,
        "Conditional GET Test Key",
        raw_key[:8],
        hashlib.sha256(raw_key.encode()).hexdigest(),
    )
    return raw_key


@pytest_asyncio.fixture(loop_scope="session")
async def person_id(db):
    pid = generate_id()
    await db.execute("INSERT INTO people (id) VALUES ($1)", pid)
    await db.execute(
        "INSERT INTO person_names (id, person_id, name, name_type, is_canonical, visibility)"
        " VALUES ($1,$2,'Conditional Tester','legal',TRUE,'public')",
        generate_id(),
        pid,
    )
    return pid


@pytest.mark.integration
@pytest.mark.parametrize("form", ["exact", "list", "weak", "star"])
async def test_detail_route_honours_tolerant_if_none_match(client, api_key, person_id, form):
    url = f"/api/v1/people/{person_id}"
    first = await client.get(url, headers={"X-API-Key": api_key})
    assert first.status_code == 200
    etag = first.headers["etag"]

    header = {
        "exact": etag,
        "list": f'"stale-1", {etag}, "stale-2"',
        "weak": f"W/{etag}",
        "star": "*",
    }[form]
    r = await client.get(url, headers={"X-API-Key": api_key, "If-None-Match": header})
    assert r.status_code == 304, f"{form} form did not revalidate"
    assert r.content == b""
    assert r.headers["etag"] == etag


@pytest.mark.integration
@pytest.mark.parametrize("form", ["exact", "list", "weak", "star"])
async def test_events_route_honours_tolerant_if_none_match(client, api_key, person_id, form):
    url = f"/api/v1/people/{person_id}/events"
    first = await client.get(url, headers={"X-API-Key": api_key})
    assert first.status_code == 200
    etag = first.headers["etag"]

    header = {
        "exact": etag,
        "list": f'"stale-1", {etag}',
        "weak": f"W/{etag}",
        "star": "*",
    }[form]
    r = await client.get(url, headers={"X-API-Key": api_key, "If-None-Match": header})
    assert r.status_code == 304, f"{form} form did not revalidate"


@pytest.mark.integration
async def test_stale_etag_still_returns_200(client, api_key, person_id):
    r = await client.get(
        f"/api/v1/people/{person_id}",
        headers={"X-API-Key": api_key, "If-None-Match": '"stale-1", W/"stale-2"'},
    )
    assert r.status_code == 200
