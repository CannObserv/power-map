"""Shared plumbing for the browser test tier: axe-core + resilient navigation.

**axe-core** (GH #438).

The vendored asset + its SHA pin, the in-page ``axe.run`` snippet, the violation
formatter and the inject-once-then-run-and-assert helper used to be duplicated
verbatim in ``test_a11y_browser.py`` (#300) and
``test_a11y_browser_interactions.py`` (#367) — the duplication was forced by a
parallel-batch read-only constraint (#433 Batch B) that no longer applies.

Deliberately **not** merged into ``a11y.py``: that module is lxml-based and is
imported by the fast non-browser tier, which must never pull in browser
plumbing. And, mirroring ``conftest.py``'s lazy-import discipline, nothing here
imports Playwright at module scope — the ``page`` argument is duck-typed, so
this module stays importable (and unit-testable) without the browser extra.

**Navigation** (GH #436). Chromium's renderer CHECK-crashes on this VM's kernel
(6.12.90) for roughly 0.5–1% of navigations, so a ~200-navigation sweep usually
dies on a random route. ``goto_with_retry`` absorbs exactly that failure — and
nothing else: assertion failures, timeouts, HTTP errors and axe violations still
fail fast. Its predicate and retry loop are covered by ``test_browser_retry.py``
in the fast tier.
"""

import hashlib
import warnings
from pathlib import Path

from src.core.logging import get_logger

logger = get_logger(__name__)

# SHA-pinned vendored axe-core (see tests/vendor/README.md). Verified at import
# so a corrupted or silently-swapped copy fails loudly, not with garbage results.
# This is the single pin for the whole browser tier (#438).
AXE_PATH = Path(__file__).parents[2] / "vendor" / "axe-core-4.10.2.min.js"
AXE_SHA256 = "b511cd9dec01c76f4b2ad1723b66b6db37d4c2eb4ed199076e1829d9ee7b75e3"
AXE_SOURCE = AXE_PATH.read_text(encoding="utf-8")
if hashlib.sha256(AXE_SOURCE.encode("utf-8")).hexdigest() != AXE_SHA256:
    raise RuntimeError(
        f"{AXE_PATH.name} SHA-256 mismatch — vendored axe-core is corrupt or was swapped;"
        " re-download the pinned version (see tests/vendor/README.md)"
    )

# In-page axe run. Restrict to violations; return only the fields the failure
# message needs (rule id, impact, help URL, and a css selector per node).
AXE_RUN_JS = """
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


def format_violations(context: str, violations: list[dict]) -> str:
    """Render axe violations as a failure message.

    ``context`` identifies what was swept — the full-page sweep passes the URL,
    the interaction tier the state name — so one format serves both callers.
    """
    lines = [f"{context}: {len(violations)} axe-core violation(s):"]
    for v in violations:
        lines.append(f"  [{v['impact']}] {v['id']} — {v['help']} ({v['helpUrl']})")
        for target in v["nodes"][:5]:
            lines.append(f"      at: {target}")
        if len(v["nodes"]) > 5:
            lines.append(f"      … +{len(v['nodes']) - 5} more node(s)")
    return "\n".join(lines)


async def axe_check(page, context: str) -> None:
    """Run axe against the page's current DOM and assert zero violations.

    Injects the vendored axe-core once per document — portal swaps and HTMX row
    swaps keep the same document, so repeated checks after further interaction
    reuse the already-injected copy.
    """
    if not await page.evaluate("() => !!window.axe"):
        await page.add_script_tag(content=AXE_SOURCE)
    violations = await page.evaluate(AXE_RUN_JS)
    assert not violations, format_violations(context, violations)


# --- crash-resilient navigation (#436) -------------------------------------

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
