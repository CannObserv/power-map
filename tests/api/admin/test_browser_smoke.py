"""Real-browser smoke tests of flows the happy-dom Vitest tier only simulates (GH #368).

Two flows, thin end-state assertions — the slow real-DOM backstop behind the
fast Vitest inner loop (`tests/js/`), never a re-implementation of it:

1. **Typeahead select → HTMX swap → resulting DOM state.** The jurisdiction
   combobox on the New Role form: real keystrokes drive the debounced HTMX
   search, the factory (``typeahead-combobox.js``) opens the swapped-in
   dropdown, and keyboard selection fills the hidden id — with real focus and
   ``aria-*`` state that ``eval()``-mounted happy-dom can only approximate.
2. **People list merge flow (select rows → preview modal → confirm).** Enter
   merge mode, select two rows, open the merge-preview modal (#255), confirm —
   asserting the modal's real focus placement, the post-merge list-region swap,
   merge-mode exit on ``showFlash``, and that the winner's detail page reflects
   the merge.

A third case, ``test_typeahead_wires_on_hard_load``, covers the hard-load entry
path. This tier found that divergence (deferred-script vs inline-mount ordering)
as an xfail; #435 fixed it with the mount queue, so it is now a plain test.

Every navigation goes through ``goto_with_retry`` (#436) — a bounded retry on
Chromium renderer crashes, which this VM produces on ~1% of navigations.

Runs on the shared browser-tier session fixtures in ``conftest.py`` (#300/#426):
``live_server`` + ``page`` + ``seeded_ids``. The merge flow MUTATES data, so it
seeds its own disposable people pair (module fixture below) and never touches
the shared session seed other files rely on — session teardown truncates, so no
cleanup is needed.

Run (isolated, marker-gated — same constraints as ``test_a11y_browser.py``)::

    uv run --group browser --env-file /etc/power-map/.env --env-file .env \\
        pytest tests/api/admin/test_browser_smoke.py -m browser
"""

import asyncpg
import pytest
import pytest_asyncio

from src.core.db import generate_id
from tests.api.admin.browser import goto_with_retry

# Skip cleanly when the browser extra isn't installed (default `uv run` syncs
# only the dev group). The `browser` fixture in conftest.py re-guards.
pytest.importorskip(
    "playwright.async_api",
    reason="install the browser group: uv sync --group browser && playwright install chromium",
)

pytestmark = [pytest.mark.browser]

# Disposable merge-pair names. Distinct prefix so the list search (?q=…) shows
# exactly these two rows — the shared seed's people never enter the flow.
_WINNER_NAME = "Smoke Merge Winner"
_LOSER_NAME = "Smoke Merge Loser"
_MERGE_QUERY = "Smoke%20Merge"


@pytest_asyncio.fixture(scope="module", loop_scope="session")
async def merge_pair(browser_db, seeded_ids):
    """Two disposable duplicate people for the merge flow (committed, so the
    out-of-process server sees them).

    The merge CONFIRMS and deletes the loser, so these rows are owned by this
    module — the shared session seed (``seeded_ids``) stays intact for sibling
    browser-test files. Depends on ``seeded_ids`` so the shared seed is already
    committed before these rows join the list. No teardown: the session-scoped
    ``browser_db`` fixture truncates all data tables at teardown.
    """
    conn = await asyncpg.connect(browser_db)
    try:
        pair = {"winner_id": generate_id(), "loser_id": generate_id()}
        for pid, name in ((pair["winner_id"], _WINNER_NAME), (pair["loser_id"], _LOSER_NAME)):
            await conn.execute("INSERT INTO people (id) VALUES ($1)", pid)
            await conn.execute(
                "INSERT INTO person_names (id, person_id, name, is_canonical)"
                " VALUES ($1, $2, $3, TRUE)",
                generate_id(),
                pid,
                name,
            )
    finally:
        await conn.close()
    return pair


async def test_typeahead_select_fills_hidden_id(live_server, seeded_ids, page):
    """Typeahead combobox: keystrokes → HTMX search swap → keyboard select.

    Real-DOM behaviors the Vitest suite (`typeahead-combobox.test.js`)
    simulates: the debounced `hx-get` round-trip, the `htmx:afterSwap`
    dropdown-open, scoped `li` ids, `aria-activedescendant` keyboard
    navigation, and focus remaining on the input after selection.

    Reaches the New Role form via a **boosted navigation** from the roles list
    — the admin shell's normal mode (`hx-boost`), where htmx executes the
    form's inline factory-mount script after the deferred factory has loaded.
    The hard-load path converges via the #435 mount queue — covered separately
    by ``test_typeahead_wires_on_hard_load``.
    """
    page, _ = await goto_with_retry(page, f"{live_server}/admin/roles/")
    await page.click('a[href="/admin/roles/new/"]')  # boosted nav (hx-boost shell)
    await page.wait_for_selector("#role_type_id")

    # The jurisdictional sub-fields are hidden until a role type is picked.
    await page.select_option("#role_type_id", index=1)
    box = page.locator("#structural-jurisdictional")
    assert await box.is_visible()

    # Real keystrokes drive HTMX's `input changed delay:200ms` trigger; the
    # server-rendered results swap into the listbox and the factory opens it.
    inp = page.locator("#jurisdiction-search")
    await inp.click()
    await inp.press_sequentially("A11y State", delay=25)
    option = page.locator("#jurisdiction-search-results li[data-id]").first
    await option.wait_for(state="visible", timeout=15_000)
    assert await inp.get_attribute("aria-expanded") == "true"

    # Scoped-id contract: afterSwap prefixes each option id with the listbox id.
    li_id = await option.get_attribute("id")
    assert li_id.startswith("jurisdiction-search-results-")

    # Keyboard navigation: ArrowDown activates the option, Enter selects it.
    await inp.press("ArrowDown")
    assert await inp.get_attribute("aria-activedescendant") == li_id
    await inp.press("Enter")

    # Resulting DOM state: hidden id filled with the seeded jurisdiction, label
    # in the visible input, dropdown closed and emptied, clear button revealed,
    # and focus still on the combobox input (real focus — not simulatable).
    assert await page.input_value("#jurisdiction-id-hidden") == seeded_ids["jurisdiction_id"]
    assert await page.input_value("#jurisdiction-search") == "A11y State"
    assert await inp.get_attribute("aria-expanded") == "false"
    assert await page.locator("#jurisdiction-search-results li").count() == 0
    assert await page.locator("#jurisdiction-clear").is_visible()
    focused = await page.evaluate("document.activeElement && document.activeElement.id")
    assert focused == "jurisdiction-search"


