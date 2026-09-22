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

``test_inline_edit_form_survives_its_overlay_note`` (#547) opens the role title
and assignment dates edit forms, outside and inside the producer's scope, and
waits out their overlay-note loads: a host that inherited the form's
``hx-target`` swapped the note (empty, or the one-line warning) over the whole
form. ``test_dup_badge_link_navigates_the_page`` (#547) clicks the People dup
badge's link: the host's own ``hx-target="this"``, inherited, loaded the
duplicates page into the badge. The static rule is
``test_self_loading_hosts.py``; these are the real-htmx seam.

Every navigation goes through ``goto_with_retry`` (#436) — a bounded retry on
Chromium renderer crashes, which this VM produces on ~1% of navigations.

Runs on the shared browser-tier session fixtures in ``conftest.py`` (#300/#426):
``live_server`` + ``page`` + ``seeded_ids``. Rows a flow mutates or depends on
beyond that seed are module-owned fixtures below — ``merge_pair`` (the merge
deletes one), ``dup_twins`` (a person dup count), ``in_scope_ids`` (producer-scope
rows) — so the shared session seed other files rely on stays as seeded. Session
teardown truncates, so no cleanup is needed.

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
# Same-named pair: similarity 1.0, so the People dup badge has a count and a link.
_DUP_TWIN_NAME = "Smoke Dup Twin"


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


@pytest_asyncio.fixture(scope="module", loop_scope="session")
async def dup_twins(browser_db, seeded_ids):
    """Two same-named people, so the People dup badge renders its link (#547).

    Module-owned like ``merge_pair``; the name stays clear of its ``?q=`` search.
    Drops the cached person dup count, which an earlier page load may have
    stored as 0 for the TTL.
    """
    conn = await asyncpg.connect(browser_db)
    try:
        for _ in range(2):
            pid = generate_id()
            await conn.execute("INSERT INTO people (id) VALUES ($1)", pid)
            await conn.execute(
                "INSERT INTO person_names (id, person_id, name, is_canonical)"
                " VALUES ($1, $2, $3, TRUE)",
                generate_id(),
                pid,
                _DUP_TWIN_NAME,
            )
        await conn.execute("DELETE FROM dup_count_cache WHERE entity_type = 'person'")
    finally:
        await conn.close()


@pytest_asyncio.fixture(scope="module", loop_scope="session")
async def in_scope_ids(browser_db, seeded_ids):
    """A disposable role and assignment inside the producer's row scope (#547).

    In scope, the overlay-note fragment is a real ``<p class="overlay-note">``
    rather than empty — the other face of the inherited-target bug, which swapped
    that line over the whole form. Owned by this module, like ``merge_pair``:
    the shared seed's role and assignment stay out of scope for sibling files.
    """
    conn = await asyncpg.connect(browser_db)
    try:
        ids = {"role_id": generate_id(), "assignment_id": generate_id()}
        await conn.execute(
            "INSERT INTO roles (id, organization_id, title) VALUES ($1, $2, 'Smoke Scoped Role')",
            ids["role_id"],
            seeded_ids["org_id"],
        )
        await conn.execute(
            "INSERT INTO role_assignments (id, person_id, role_id) VALUES ($1, $2, $3)",
            ids["assignment_id"],
            seeded_ids["person_id"],
            ids["role_id"],
        )
        for kind, key in (("role", "role_id"), ("assignment", "assignment_id")):
            await conn.execute(
                "INSERT INTO producer_crosswalk"
                " (id, source, kind, producer_id, exported_pm_id, pm_id, resolution)"
                " VALUES ($1, 'usa_wa', $2, $3, $4, $4, 'live')",
                generate_id(),
                kind,
                f"smoke-scoped-{kind}",
                ids[key],
            )
    finally:
        await conn.close()
    return ids


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


# Counts overlay-note loads as htmx settles them. ``htmx:afterSettle`` fires on
# the swap target, which is still in the document whichever element that is —
# so the count advances even when an inherited target swallowed the host.
_COUNT_NOTE_SETTLES = """() => {
  window.__noteSettles = 0;
  document.body.addEventListener('htmx:afterSettle', (e) => {
    const url = e.detail.xhr ? e.detail.xhr.responseURL : '';
    if (url.includes('variant=note')) window.__noteSettles += 1;
  });
}"""


async def _open_inline_edit(page, url: str, edit_path: str, notes: int):
    """Load ``url``, click the Edit whose ``hx-get`` ends ``edit_path``, and wait
    until the edit form's ``notes`` overlay-note hosts have loaded and settled."""
    page, _ = await goto_with_retry(page, url)
    await page.evaluate(_COUNT_NOTE_SETTLES)
    await page.click(f'button[hx-get$="{edit_path}"]')
    await page.wait_for_function(f"() => window.__noteSettles >= {notes}", timeout=5_000)
    return page


@pytest.mark.parametrize("scope", ["outside", "inside"])
@pytest.mark.parametrize(
    ("detail", "seed_key", "field", "edit_path", "notes", "inputs"),
    [
        ("/admin/roles/", "role_id", "#title-field", "/inline/title/edit/", 1, ("#title-input",)),
        (
            "/admin/role-assignments/",
            "assignment_id",
            "#dates-field",
            "/inline/dates/edit/",
            2,
            ("#start-date-input", "#end-date-input"),
        ),
    ],
)
async def test_inline_edit_form_survives_its_overlay_note(
    live_server,
    seeded_ids,
    in_scope_ids,
    page,
    scope,
    detail,
    seed_key,
    field,
    edit_path,
    notes,
    inputs,
):
    """#547: an edit form's overlay-note host loads into itself, not the form.

    The host sat inside the ``<form>`` with no ``hx-target``, inherited the
    form's, and swapped the note over the whole field: outside the producer's
    scope an empty note, so the label, input and buttons all vanished; inside
    it, the one-line note alone. Waits for the note loads to settle, then
    asserts the form is still there — with the note in it when in scope.
    """
    ids = seeded_ids if scope == "outside" else in_scope_ids
    url = f"{live_server}{detail}{ids[seed_key]}/"
    page = await _open_inline_edit(page, url, edit_path, notes)
    form = page.locator(f"{field} form")
    for selector in inputs:
        assert await form.locator(selector).is_visible(), f"{selector} gone once the note loaded"
    assert await form.locator('button[type="submit"]:has-text("Save")').is_visible()
    assert await form.locator(".overlay-note-host").count() == notes
    shown = notes if scope == "inside" else 0
    assert await form.locator(".overlay-note-host > .overlay-note").count() == shown


@pytest.mark.parametrize("start", ["/admin/people/", "/admin/"])
async def test_dup_badge_link_navigates_the_page(live_server, dup_twins, page, start):
    """#547: a boosted link inside a self-loading host navigates the page.

    The host names ``hx-target="this"`` for its own load. Inherited, that
    ``this`` still means the host, and htmx's boosted-link ``body`` fallback
    applies only when no ``hx-target`` is found — so the badge's link loaded
    the duplicates page into the badge. The host disinherits its target and swap.
    """
    page, _ = await goto_with_retry(page, f"{live_server}{start}")
    link = page.locator('[hx-get^="/admin/_dup-badge/people/"] a[href]').first
    await link.wait_for(state="visible", timeout=5_000)
    await link.click()
    await page.wait_for_selector('h1:has-text("Duplicate People")', timeout=5_000)
    assert await page.locator("main").count() == 1, "the duplicates page nested in the badge"
    assert await page.locator('[hx-get^="/admin/_dup-badge/people/"] h1').count() == 0


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
