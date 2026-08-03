"""Real-browser axe-core a11y sweep over every full-page admin route (GH #300).

The browser tier that the render-based lxml sweep (#246, ``test_a11y_render``)
can't reach: axe-core's full ruleset — colour contrast, ARIA role validity,
landmark structure, focus order — needs a real browser DOM, not resolved HTML.

Scope (v1): **full pages only.** Each admin GET route is navigated in headless
Chromium; HTMX fragments and routes that redirect (HTMX-only partials served
without an ``HX-Request`` header) are skipped — they render standalone in the
lxml tier but are not meaningful documents in a browser. axe-after-interaction
(open edit rows, modals) and real-browser flow smoke are deliberate follow-ups.

Reuses the single route enumeration + seed dataset from ``admin_routes`` (#300
step 0) so this tier and the lxml tier never drift.

**Isolation.** The BEGIN/ROLLBACK client (#288) can't cross the uvicorn process
boundary, and the design's ideal — a disposable ``CREATE DATABASE`` per session
— is unavailable: the managed-Postgres test role has no ``CREATEDB`` privilege.
So v1 uses the same truncate-and-seed pattern ``db_pool`` already applies to the
dedicated test DB at session start: reset data tables, seed once (committed so
the out-of-process server sees it), sweep, reset again on teardown. Safe because
the tier is marker-gated (``-m browser``) and runs in isolation — never
alongside the integration suite. A dbname guard refuses anything but the test DB.

Run (never in pre-commit; one-time browser install required)::

    uv sync --group browser && uv run --group browser playwright install chromium
    uv run --env-file /etc/power-map/.env --env-file .env pytest \\
        tests/api/admin/test_a11y_browser.py -m browser
"""

import asyncio
import hashlib
import os
import socket
import subprocess
import sys
import tempfile
from pathlib import Path
from urllib.parse import urlsplit

import asyncpg
import httpx
import pytest
import pytest_asyncio

from src.core.db import apply_schema
from tests.api.admin.a11y import is_full_document
from tests.api.admin.admin_routes import (
    ADMIN_GET_PATHS,
    AUTH_HEADERS,
    EXTRA_HEADERS,
    QUERY_PARAMS,
    param_values,
    seed_admin_fixtures,
)
from tests.db_utils import reset_data_tables

# Skip the whole module cleanly when the browser extra isn't installed (default
# `uv run` syncs only the `dev` group, so Playwright is absent there).
playwright_async = pytest.importorskip(
    "playwright.async_api",
    reason="install the browser group: uv sync --group browser && playwright install chromium",
)

pytestmark = [pytest.mark.browser]

# Routes that only render a partial for an HX-Request and otherwise 400 / 303 —
# HTMX-only, never standalone browser pages, so skipped. Every *other* route must
# render a real 200 (full page or a 200 fragment); an unexpected redirect/non-200
# there is a failure, not a silent skip (CR #2).
_HTMX_ONLY_PATHS = frozenset(EXTRA_HEADERS)

# Vacuous-pass guard (CR #1): if a global break (auth, base template) made every
# route redirect/error/skip, the sweep would pass with zero axe runs. Count the
# pages axe actually ran on and, when the *whole* parametrized set executed,
# assert the count clears a floor well below the ~28 currently swept — mirrors
# the lxml tier's `_MIN_TOTAL_CONTROLS` guard. A filtered `-k` subset can't reach
# the full-set count, so the check no-ops there instead of false-failing.
_MIN_FULL_PAGES_SWEPT = 20
_axe_pages_swept = 0
_cases_run = 0

# SHA-pinned vendored axe-core (see tests/vendor/README.md). Verified at import
# so a corrupted or silently-swapped copy fails loudly, not with garbage results.
_AXE_PATH = Path(__file__).parents[2] / "vendor" / "axe-core-4.10.2.min.js"
_AXE_SHA256 = "b511cd9dec01c76f4b2ad1723b66b6db37d4c2eb4ed199076e1829d9ee7b75e3"
_AXE_SOURCE = _AXE_PATH.read_text(encoding="utf-8")
if hashlib.sha256(_AXE_SOURCE.encode("utf-8")).hexdigest() != _AXE_SHA256:
    raise RuntimeError(
        f"{_AXE_PATH.name} SHA-256 mismatch — vendored axe-core is corrupt or was swapped;"
        " re-download the pinned version (see tests/vendor/README.md)"
    )

