"""Axe-core a11y checks on post-interaction admin DOM states (GH #367).

The v1 browser sweep (``test_a11y_browser.py``, #300) runs axe over full pages
only. This module drives the page into interactive states server-side rendering
can't reach — inline edit rows, portal modals, merge mode — and runs axe on the
resulting DOM. Targeted interaction scripts, not a blanket sweep: a few
high-value states, each asserted reachable (a timed wait on a state marker)
before axe runs, so a broken interaction fails loudly instead of passing
vacuously against the pre-interaction page.

States covered:

- **Org contact inline edit row** — the ``edit-row`` swap pattern shared by
  every ancillary table (contacts/links/identifiers/names/...).
- **People list merge mode** — reveals the hidden ``.merge-col`` checkbox
  column (labelled "Select ... for merge", #366) and the Keep A/B merge bar.
- **People merge preview modal** — the #255 curated-merge modal opened into
  ``#merge-modal-portal`` over the list page.
- **Org merge search -> preview modals** — modal over a detail page: the
  typeahead search modal, its populated listbox, then the preview modal that
  replaces it in the portal (the full "Merge with..." Danger Zone flow).
- **Archived person delete confirm modal** (#426 archived seeds) — the
  ``hx-confirm`` interception is a real DOM modal (``admin-modal.js``), not a
  browser-native dialog, so it is axe-evaluable. Closed via Escape.

Nothing here mutates the shared session seed: every driven request is a GET
(edit-row, merge previews) and destructive confirms are never submitted — the
delete modal is cancelled, merge forms are left unsubmitted.

Shares the #426 session fixtures (``live_server``, ``seeded_ids``, ``page``)
from ``conftest.py`` and the SHA-pinned vendored axe-core asset. Same marker
(``-m browser``) and isolation constraints as the full-page sweep; run it
alone against the dedicated test DB (see docs/TESTING.md).
"""

import hashlib
from pathlib import Path

import pytest

# Same gate as the full-page sweep: only `-m browser` runs may need Playwright.
pytest.importorskip(
    "playwright.async_api",
    reason="install the browser group: uv sync --group browser && playwright install chromium",
)

pytestmark = [pytest.mark.browser]

# SHA-pinned vendored axe-core — same asset the full-page sweep injects (see
# tests/vendor/README.md). The pin is repeated here deliberately: conftest and
# the sweep are read-only foundation files for this module (#367 batch plan),
# and the versioned filename means an axe upgrade must touch both pins anyway.
_AXE_PATH = Path(__file__).parents[2] / "vendor" / "axe-core-4.10.2.min.js"
_AXE_SHA256 = "b511cd9dec01c76f4b2ad1723b66b6db37d4c2eb4ed199076e1829d9ee7b75e3"
_AXE_SOURCE = _AXE_PATH.read_text(encoding="utf-8")
if hashlib.sha256(_AXE_SOURCE.encode("utf-8")).hexdigest() != _AXE_SHA256:
    raise RuntimeError(
        f"{_AXE_PATH.name} SHA-256 mismatch — vendored axe-core is corrupt or was swapped;"
        " re-download the pinned version (see tests/vendor/README.md)"
    )

# In-page axe run (same shape as the sweep): violations only, trimmed to the
# fields the failure message needs.
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

# Ceiling on every state-marker wait. Interaction swaps are local HTMX round
# trips against a warm local server — if a state isn't reached in 5s the
# interaction is broken, and a short ceiling keeps a red run fast.
_WAIT_MS = 5_000


def _format_violations(state: str, violations: list[dict]) -> str:
    """Failure message in the sweep's format, keyed by interaction state."""
    lines = [f"{state}: {len(violations)} axe-core violation(s):"]
    for v in violations:
        lines.append(f"  [{v['impact']}] {v['id']} — {v['help']} ({v['helpUrl']})")
        for target in v["nodes"][:5]:
            lines.append(f"      at: {target}")
        if len(v["nodes"]) > 5:
            lines.append(f"      … +{len(v['nodes']) - 5} more node(s)")
    return "\n".join(lines)


async def _axe_check(page, state: str) -> None:
    """Run axe against the page's current DOM and assert zero violations.

    Injects the vendored axe-core once per document — portal swaps and HTMX
    row swaps keep the same document, so repeated checks after further
    interaction reuse the already-injected copy.
    """
    if not await page.evaluate("() => !!window.axe"):
        await page.add_script_tag(content=_AXE_SOURCE)
    violations = await page.evaluate(_AXE_RUN_JS)
    assert not violations, _format_violations(state, violations)


