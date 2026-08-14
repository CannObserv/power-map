"""Shared axe-core plumbing for the browser test tier (GH #438).

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
"""

import hashlib
from pathlib import Path

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
