"""Rendered-DOM a11y sweep over every admin GET route (GH #246).

Authoritative complement to the static template lint (``test_aria_labels.py``):
fetches each admin GET route through the lifespan-less rollback client (#288),
so the checks in ``tests.api.admin.a11y`` run against **resolved output** —
includes expanded, ids materialized — closing the three #244 blind spots.

Coverage is programmatic: routes are enumerated from ``app.routes`` (no
hand-picked "representative views"), path params filled from one seeded entity
per type. A new admin GET route is swept automatically; if its params can't be
filled the test fails loudly rather than silently skipping. The enumeration +
seed live in ``tests.api.admin.admin_routes`` so the #300 Playwright/axe browser
tier reuses the identical route list and dataset (neither tier can drift).

Checks per route:

- response is 200 (the sweep must actually render every route);
- every input/select/textarea resolves an accessible name via real ancestry;
- on full-page documents only: every ``<label for>`` / ``aria-labelledby`` /
  ``aria-describedby`` reference resolves to an existing id. HTMX fragments are
  exempt from the id-resolution check — they may legitimately reference ids
  rendered by the parent page (that is #244 blind spot 2).
"""

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from src.api.admin.deps import get_db
from src.api.main import app
from tests.api.admin.a11y import (
    controls_missing_accessible_name,
    count_controls,
    dangling_id_refs,
    is_full_document,
)
from tests.api.admin.admin_routes import (
    ADMIN_GET_PATHS,
    AUTH_HEADERS,
    EXTRA_HEADERS,
    QUERY_PARAMS,
    param_values,
    seed_admin_fixtures,
)

pytestmark = [
    pytest.mark.integration,
]

# Aggregate control-coverage guard. Each rendered route adds its control count
# to this accumulator; a module-teardown fixture asserts the total clears a
# floor, but only when the *full* sweep ran (a filtered `-k` subset skips the
# check). Catches a mass regression — a form that silently stops rendering
# controls would still pass every per-route check vacuously (#246 CR). Floor is
# well below the ~345 currently rendered, so it flags a collapse, not drift.
#
# xdist caveat: these are process-global counters. Under `pytest-xdist` (not
# used yet — see #288) each worker runs only a shard, so `_routes_executed`
# never reaches len(ADMIN_GET_PATHS) on any worker and the floor check silently
# no-ops. Before enabling xdist, move the aggregate to a cross-worker mechanism
# (e.g. a `pytest_sessionfinish` hook) or the backstop disappears without warning.
_MIN_TOTAL_CONTROLS = 250
_control_total = 0
_routes_executed = 0


@pytest_asyncio.fixture(scope="module", loop_scope="session")
async def db(db_pool):
    """Module-scoped connection in one rolled-back transaction.

    The sweep is read-only (GETs), so all ~150 parametrized cases share one
    seed + connection instead of re-seeding per test."""
    async with db_pool.acquire() as conn:
        tr = conn.transaction()
        await tr.start()
        try:
            yield conn
        finally:
            await tr.rollback()


@pytest_asyncio.fixture(scope="module", loop_scope="session")
async def client(db):
    async def _get_db_override():
        yield db

    app.dependency_overrides[get_db] = _get_db_override
    # follow_redirects=False on purpose: a route that 3xx's must fail the 200
    # gate, not silently pass by following the redirect to some other page
    # (which masks HTMX-only partials — a 303 to the list page still 200s).
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test", follow_redirects=False
    ) as c:
        yield c
    app.dependency_overrides.pop(get_db, None)


@pytest_asyncio.fixture(scope="module", loop_scope="session")
async def seed(db):
    """One entity per type plus one row per sub-resource — enough to fill every
    path param in ADMIN_GET_PATHS."""
    return await seed_admin_fixtures(db)


@pytest.fixture(scope="module", autouse=True)
def _assert_control_floor():
    """After the module's tests run, assert aggregate control coverage cleared
    the floor — but only when the whole sweep ran (a filtered subset can't reach
    it and would false-fail)."""
    yield
    if _routes_executed == len(ADMIN_GET_PATHS):
        assert _control_total >= _MIN_TOTAL_CONTROLS, (
            f"rendered only {_control_total} controls across {_routes_executed} routes"
            f" (floor {_MIN_TOTAL_CONTROLS}) — a form may have silently stopped rendering"
        )


@pytest.mark.parametrize("path", ADMIN_GET_PATHS)
async def test_admin_route_renders_accessible_dom(path, client, seed):
    global _control_total, _routes_executed
    _routes_executed += 1
    url = path.format_map(param_values(path, seed)) + QUERY_PARAMS.get(path, "")
    resp = await client.get(url, headers=AUTH_HEADERS | EXTRA_HEADERS.get(path, {}))
    assert resp.status_code == 200, f"{url} -> {resp.status_code}: {resp.text[:300]}"

    if "text/html" not in resp.headers.get("content-type", ""):
        return  # nothing to check on non-HTML responses (e.g. JSON vectors)

    html = resp.text
    _control_total += count_controls(html)

    missing = controls_missing_accessible_name(html)
    assert not missing, f"{url}: controls missing accessible name:\n  " + "\n  ".join(missing)

    if is_full_document(html):
        dangling = dangling_id_refs(html)
        assert not dangling, f"{url}: unresolved id references:\n  " + "\n  ".join(dangling)
