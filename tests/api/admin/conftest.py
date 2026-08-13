"""Shared fixtures for admin route tests.

Includes the browser-tier session fixtures (#300) — ``browser_db``,
``seeded_ids``, ``live_server``, ``browser``, ``page`` — hoisted from
``test_a11y_browser.py`` (#426) so sibling browser-test files (#367/#368) can
share them. They are inert unless a ``-m browser`` test requests them, and
Playwright is imported lazily inside the ``browser`` fixture, so the fast
non-browser suite never requires the browser extra.
"""

import asyncio
import os
import socket
import subprocess
import sys
import tempfile
from urllib.parse import urlsplit

import asyncpg
import httpx
import pytest
import pytest_asyncio
from fastapi.testclient import TestClient

from src.api.main import app
from src.core.db import apply_schema
from src.core.embedding_registry import EmbeddingRegistry
from tests.api.admin.admin_routes import AUTH_HEADERS as SWEEP_AUTH_HEADERS
from tests.api.admin.admin_routes import seed_admin_fixtures
from tests.db_utils import reset_data_tables

AUTH_HEADERS = {
    "X-ExeDev-UserID": "usr_test123",
    "X-ExeDev-Email": "admin@example.com",
}


@pytest_asyncio.fixture(scope="session", loop_scope="session", autouse=True)
async def _init_app_embedding_registry():
    """Populate ``app.state.embedding_registry`` once per session.

    Admin route tests drive the app lifespan-less (#288), so the lifespan's
    ``app.state.embedding_registry`` initialization never runs. Person detail /
    name pages read ``request.app.state.embedding_registry``; without this they
    raise ``AttributeError``. Mirrors the lifespan: load from the DB when a test
    DB is configured, else an empty registry (matches the lifespan's no-DSN
    branch, keeps DB-free unit runs working).
    """
    test_url = os.environ.get("TEST_DATABASE_URL")
    if test_url:
        pool = await asyncpg.create_pool(test_url, min_size=1, max_size=1)
        try:
            async with pool.acquire() as conn:
                app.state.embedding_registry = await EmbeddingRegistry.load(conn)
        finally:
            await pool.close()
    else:
        app.state.embedding_registry = EmbeddingRegistry({})
    yield


# Canonical entity ordering across the admin shell (#275): Jurisdiction, Org,
# Person, Role, Assignment. Asserted in the sidebar nav (base.html), the
# dashboard cards, and the entities landing cards so the three surfaces never
# drift out of sync. Href strings are unique per surface region, so
# first-occurrence position reflects render order.
ENTITY_ORDER_HREFS = [
    'href="/admin/jurisdictions/"',
    'href="/admin/orgs/"',
    'href="/admin/people/"',
    'href="/admin/roles/"',
    'href="/admin/role-assignments/"',
]


def assert_render_order(haystack, needles):
    """Assert each needle appears in ``haystack`` and their first-occurrence
    positions are strictly increasing (i.e. rendered in ``needles`` order)."""
    seen = []
    for needle in needles:
        idx = haystack.find(needle)
        assert idx != -1, f"{needle!r} not found in rendered output"
        seen.append((idx, needle))
    actual = [n for _, n in sorted(seen)]
    assert actual == needles, f"Expected order {needles}, got {actual}"


async def jurisdiction_change_count(db_pool, jurisdiction_id):
    """Count change-feed rows recorded for a jurisdiction (shared test helper)."""
    async with db_pool.acquire() as conn:
        return await conn.fetchval(
            "SELECT COUNT(*) FROM entity_changes WHERE entity_type='jurisdiction' AND entity_id=$1",
            jurisdiction_id,
        )


@pytest.fixture
def client():
    """TestClient without DB pool (no lifespan). Auth + routing tests only.

    Constructed without `with`, so the app lifespan never runs and no app pool
    is created (#288) — matching this fixture's DB-free contract. Consumers that
    need a real DB define their own rollback client (see ``test_orgs.py``).
    """
    return TestClient(app, raise_server_exceptions=False)


# --- Browser-tier session fixtures (#300, hoisted from test_a11y_browser.py in
# #426). Only `-m browser` tests request these; nothing below runs during the
# fast non-browser suite.