async def _enter_people_merge_mode(page, live_server: str, seeded_ids: dict) -> None:
    """Drive the People list into merge mode with both active people selected.

    Ends with the merge bar's Keep buttons enabled (two selections) — the
    state the merge-preview test continues from.
    """
    await page.goto(live_server + "/admin/people/", wait_until="domcontentloaded")
    # Pre-interaction: the merge column exists but is hidden (display:none).
    assert not await page.locator("th.merge-col").first.is_visible(), (
        "merge column visible before entering merge mode — page state changed?"
    )
    await page.click("#people-merge-btn")
    await page.wait_for_selector("th.merge-col", state="visible", timeout=_WAIT_MS)
    for pid in (seeded_ids["person_id"], seeded_ids["person2_id"]):
        await page.check(f'input[name="merge-select"][value="{pid}"]')
    await page.wait_for_selector(".merge-bar__keep-a:not([disabled])", timeout=_WAIT_MS)


async def test_org_contact_edit_row_axe_clean(live_server, seeded_ids, page):
    """Open the seeded org contact's inline edit row and axe the edited state."""
    contact_id = seeded_ids["org_sub"]["contact_id"]
    await page.goto(
        live_server + f"/admin/orgs/{seeded_ids['org_id']}/", wait_until="domcontentloaded"
    )
    row = f"#contact-row-{contact_id}"
    # Pre-interaction: the read row has no form.
    assert await page.locator(f"{row} form").count() == 0, "edit form present before interaction"
    await page.click(f'{row} button[aria-label^="Edit contact"]')
    await page.wait_for_selector(f'{row} form input[name="value"]', timeout=_WAIT_MS)
    await _axe_check(page, "org detail — contact inline edit row open")


async def test_people_list_merge_mode_axe_clean(live_server, seeded_ids, page):
    """Enable merge mode on the People list — revealed .merge-col checkboxes
    (labelled 'Select … for merge') and the two-selection merge bar — and axe."""
    await _enter_people_merge_mode(page, live_server, seeded_ids)
    await _axe_check(page, "people list — merge mode, two selected, merge bar shown")


async def test_people_merge_preview_modal_axe_clean(live_server, seeded_ids, page):
    """Open the merge preview modal (Keep button → portal) over the People list
    and axe it. The merge form is never submitted."""
    await _enter_people_merge_mode(page, live_server, seeded_ids)
    await page.click(".merge-bar__keep-a")
    await page.wait_for_selector("#merge-modal-portal #merge-form", timeout=_WAIT_MS)
    await _axe_check(page, "people list — merge preview modal open")


async def test_org_merge_search_and_preview_modals_axe_clean(live_server, seeded_ids, page):
    """Drive the org Danger Zone 'Merge with…' flow: search modal over the
    detail page, populated typeahead listbox, then the preview modal that
    replaces it in the portal. Axe at each state; the merge is never posted."""
    await page.goto(
        live_server + f"/admin/orgs/{seeded_ids['org_id']}/", wait_until="domcontentloaded"
    )
    assert await page.locator("#merge-modal-portal .modal").count() == 0, (
        "merge portal already populated before interaction"
    )
    await page.click('.danger-zone button[hx-get$="/merge-search/"]')
    await page.wait_for_selector("#merge-modal-portal .modal", timeout=_WAIT_MS)
    await _axe_check(page, "org detail — merge search modal open")

    await page.fill("#merge-target-display", "A11y Org Two")
    await page.wait_for_selector('#merge-target-results li[role="option"]', timeout=_WAIT_MS)
    await _axe_check(page, "org detail — merge search modal, typeahead results shown")

    await page.click('#merge-target-results li[role="option"]')
    await page.wait_for_selector("#merge-modal-portal #merge-form", timeout=_WAIT_MS)
    await _axe_check(page, "org detail — merge preview modal open")


async def test_archived_person_delete_confirm_modal_axe_clean(live_server, seeded_ids, page):
    """Open the Danger Zone delete confirm modal on an archived person detail
    page (#426 archived seeds), axe it, then cancel via Escape — the DELETE is
    never issued. hx-confirm renders a DOM modal here (admin-modal.js), so the
    state is axe-evaluable, unlike a browser-native confirm()."""
    await page.goto(
        live_server + f"/admin/people/{seeded_ids['archived_person_id']}/",
        wait_until="domcontentloaded",
    )
    assert await page.locator(".modal-backdrop").count() == 0, "modal open before interaction"
    await page.click(".danger-zone button[hx-delete][hx-confirm]")
    await page.wait_for_selector("#pm-confirm-title", timeout=_WAIT_MS)
    await _axe_check(page, "archived person detail — delete confirm modal open")
    # Cancel non-destructively and confirm the modal tears down.
    await page.keyboard.press("Escape")
    await page.wait_for_selector(".modal-backdrop", state="detached", timeout=_WAIT_MS)