async def test_typeahead_wires_on_hard_load(live_server, seeded_ids, page):
    """Hard (non-boosted) load of the New Role form wires the combobox too (#435).

    The inline mount in ``roles/form.html`` runs during parse, before any
    deferred ``<head>`` script — so it calls the mount **queue stub**
    (the inline, non-deferred block in ``base.html``) rather than the real
    factory. ``typeahead-combobox.js`` replaces the stub and drains the
    queue when it loads, which is what makes this path converge with the
    boosted nav covered by ``test_typeahead_select_fills_hidden_id``.
    """
    page, _ = await goto_with_retry(page, f"{live_server}/admin/roles/new/")
    await page.select_option("#role_type_id", index=1)
    inp = page.locator("#jurisdiction-search")
    await inp.click()
    await inp.press_sequentially("A11y State", delay=25)
    # If the factory were wired, its htmx:afterSwap handler would open the
    # dropdown (and prefix the option ids). Short timeout: the swap itself
    # lands well inside it; only the wiring is missing.
    await page.locator("#jurisdiction-search-results li[data-id]").first.wait_for(
        state="visible", timeout=5_000
    )
    assert await inp.get_attribute("aria-expanded") == "true"


async def test_people_list_merge_flow(live_server, merge_pair, page):
    """People list merge: merge mode → select 2 rows → preview modal → confirm.

    Real-DOM behaviors the Vitest suites (`people-merge.test.js`,
    `merge-modal-script.test.js`) simulate: the delegated merge-mode toggle,
    checkbox selection driving the Keep buttons, the `hx-get` modal swap into
    the portal with its focus placement, the confirm POST's list-region swap,
    and merge-mode exit on the `showFlash` flash trigger.
    """
    winner, loser = merge_pair["winner_id"], merge_pair["loser_id"]
    page, _ = await goto_with_retry(page, f"{live_server}/admin/people/?q={_MERGE_QUERY}")
    await page.wait_for_selector(f'tr[data-person-id="{winner}"]')
    await page.wait_for_selector(f'tr[data-person-id="{loser}"]')

    # Enter merge mode (document-delegated toggle) and select both rows.
    await page.click("#people-merge-btn")
    winner_cb = page.locator(f'tr[data-person-id="{winner}"] input[name="merge-select"]')
    loser_cb = page.locator(f'tr[data-person-id="{loser}"] input[name="merge-select"]')
    await winner_cb.check()
    await loser_cb.check()

    # Two selected: the bar offers both Keep buttons; A is the first-checked row.
    bar = page.locator("#people-merge-bar")
    assert await bar.is_visible()
    keep_a = bar.locator(".merge-bar__keep-a")
    assert await keep_a.inner_text() == f'Keep "{_WINNER_NAME}"'

    # Keep winner → hx-get the merge-preview modal into the shared portal.
    await keep_a.click()
    await page.wait_for_selector("#merge-form")
    assert await page.locator("#merge-name-winner").inner_text() == _WINNER_NAME
    assert await page.locator("#merge-name-loser").inner_text() == _LOSER_NAME
    # The portal script focuses the first button in the modal (real focus).
    await page.wait_for_function(
        "document.activeElement && document.activeElement.id === 'merge-swap-btn'",
        timeout=5_000,
    )

    # Confirm the merge.
    await page.click("#merge-execute-btn")

    # Resulting DOM state: the list region re-renders without the loser, the
    # modal portal empties, and showFlash exits merge mode.
    await page.wait_for_selector(f'tr[data-person-id="{loser}"]', state="detached", timeout=15_000)
    await page.wait_for_selector(f'tr[data-person-id="{winner}"]')
    assert await page.locator("#merge-form").count() == 0
    await page.wait_for_function(
        "document.getElementById('people-merge-btn').textContent === 'Merge'", timeout=5_000
    )
    assert not await page.locator(f'tr[data-person-id="{winner}"] td.merge-col').is_visible()

    # The merged entity's page reflects the merge: the loser's canonical name
    # survives as an alias on the winner (default keep_name_ids all-checked).
    page, _ = await goto_with_retry(page, f"{live_server}/admin/people/{winner}/")
    assert _LOSER_NAME in await page.locator("#names-table").inner_text()
