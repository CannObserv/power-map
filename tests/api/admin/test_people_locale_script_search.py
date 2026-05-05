"""Phase 2b Task 1 — locale + script search endpoints for typeahead.

Endpoints render HTML <li role="option" data-id data-label> partials
for the existing typeahead-combobox.js listbox. Empty `q` renders an
empty list; substring matches on code OR human-readable column; sort by
code ASC; capped at `limit`. Auth-guarded.
"""

import os
import re

import asyncpg
import pytest
from fastapi.testclient import TestClient

from src.api.main import app
from src.core.db import apply_schema

pytestmark = pytest.mark.integration

AUTH_HEADERS = {"X-ExeDev-UserID": "usr_test", "X-ExeDev-Email": "admin@test.com"}


def _dsn() -> str:
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        pytest.skip("DATABASE_URL not set")
    return dsn


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture(scope="module")
def seeded_lookup_tables():
    """Confirm the lookup tables are populated; skip if not seeded.

    Phase 2-prep is a hard pre-condition; running this suite against an
    unseeded DB makes no sense.
    """
    import asyncio

    async def check():
        conn = await asyncpg.connect(os.environ.get("DATABASE_URL", ""))
        try:
            await apply_schema(conn)
            n_loc = await conn.fetchval("SELECT COUNT(*) FROM bcp47_locales")
            n_scr = await conn.fetchval("SELECT COUNT(*) FROM iso15924_scripts")
            return n_loc, n_scr
        finally:
            await conn.close()

    if not os.environ.get("DATABASE_URL"):
        pytest.skip("DATABASE_URL not set")
    n_loc, n_scr = asyncio.run(check())
    if n_loc == 0 or n_scr == 0:
        pytest.skip("lookup tables empty — seed via scripts/seed_locales_scripts.py")
    return n_loc, n_scr


def _option_codes(html: str) -> list[str]:
    """Extract data-id values from rendered <li> options, in document order."""
    return re.findall(r'<li[^>]*\bdata-id="([^"]+)"', html)


def _option_labels(html: str) -> list[str]:
    return re.findall(r'<li[^>]*\bdata-label="([^"]+)"', html)


# ---- Locale search ------------------------------------------------------


def test_locale_search_empty_q_renders_no_options(client, seeded_lookup_tables):
    r = client.get("/admin/people/_locale_search?q=", headers=AUTH_HEADERS)
    assert r.status_code == 200
    assert _option_codes(r.text) == []


def test_locale_search_missing_q_renders_no_options(client, seeded_lookup_tables):
    r = client.get("/admin/people/_locale_search", headers=AUTH_HEADERS)
    assert r.status_code == 200
    assert _option_codes(r.text) == []


def test_locale_search_matches_on_code(client, seeded_lookup_tables):
    r = client.get("/admin/people/_locale_search?q=en-US", headers=AUTH_HEADERS)
    assert r.status_code == 200
    assert "en-US" in _option_codes(r.text)


def test_locale_search_matches_on_display_name(client, seeded_lookup_tables):
    """Find Spanish-something via the human-readable column."""
    r = client.get("/admin/people/_locale_search?q=Spanish", headers=AUTH_HEADERS)
    assert r.status_code == 200
    labels = _option_labels(r.text)
    assert labels, "expected at least one match"
    assert all("spanish" in lbl.lower() for lbl in labels)


def test_locale_search_option_shape(client, seeded_lookup_tables):
    r = client.get("/admin/people/_locale_search?q=en-US", headers=AUTH_HEADERS)
    assert 'role="option"' in r.text
    assert 'data-id="en-US"' in r.text
    assert 'data-label="en-US — ' in r.text  # "<code> — <display_name>"


def test_locale_search_caps_at_limit(client, seeded_lookup_tables):
    r = client.get("/admin/people/_locale_search?q=en&limit=5", headers=AUTH_HEADERS)
    assert r.status_code == 200
    assert len(_option_codes(r.text)) <= 5


def test_locale_search_default_limit_is_20(client, seeded_lookup_tables):
    r = client.get("/admin/people/_locale_search?q=en", headers=AUTH_HEADERS)
    assert r.status_code == 200
    assert len(_option_codes(r.text)) <= 20


def test_locale_search_sorted_by_code_asc(client, seeded_lookup_tables):
    r = client.get("/admin/people/_locale_search?q=en", headers=AUTH_HEADERS)
    codes = _option_codes(r.text)
    assert codes == sorted(codes), f"not sorted: {codes}"


def test_locale_search_requires_auth(client):
    r = client.get("/admin/people/_locale_search?q=en", follow_redirects=False)
    assert r.status_code in (302, 307)


# ---- Script search ------------------------------------------------------


def test_script_search_empty_q_renders_no_options(client, seeded_lookup_tables):
    r = client.get("/admin/people/_script_search?q=", headers=AUTH_HEADERS)
    assert r.status_code == 200
    assert _option_codes(r.text) == []


def test_script_search_matches_on_code(client, seeded_lookup_tables):
    r = client.get("/admin/people/_script_search?q=Latn", headers=AUTH_HEADERS)
    assert r.status_code == 200
    assert "Latn" in _option_codes(r.text)


def test_script_search_matches_on_name(client, seeded_lookup_tables):
    r = client.get("/admin/people/_script_search?q=Latin", headers=AUTH_HEADERS)
    assert r.status_code == 200
    assert "Latn" in _option_codes(r.text)


def test_script_search_option_shape(client, seeded_lookup_tables):
    r = client.get("/admin/people/_script_search?q=Latn", headers=AUTH_HEADERS)
    assert 'role="option"' in r.text
    assert 'data-id="Latn"' in r.text
    assert 'data-label="Latn — ' in r.text


def test_script_search_caps_at_limit(client, seeded_lookup_tables):
    r = client.get("/admin/people/_script_search?q=a&limit=3", headers=AUTH_HEADERS)
    assert r.status_code == 200
    assert len(_option_codes(r.text)) <= 3


def test_script_search_sorted_by_code_asc(client, seeded_lookup_tables):
    r = client.get("/admin/people/_script_search?q=a", headers=AUTH_HEADERS)
    codes = _option_codes(r.text)
    assert codes == sorted(codes)


def test_script_search_requires_auth(client):
    r = client.get("/admin/people/_script_search?q=Latn", follow_redirects=False)
    assert r.status_code in (302, 307)
