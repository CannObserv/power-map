"""Crash-resilient navigation for the browser test tier (GH #436).

Chromium's renderer CHECK-crashes on this VM's kernel (6.12.90) for roughly
0.5–1% of navigations, so a ~200-navigation sweep usually dies on a random
route. ``goto_with_retry`` absorbs exactly that failure — and nothing else:
assertion failures, timeouts, HTTP errors and axe violations still fail fast.
Its predicate and retry loop are covered by ``test_browser_retry.py`` in the
fast tier.

**Scope boundary:** only the navigation itself is retried. A renderer crash
*after* ``goto`` — mid-interaction in ``test_a11y_browser_interactions.py``, or
part-way through the merge flow in ``test_browser_smoke.py`` — still fails the
run. The sweep is navigation-dominated (one ``goto`` plus one axe run per case),
so this covers the common failure; it is not a blanket immunity. A crash that
surfaces outside a ``goto`` is a genuine red run, not a gap in this module.

Separate from ``axe.py`` (#438 CR): navigation has nothing to do with
accessibility, and ``test_browser_smoke.py`` — which never runs axe — would
otherwise import the axe SHA verification just to navigate. Mirroring
``conftest.py``'s lazy-import discipline, nothing here imports Playwright at
module scope: the ``page`` argument is duck-typed, so this module stays
importable (and unit-testable) without the browser extra.
"""

import warnings

from src.core.logging import get_logger

logger = get_logger(__name__)

# Substrings that identify a Chromium *renderer crash*, the only failure mode
# worth retrying (#436):
#   net::ERR_ABORTED  — what a navigation raises when the renderer dies mid-flight
#                       (the signature seen in the reproduction runs)
#   Page crashed      — Playwright's page-level surface for the same CHECK crash
#   Target crashed    — the CDP wording of it, raised by evaluate/goto depending
#                       on which call notices the dead target first
# Deliberately absent: timeouts (a real hang), net::ERR_CONNECTION_REFUSED /
# ERR_NAME_NOT_RESOLVED (the server is gone — a real failure), and anything
# assertion-shaped (HTTP status, axe violations), which must fail loudly.
#
# ``net::ERR_ABORTED`` is the loosest of the three: Chromium also raises it for
# legitimately aborted navigations (a route that starts serving a download via
# Content-Disposition, or a navigation superseded by another). That costs one
# wasted attempt, never a masked failure — the second attempt aborts the same
# way and the test goes red. A *persistent* ERR_ABORTED is a real bug in the
# route, not VM flakiness; check what the route now serves before blaming #436.
RENDERER_CRASH_SIGNATURES = ("net::ERR_ABORTED", "Page crashed", "Target crashed")

# 2 attempts total: the crash is independent per navigation at ~1%, so one retry
# takes a full-sweep failure probability from ~85% to ~2%. More attempts would
# mostly buy the ability to hide a genuine crash storm.
GOTO_ATTEMPTS = 2

# Process-wide count of retries fired, so a crash storm is visible (each retry
# also emits a RendererCrashRetry warning — pytest prints those in its warnings
# summary, which the weekly #369 sweep captures in the journal).
RENDERER_CRASH_RETRIES = 0


class RendererCrashRetry(UserWarning):
    """Warning emitted when a navigation is retried after a renderer crash."""


def is_renderer_crash(exc: BaseException) -> bool:
    """True if ``exc`` is a Chromium renderer crash worth one more attempt.

    Conservative by construction: assertions never retry (a red a11y test must
    stay red even if its message quotes a crash), timeouts never retry, and the
    message must carry a known crash signature.
    """
    if isinstance(exc, AssertionError):
        return False
    if type(exc).__name__ == "TimeoutError":  # playwright's and the builtin
        return False
    message = str(exc)
    return any(sig in message for sig in RENDERER_CRASH_SIGNATURES)


async def _replacement_page(page):
    """Close a crashed page and open a fresh one in the same browser context.

    A crashed page stays unusable — its renderer is gone — so the retry needs a
    new target. The context (and its auth headers) survives the crash, so the
    replacement is authenticated exactly like the original."""
    context = page.context
    try:
        await page.close()
    except Exception as exc:  # a crashed page can refuse to close; context teardown reaps it
        logger.warning("could not close crashed page: %s", exc)
    return await context.new_page()


async def goto_with_retry(
    page, url: str, *, wait_until: str = "domcontentloaded", attempts: int = GOTO_ATTEMPTS
):
    """Navigate to ``url``, surviving Chromium renderer crashes (#436).

    Returns ``(page, response)`` — **the page may be a new object**: a crashed
    page can't be reused, so a retry runs on a fresh page from the same context
    and callers must rebind their local ``page``. Every retry is counted and
    warned about; any non-crash failure propagates on the first attempt.
    """
    global RENDERER_CRASH_RETRIES
    for attempt in range(1, attempts + 1):
        try:
            return page, await page.goto(url, wait_until=wait_until)
        except Exception as exc:
            if attempt >= attempts or not is_renderer_crash(exc):
                raise
            RENDERER_CRASH_RETRIES += 1
            detail = str(exc).splitlines()[0]
            message = (
                f"Chromium renderer crash on {url} (attempt {attempt}/{attempts}):"
                f" {detail} — retrying on a fresh page (#436)"
            )
            logger.warning(message)
            warnings.warn(message, RendererCrashRetry, stacklevel=2)
            page = await _replacement_page(page)
    raise AssertionError("unreachable: the loop returns or raises on every attempt")
