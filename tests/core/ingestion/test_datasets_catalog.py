"""Reading usa-wa's dataset catalog (#496).

The catalog is served from a **private** exe.dev proxy, which is the source of
this module's least obvious failure: an unauthenticated request does not 401, it
returns 307 and an HTML login page. A puller that checks only for a 2xx will
parse that page as a catalog and report "no datasets" — a green run that fetched
nothing. Most of these tests exist for that class of lie rather than for the
happy path.
"""

import httpx
import pytest

from src.core.ingestion.datasets import (
    CatalogError,
    fetch_catalog,
    parse_catalog,
)

CATALOG = {
    "generated_at": "2026-09-09T04:34:02.497392Z",
    "datasets": [
        {
            "name": "pm_anchors",
            "tier": "cutover",
            "latest_version": "v20260909T043402Z-4f46dd",
            "schema_version": "1.5.0",
            "derived_from": [],
            "rows": 12427,
            "bytes": 793870,
            "hash": "sha256:02a619905bd6df29b34169179258f05032aa11f3f91e7bceba5d2a1ad0a612d3",
            "generated_at": "2026-09-09T04:34:02.497392Z",
        },
        {
            "name": "stg_wsl_committees",
            "tier": "staging",
            "latest_version": "v20260904T125046Z-afc2ca",
            "schema_version": "1.4.0",
            "derived_from": [],
            "rows": 667,
            "bytes": 97686,
            "hash": "sha256:ca3aba75bd647f71495d581c2dffaeec0fe48c1356871e575af5152e92d6be77",
            "generated_at": "2026-09-04T12:50:46.701326Z",
        },
    ],
}

LOGIN_PAGE = (
    b'<a href="https://usa-wa.exe.xyz:8000/__exe.dev/login?redirect=%2Fdatasets%2F'
    b'catalog.json">Temporary Redirect</a>.'
)


def _transport(handler):
    return httpx.MockTransport(handler)


def test_parses_each_entry():
    entries = {e.name: e for e in parse_catalog(CATALOG)}

    assert entries["pm_anchors"].rows == 12427
    assert entries["pm_anchors"].tier == "cutover"
    assert entries["pm_anchors"].latest_version == "v20260909T043402Z-4f46dd"


def test_strips_the_algorithm_prefix_from_the_hash():
    """The catalog states `sha256:02a6…`; a bare digest is what verification needs."""
    entry = next(e for e in parse_catalog(CATALOG) if e.name == "pm_anchors")

    assert entry.sha256 == "02a619905bd6df29b34169179258f05032aa11f3f91e7bceba5d2a1ad0a612d3"


def test_refuses_a_digest_algorithm_it_cannot_verify():
    """Keeping an unknown algorithm would fail later as a content mismatch."""
    payload = {"datasets": [dict(CATALOG["datasets"][0], hash="sha512:" + "0" * 128)]}

    with pytest.raises(CatalogError, match="unsupported digest algorithm"):
        parse_catalog(payload)


def test_refuses_an_entry_missing_a_field_it_needs():
    payload = {"datasets": [{k: v for k, v in CATALOG["datasets"][0].items() if k != "hash"}]}

    with pytest.raises(CatalogError, match="hash"):
        parse_catalog(payload)


def test_refuses_a_payload_with_no_datasets_key():
    """An empty catalog and a wrong-shaped document must not look alike."""
    with pytest.raises(CatalogError, match="datasets"):
        parse_catalog({"generated_at": "2026-09-09T04:34:02Z"})


def test_parses_the_schema_major_from_schema_version():
    entry = next(e for e in parse_catalog(CATALOG) if e.name == "pm_anchors")

    assert entry.schema_major == 1


async def test_fetch_sends_the_exe_dev_bearer_header():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(request.headers)
        return httpx.Response(200, json=CATALOG)

    async with httpx.AsyncClient(transport=_transport(handler)) as client:
        await fetch_catalog("https://usa-wa.exe.xyz:8000", token="tok", client=client)

    assert seen["x-exedev-authorization"] == "Bearer tok"


async def test_fetch_names_the_login_redirect_as_an_auth_failure():
    """The 307 trap: HTML where a catalog should be, and a 200 after redirects.

    Naming it as auth is the whole point — "no datasets" or "invalid JSON" would
    send the reader looking at usa-wa's publisher instead of at the token.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=LOGIN_PAGE, headers={"content-type": "text/html"})

    async with httpx.AsyncClient(transport=_transport(handler)) as client:
        with pytest.raises(CatalogError, match="authentication"):
            await fetch_catalog("https://usa-wa.exe.xyz:8000", token="tok", client=client)


async def test_fetch_does_not_follow_a_redirect_into_a_login_page():
    """Following it turns an auth failure into whatever the login page returns."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(307, headers={"location": "/__exe.dev/login"})

    async with httpx.AsyncClient(transport=_transport(handler)) as client:
        with pytest.raises(CatalogError, match="authentication"):
            await fetch_catalog("https://usa-wa.exe.xyz:8000", token="tok", client=client)


@pytest.mark.parametrize("status", [401, 403])
async def test_fetch_names_a_rejected_token(status):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status)

    async with httpx.AsyncClient(transport=_transport(handler)) as client:
        with pytest.raises(CatalogError, match="authentication"):
            await fetch_catalog("https://usa-wa.exe.xyz:8000", token="tok", client=client)


async def test_fetch_distinguishes_a_missing_catalog_from_a_rejected_one():
    """404 is the publisher's problem; 403 is ours. They must not read alike."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404)

    async with httpx.AsyncClient(transport=_transport(handler)) as client:
        with pytest.raises(CatalogError, match="not found"):
            await fetch_catalog("https://usa-wa.exe.xyz:8000", token="tok", client=client)


async def test_fetch_refuses_a_non_json_content_type_even_with_200():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"name,rows\n", headers={"content-type": "text/csv"})

    async with httpx.AsyncClient(transport=_transport(handler)) as client:
        with pytest.raises(CatalogError, match="content type"):
            await fetch_catalog("https://usa-wa.exe.xyz:8000", token="tok", client=client)


def test_refuses_a_dataset_name_that_would_escape_the_store():
    """The name becomes a directory that `land` mkdirs and `prune` rmtrees."""
    payload = {"datasets": [dict(CATALOG["datasets"][0], name="../../etc")]}

    with pytest.raises(CatalogError, match="unsafe name"):
        parse_catalog(payload)


def test_refuses_a_version_that_would_escape_the_store():
    payload = {"datasets": [dict(CATALOG["datasets"][0], latest_version="..")]}

    with pytest.raises(CatalogError, match="unsafe latest_version"):
        parse_catalog(payload)


def test_refuses_a_hierarchical_dataset_name():
    """A separator nests a directory that `has` and `versions` then cannot see."""
    payload = {"datasets": [dict(CATALOG["datasets"][0], name="wa/persons")]}

    with pytest.raises(CatalogError, match="unsafe name"):
        parse_catalog(payload)
