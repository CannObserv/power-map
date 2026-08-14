"""Tests for admin base template: header branding and footer."""

import lxml.html
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from src.api.admin.deps import get_db
from src.api.main import app
from tests.api.admin.conftest import (
    AUTH_HEADERS,
    ENTITY_ORDER_HREFS,
    assert_render_order,
)

pytestmark = pytest.mark.integration


@pytest_asyncio.fixture(loop_scope="session")
async def db(db_pool):
    """Pool-acquired connection wrapped in a rolled-back transaction."""
    async with db_pool.acquire() as conn:
        tr = conn.transaction()
        await tr.start()
        try:
            yield conn
        finally:
            await tr.rollback()


@pytest_asyncio.fixture(loop_scope="session")
async def client(db):
    """AsyncClient with app, overriding get_db to use the test connection."""

    async def _get_db_override():
        yield db

    app.dependency_overrides[get_db] = _get_db_override
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test", follow_redirects=True
    ) as c:
        yield c
    app.dependency_overrides.pop(get_db, None)


async def test_header_brand_text_is_power_map(client):
    """Brand in topbar must read 'Power Map'."""
    response = await client.get("/admin/", headers=AUTH_HEADERS)
    assert response.status_code == 200
    assert "Power Map" in response.text


async def test_header_brand_icon_present(client):
    """Brand icon (cannabis_observer-icon-square.svg) must appear in topbar."""
    response = await client.get("/admin/", headers=AUTH_HEADERS)
    assert response.status_code == 200
    assert "admin-topbar__brand-icon" in response.text
    assert "cannabis_observer-icon-square.svg" in response.text


async def test_noscript_banner_renders_on_every_page(client):
    """The JS-required notice reaches the browser, not just the template source (#287)."""
    response = await client.get("/admin/", headers=AUTH_HEADERS)
    assert response.status_code == 200
    assert "<noscript>" in response.text
    assert "JavaScript is required to edit." in response.text
    assert "noscript-banner" in response.text


async def test_footer_credits_cannabis_observer(client):
    """Footer must contain the 'A project of … Cannabis Observer' credit text."""
    response = await client.get("/admin/", headers=AUTH_HEADERS)
    assert response.status_code == 200
    assert "admin-footer" in response.text
    assert "A project of" in response.text
    assert "Cannabis Observer" in response.text


async def test_footer_links_to_cannabis_observer_site(client):
    """Footer 'Cannabis Observer' link must point to https://cannabis.observer/."""
    response = await client.get("/admin/", headers=AUTH_HEADERS)
    assert response.status_code == 200
    assert "https://cannabis.observer/" in response.text


async def test_footer_icon_present(client):
    """Footer must include a cannabis observer icon image."""
    response = await client.get("/admin/", headers=AUTH_HEADERS)
    assert response.status_code == 200
    assert "admin-footer__icon" in response.text


async def test_footer_emoji_present(client):
    """Footer must include the 🌱🏛️🔍 emoji wrapped in aria-hidden span."""
    response = await client.get("/admin/", headers=AUTH_HEADERS)
    assert response.status_code == 200
    assert "🌱🏛️🔍" in response.text
    assert 'aria-hidden="true">🌱🏛️🔍' in response.text


async def test_fouc_prevention_script_in_base_template(client):
    """FOUC script must appear in base.html to prevent flash on load."""
    response = await client.get("/admin/", headers=AUTH_HEADERS)
    assert "pm-color-scheme" in response.text


async def test_dark_mode_toggle_button_present(client):
    """Theme toggle button must be in the topbar with a neutral accessible name.
    Per #25, dark-mode.js owns the per-state label/icon (its META map) and
    populates the button on load; the server renders only this neutral default."""
    response = await client.get("/admin/", headers=AUTH_HEADERS)
    assert "theme-toggle" in response.text
    assert 'aria-label="Color theme"' in response.text


async def test_typeahead_mount_queue_stub_precedes_the_deferred_factory(client):
    """The #435 mount-queue stub must stay inline, non-deferred, and ahead of
    typeahead-combobox.js.

    That ordering IS the fix: templates mount the combobox from inline <body>
    scripts, which run during parse — before any deferred script. If this stub
    gains a `defer`/`src`, or slips below the factory, hard loads silently stop
    wiring comboboxes again (the browser tier catches it too, but only there).
    """
    response = await client.get("/admin/roles/new/", headers=AUTH_HEADERS)
    # Parsed, not string-searched: the stub's own comment contains both the
    # factory filename and a literal "<script>", which fools index arithmetic.
    scripts = lxml.html.fromstring(response.text).xpath("//script")
    stub_i = next(
        (i for i, s in enumerate(scripts) if "__pmTypeaheadQueue" in (s.text_content() or "")),
        None,
    )
    factory_i = next(
        (i for i, s in enumerate(scripts) if "typeahead-combobox.js" in (s.get("src") or "")),
        None,
    )
    assert stub_i is not None, "typeahead mount-queue stub missing from base.html (#435)"
    assert factory_i is not None, "typeahead-combobox.js script tag missing from base.html"
    assert stub_i < factory_i, "mount-queue stub must precede the deferred factory (#435)"
    stub = scripts[stub_i]
    assert stub.get("defer") is None, "mount-queue stub must not be deferred (#435)"
    assert stub.get("async") is None, "mount-queue stub must not be async (#435)"
    assert stub.get("src") is None, "mount-queue stub must stay inline (#435)"


async def test_dark_mode_js_loaded_with_defer(client):
    """dark-mode.js must be loaded with defer to avoid blocking render."""
    response = await client.get("/admin/", headers=AUTH_HEADERS)
    assert "dark-mode.js" in response.text
    # Check defer appears on the same script tag line
    text = response.text
    idx = text.find("dark-mode.js")
    assert "defer" in text[max(0, idx - 100) : idx + 100]


async def test_people_merge_js_loaded_site_wide_with_defer(client):
    """#249: people-merge.js must load site-wide from base.html, not only from
    the People list's extra_head.

    The admin shell is hx-boost; boosted navs strip <head>, so an
    extra_head-only script never ran when the user reached the People list by
    clicking the sidebar — Merge was a silent no-op. Asserting it on a
    NON-People page (the dashboard) proves it is loaded site-wide alongside
    role-merge.js. Defer keeps it non-blocking and order-independent."""
    response = await client.get("/admin/", headers=AUTH_HEADERS)
    assert "people-merge.js" in response.text
    idx = response.text.find("people-merge.js")
    assert "defer" in response.text[max(0, idx - 100) : idx + 100]


async def test_sidebar_entity_links_jurisdiction_first(client):
    """Sidebar entity nav is ordered Jurisdiction, Org, Person, Role, Assignment
    (#275). The sidebar renders before <main>, so first-occurrence position of
    each entity list href in that prefix reflects nav order."""
    response = await client.get("/admin/", headers=AUTH_HEADERS)
    assert response.status_code == 200
    sidebar = response.text.split('id="main-content"')[0]
    assert_render_order(sidebar, ENTITY_ORDER_HREFS)