# In-page axe run. Restrict to violations; return only the fields the failure
# message needs (rule id, impact, help URL, and a css selector per node).
_AXE_RUN_JS = """
async () => {
  const r = await axe.run(document, { resultTypes: ['violations'] });
  return r.violations.map(v => ({
    id: v.id,
    impact: v.impact,
    help: v.help,
    helpUrl: v.helpUrl,
    nodes: v.nodes.map(n => n.target.join(' ')),
  }));
}
"""


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
    log_file = tempfile.NamedTemporaryFile(  # noqa: SIM115 — closed in finally
        mode="w+", suffix=".uvicorn.log", prefix="a11y-browser-"
    )

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
    async with playwright_async.async_playwright() as p:
        b = await p.chromium.launch()
        try:
            yield b
        finally:
            await b.close()


@pytest_asyncio.fixture(loop_scope="session")
async def page(browser):
    context = await browser.new_context(extra_http_headers=AUTH_HEADERS)
    pg = await context.new_page()
    try:
        yield pg
    finally:
        await context.close()


def _format_violations(url: str, violations: list[dict]) -> str:
    lines = [f"{url}: {len(violations)} axe-core violation(s):"]
    for v in violations:
        lines.append(f"  [{v['impact']}] {v['id']} — {v['help']} ({v['helpUrl']})")
        for target in v["nodes"][:5]:
            lines.append(f"      at: {target}")
        if len(v["nodes"]) > 5:
            lines.append(f"      … +{len(v['nodes']) - 5} more node(s)")
    return "\n".join(lines)


@pytest.fixture(scope="module", autouse=True)
def _assert_pages_swept_floor():
    """After the module runs, assert axe actually ran on a floor of full pages —
    but only when the whole parametrized set executed (a filtered subset can't
    reach it and would false-fail). Catches a vacuous pass where a global break
    makes every route skip/fail out of the axe path (CR #1)."""
    yield
    if _cases_run == len(ADMIN_GET_PATHS):
        assert _axe_pages_swept >= _MIN_FULL_PAGES_SWEPT, (
            f"axe ran on only {_axe_pages_swept} full pages across {_cases_run} routes"
            f" (floor {_MIN_FULL_PAGES_SWEPT}) — pages may have silently stopped rendering"
            " as full documents (auth/base-template break?)"
        )


@pytest.mark.parametrize("path", ADMIN_GET_PATHS)
async def test_admin_full_page_axe_clean(path, live_server, seeded_ids, page):
    """Navigate each admin GET route; run axe-core on the ones that are real full
    pages. HTMX-only routes and 200 fragments are out of v1 scope — but an
    *unexpected* redirect/non-200 on any other route fails (CR #2)."""
    global _axe_pages_swept, _cases_run
    _cases_run += 1
    if path in _HTMX_ONLY_PATHS:
        pytest.skip(f"{path} is HTMX-only (needs HX-Request) — not a standalone browser page")

    url = live_server + path.format_map(param_values(path, seeded_ids)) + QUERY_PARAMS.get(path, "")
    resp = await page.goto(url, wait_until="domcontentloaded")
    assert resp is not None, f"no response for {url}"

    # Every non-HTMX-only route must render a real page. A silent redirect or
    # error here (e.g. auth broke → 307 to login) must fail, not skip, so it
    # can't drop out of coverage unnoticed.
    assert resp.request.redirected_from is None, (
        f"{path} unexpectedly redirected to {resp.url} — a full-page admin route must not redirect"
    )
    assert resp.status == 200, f"{url} -> {resp.status}: {(await resp.text())[:300]}"

    if not is_full_document(await resp.text()):
        pytest.skip(f"{path} renders an HTMX fragment — out of full-page axe scope")

    await page.add_script_tag(content=_AXE_SOURCE)
    violations = await page.evaluate(_AXE_RUN_JS)
    _axe_pages_swept += 1
    assert not violations, _format_violations(url, violations)
