"""Unit tests for the browser tier's renderer-crash retry (GH #436).

The retry predicate and the bounded retry loop are pure Python — no Playwright,
no browser — so they belong in the fast tier, not behind ``-m browser``. The
fake page/context below mimic only the three members ``goto_with_retry`` uses
(``page.goto``, ``page.close``, ``page.context.new_page``).

What must hold: crash signatures retry once on a fresh page; everything else —
assertion failures, timeouts, HTTP/connection errors, axe violations — fails
fast and loudly, and every retry is announced.
"""

import pytest

from tests.api.admin import browser as browser_mod
from tests.api.admin.browser import (
    RendererCrashRetry,
    goto_with_retry,
    is_renderer_crash,
)


class _PlaywrightishError(Exception):
    """Stand-in for ``playwright.async_api.Error`` (not importable in this tier)."""


class _FakePage:
    def __init__(self, context, script):
        self.context = context
        self._script = script
        self.closed = False
        self.gotos = []

    async def goto(self, url, wait_until=None):
        self.gotos.append(url)
        outcome = self._script.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    async def close(self):
        self.closed = True


class _FakeContext:
    """Hands out pages that all consume the same scripted outcome list."""

    def __init__(self, script):
        self._script = script
        self.new_pages = []

    async def new_page(self):
        page = _FakePage(self, self._script)
        self.new_pages.append(page)
        return page

    def first_page(self):
        return _FakePage(self, self._script)


def _fake(*outcomes):
    context = _FakeContext(list(outcomes))
    return context, context.first_page()


# --- the predicate ---------------------------------------------------------


@pytest.mark.parametrize(
    "exc",
    [
        _PlaywrightishError(
            "Page.goto: net::ERR_ABORTED at http://127.0.0.1:8123/admin/people/\n"
            "Call log:\n  - navigating to …"
        ),
        _PlaywrightishError("Page.evaluate: Target crashed"),
        _PlaywrightishError("Page crashed"),
    ],
    ids=["err_aborted", "target_crashed", "page_crashed"],
)
def test_renderer_crash_signatures_retry(exc):
    assert is_renderer_crash(exc) is True


@pytest.mark.parametrize(
    "exc",
    [
        AssertionError("Page crashed"),  # never retry an assertion, whatever it says
        TimeoutError("Timeout 5000ms exceeded."),
        _PlaywrightishError("Page.goto: net::ERR_CONNECTION_REFUSED at http://127.0.0.1:8123/"),
        _PlaywrightishError("Page.goto: net::ERR_NAME_NOT_RESOLVED"),
        AssertionError("http://x/admin/people/: 3 axe-core violation(s):"),
        AssertionError("/admin/people/ -> 500: Internal Server Error"),
        RuntimeError("uvicorn exited early with code 1"),
    ],
    ids=[
        "assertion_mentioning_crash",
        "timeout",
        "connection_refused",
        "dns",
        "axe_violations",
        "http_status",
        "server_died",
    ],
)
def test_non_crash_failures_do_not_retry(exc):
    assert is_renderer_crash(exc) is False


# --- the retry loop --------------------------------------------------------


async def test_clean_navigation_returns_same_page_and_response():
    context, page = _fake("response-1")
    got_page, resp = await goto_with_retry(page, "http://x/admin/")
    assert (got_page, resp) == (page, "response-1")
    assert context.new_pages == [], "no fresh page should be opened on a clean navigation"


async def test_crash_retries_once_on_a_fresh_page(monkeypatch):
    monkeypatch.setattr("tests.api.admin.browser.RENDERER_CRASH_RETRIES", 0, raising=True)
    context, page = _fake(
        _PlaywrightishError("Page.goto: net::ERR_ABORTED at http://x/admin/"), "ok"
    )

    with pytest.warns(RendererCrashRetry, match="ERR_ABORTED"):
        got_page, resp = await goto_with_retry(page, "http://x/admin/")

    assert resp == "ok"
    assert got_page is not page, "retry must run on a fresh page — a crashed one is unusable"
    assert got_page is context.new_pages[0]
    assert page.closed, "the crashed page should be closed before retrying"
    assert browser_mod.RENDERER_CRASH_RETRIES == 1, "retries must be counted, not silently absorbed"


async def test_close_failure_on_a_crashed_page_does_not_break_the_retry():
    context, page = _fake(_PlaywrightishError("Page crashed"), "ok")

    async def _boom():
        raise _PlaywrightishError("Target page, context or browser has been closed")

    page.close = _boom
    with pytest.warns(RendererCrashRetry):
        got_page, resp = await goto_with_retry(page, "http://x/admin/")
    assert (got_page, resp) == (context.new_pages[0], "ok")


async def test_non_crash_error_propagates_without_retrying():
    context, page = _fake(_PlaywrightishError("Page.goto: net::ERR_CONNECTION_REFUSED"))
    with pytest.raises(_PlaywrightishError, match="ERR_CONNECTION_REFUSED"):
        await goto_with_retry(page, "http://x/admin/")
    assert context.new_pages == [], "a non-crash failure must fail fast, not open a fresh page"


async def test_retry_is_bounded_and_the_last_crash_propagates():
    context, page = _fake(
        _PlaywrightishError("Page crashed: attempt 1"),
        _PlaywrightishError("Page crashed: attempt 2"),
        "never reached",
    )
    with pytest.warns(RendererCrashRetry):
        with pytest.raises(_PlaywrightishError, match="attempt 2"):
            await goto_with_retry(page, "http://x/admin/")
    assert len(context.new_pages) == 1, "2 attempts total — exactly one fresh page"


async def test_attempts_are_configurable():
    context, page = _fake(
        _PlaywrightishError("Page crashed 1"),
        _PlaywrightishError("Page crashed 2"),
        "ok",
    )
    with pytest.warns(RendererCrashRetry):
        got_page, resp = await goto_with_retry(page, "http://x/admin/", attempts=3)
    assert resp == "ok"
    assert len(context.new_pages) == 2
    assert got_page is context.new_pages[1]