def _free_port() -> int:
    """Grab an ephemeral port. Small TOCTOU window between close and uvicorn
    bind, acceptable for a single-VM serial test run."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def browser_db():
    """Prepare the dedicated test DB for the out-of-process sweep and reset it on
    teardown. Yields the DSN the live server will use.

    Guard: refuses any dbname that isn't the test database — ``co_pm_db_production``
    lives on the same server, and this fixture TRUNCATEs."""
    dsn = os.environ.get("TEST_DATABASE_URL")
    if not dsn:
        pytest.skip("TEST_DATABASE_URL not set — see docs/COMMANDS.md")
    dbname = urlsplit(dsn).path.lstrip("/")
    if "test" not in dbname or "prod" in dbname:
        pytest.fail(
            f"refusing to run the browser tier against non-test database {dbname!r} —"
            " it truncates data tables; point TEST_DATABASE_URL at the test DB"
        )
    conn = await asyncpg.connect(dsn)
    try:
        await apply_schema(conn)  # absorb any schema.sql change on this branch
        await reset_data_tables(conn)
    finally:
        await conn.close()
    try:
        yield dsn
    finally:
        conn = await asyncpg.connect(dsn)
        try:
            await reset_data_tables(conn)
        finally:
            await conn.close()


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def seeded_ids(browser_db):
    """Seed one entity per type (committed, so the server process sees it) and
    return the id map for path-param fill."""
    conn = await asyncpg.connect(browser_db)
    try:
        return await seed_admin_fixtures(conn)
    finally:
        await conn.close()


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def live_server(browser_db, seeded_ids):
    """Launch uvicorn on an ephemeral port against the seeded test DB, wait for
    the #343 /health probe, and tear it down. Returns the base URL."""
    port = _free_port()
    env = os.environ.copy()
    env["DATABASE_URL"] = browser_db
    env["DB_POOL_MIN_SIZE"] = "1"
    env["DB_POOL_MAX_SIZE"] = "4"
    # Capture the child's output to a temp file (not a PIPE we'd never drain — at
    # --log-level warning volume is tiny, but a file can't deadlock) so a startup
    # failure surfaces the actual traceback, not a bare exit code (CR #4).
    # Closed (and, being a NamedTemporaryFile, deleted) in the finally below.
    log_file = tempfile.NamedTemporaryFile(mode="w+", suffix=".uvicorn.log", prefix="a11y-browser-")

    def _log_tail() -> str:
        log_file.flush()
        log_file.seek(0)
        return "".join(log_file.readlines()[-40:]).rstrip()

    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "src.api.main:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--log-level",
            "warning",
        ],
        env=env,
        stdout=log_file,
        stderr=subprocess.STDOUT,
    )
    base_url = f"http://127.0.0.1:{port}"
    try:
        async with httpx.AsyncClient() as c:
            for _ in range(120):  # up to ~30s
                if proc.poll() is not None:
                    raise RuntimeError(
                        f"uvicorn exited early with code {proc.returncode}:\n{_log_tail()}"
                    )
                try:
                    r = await c.get(f"{base_url}/health", timeout=1.0)
                    if r.status_code == 200:
                        break
                except httpx.HTTPError:
                    pass
                await asyncio.sleep(0.25)
            else:
                raise RuntimeError(f"uvicorn did not become ready within 30s:\n{_log_tail()}")
        yield base_url
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)
        log_file.close()


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def browser():
    """Session-scoped headless Chromium. Playwright is imported lazily here —
    this conftest is imported by every admin test, and only `-m browser` tests
    may require the browser extra (default `uv run` syncs only the dev group)."""
    playwright_async = pytest.importorskip(
        "playwright.async_api",
        reason="install the browser group: uv sync --group browser && playwright install chromium",
    )
    async with playwright_async.async_playwright() as p:
        b = await p.chromium.launch()
        try:
            yield b
        finally:
            await b.close()


@pytest_asyncio.fixture(loop_scope="session")
async def page(browser):
    """Fresh page per test, authenticated with the sweep's admin headers."""
    context = await browser.new_context(extra_http_headers=SWEEP_AUTH_HEADERS)
    pg = await context.new_page()
    try:
        yield pg
    finally:
        await context.close()
