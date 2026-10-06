"""The generated client against the app: a scratch consumer's view (#618).

These tests drive ``power_map_client`` (``clients/python``) through the ASGI app.
They show that the published schema and the generated code agree on the
statuses a consumer branches on: a typed 401/403 for auth, and for a detail read
200, 304 when revalidating with ``If-None-Match``, and 410 with ``EntityGone``.
The 401/403 tests run in the unit tier, with ``get_db`` mocked. The rest need
the test DB and use the rollback connection (#288).
"""

import hashlib
import os
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from httpx import ASGITransport
from power_map_client import Client, connect
from power_map_client.generated.api.public_api import get_org, search_orgs
from power_map_client.generated.models import (
    EntityGone,
    ErrorDetail,
    OrgDetail,
    OrgSearchResponse,
)

from src.api.deps import get_db
from src.api.main import app
from src.core.db import generate_id

_BASE = "http://test"


def _sdk(api_key: str):
    return connect(_BASE, api_key, httpx_args={"transport": ASGITransport(app=app)})


@pytest.fixture
def no_such_key():
    """get_db whose key lookup finds nothing, so auth answers 401."""

    async def _db():
        conn = AsyncMock()
        conn.fetchrow.return_value = None
        yield conn

    app.dependency_overrides[get_db] = _db
    yield
    app.dependency_overrides.pop(get_db, None)


async def test_bad_key_is_a_typed_401(no_such_key):
    r = await get_org.asyncio_detailed(org_id=generate_id(), client=_sdk("pm_not_a_key"))
    assert r.status_code == 401
    assert r.parsed == ErrorDetail(detail="Invalid API key")


async def test_missing_key_is_a_typed_403(no_such_key):
    anonymous = Client(base_url=_BASE, httpx_args={"transport": ASGITransport(app=app)})
    r = await search_orgs.asyncio_detailed(client=anonymous, q="x")
    assert r.status_code == 403
    assert isinstance(r.parsed, ErrorDetail)


# ---------------------------------------------------------------------------
# Integration: search, and a detail read's 200 / 304 / 410
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture(loop_scope="session")
async def sdk(db):
    """A connected client whose requests share the rolled-back test connection."""
    uid, kid = generate_id(), generate_id()
    raw_key = "pm_" + os.urandom(16).hex()
    await db.execute("INSERT INTO app_users (id, email) VALUES ($1,$2)", uid, "sdk@test.com")
    await db.execute(
        "INSERT INTO api_keys (id, user_id, label, key_prefix, key_hash) VALUES ($1,$2,$3,$4,$5)",
        kid,
        uid,
        "SDK Test Key",
        raw_key[:8],
        hashlib.sha256(raw_key.encode()).hexdigest(),
    )

    async def _get_db_override():
        yield db

    app.dependency_overrides[get_db] = _get_db_override
    async with _sdk(raw_key) as client:
        yield client
    app.dependency_overrides.pop(get_db, None)


async def _org(db, name: str) -> str:
    org_id = generate_id()
    await db.execute("INSERT INTO organizations (id) VALUES ($1)", org_id)
    await db.execute(
        "INSERT INTO organization_names (id, organization_id, name, name_type, is_canonical)"
        " VALUES ($1,$2,$3,'legal',TRUE)",
        generate_id(),
        org_id,
        name,
    )
    return org_id


@pytest.mark.integration
async def test_search_orgs_is_typed(sdk, db):
    org_id = await _org(db, "Quillfeather Sdk Probe Commission")
    r = await search_orgs.asyncio_detailed(client=sdk, q="Quillfeather Sdk Probe")
    assert r.status_code == 200
    assert isinstance(r.parsed, OrgSearchResponse)
    assert [o.id for o in r.parsed.data] == [org_id]


@pytest.mark.integration
async def test_get_org_200_then_304(sdk, db):
    org_id = await _org(db, "Quillfeather Sdk Revalidation Board")

    first = await get_org.asyncio_detailed(org_id=org_id, client=sdk)
    assert first.status_code == 200
    assert isinstance(first.parsed, OrgDetail)
    assert first.parsed.id == org_id

    again = await get_org.asyncio_detailed(
        org_id=org_id, client=sdk, if_none_match=first.headers["etag"]
    )
    assert again.status_code == 304
    assert again.parsed is None


@pytest.mark.integration
async def test_get_org_410_names_the_winner(sdk, db):
    winner = await _org(db, "Quillfeather Sdk Survivor")
    loser = generate_id()
    await db.execute(
        "INSERT INTO deleted_entities (entity_type, entity_id, merged_into)"
        " VALUES ('organization',$1,$2)",
        loser,
        winner,
    )

    r = await get_org.asyncio_detailed(org_id=loser, client=sdk)

    assert r.status_code == 410
    assert isinstance(r.parsed, EntityGone)
    assert r.parsed.merged_into == winner


@pytest.mark.integration
async def test_unknown_org_is_a_typed_404(sdk):
    r = await get_org.asyncio_detailed(org_id=generate_id(), client=sdk)
    assert r.status_code == 404
    assert isinstance(r.parsed, ErrorDetail)
