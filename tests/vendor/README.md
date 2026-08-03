# tests/vendor — SHA-pinned test-only assets

Third-party assets used **only by the test suite** (never served to production,
so not under `src/static/vendor/`). Same SHA-pin discipline: version in the
filename, exact bytes committed, hash recorded here. To upgrade, download the
new pinned version, update the filename + row below, and re-run the browser tier.

| File | Version | Source | SHA-256 |
|------|---------|--------|---------|
| `axe-core-4.10.2.min.js` | 4.10.2 | `https://cdn.jsdelivr.net/npm/axe-core@4.10.2/axe.min.js` | `b511cd9dec01c76f4b2ad1723b66b6db37d4c2eb4ed199076e1829d9ee7b75e3` |

## axe-core

Injected into each rendered admin page by the #300 Playwright browser tier
(`tests/api/admin/test_a11y_browser.py`) via `page.add_script_tag(path=...)`,
then `axe.run()` is evaluated in-page. Vendored (not `axe-playwright-python`)
so the axe version is pinned here rather than transitively by a wrapper package.
The browser tier verifies this file's SHA-256 at import time — a corrupted or
silently-swapped copy fails the run loudly.
