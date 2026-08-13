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

The session fixtures this tier runs on — ``browser_db``, ``seeded_ids``,
``live_server``, ``browser``, ``page`` — live in ``conftest.py`` (#426), shared
with the sibling browser-test files (#367/#368).

Run (never in pre-commit; one-time browser install required)::

    uv sync --group browser && uv run --group browser playwright install chromium
    uv run --env-file /etc/power-map/.env --env-file .env pytest \\
        tests/api/admin/test_a11y_browser.py -m browser
"""

import hashlib
from pathlib import Path

import pytest

from tests.api.admin.a11y import is_full_document
from tests.api.admin.admin_routes import (
    ADMIN_GET_PATHS,
    EXTRA_HEADERS,
    QUERY_PARAMS,
    param_values,
)

# Skip the whole module cleanly when the browser extra isn't installed (default
# `uv run` syncs only the `dev` group, so Playwright is absent there). The
# `browser` fixture (conftest.py) re-guards for any sibling module that forgets.
pytest.importorskip(
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
#
# xdist caveat (same as test_a11y_render.py): these are process-global counters.
# Under `pytest-xdist` (not used yet) each worker runs only a shard, so `_cases_run`
# never reaches len(ADMIN_GET_PATHS) on any worker and the floor silently no-ops.
# Before enabling xdist, move the aggregate to a cross-worker mechanism (e.g. a
# `pytest_sessionfinish` hook) or the backstop disappears without warning.
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
