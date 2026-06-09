"""Phase 2b Task 1 — locale + script search endpoints for typeahead.

Endpoints render HTML <li role="option" data-id data-label> partials
for the existing typeahead-combobox.js listbox. Empty `q` renders an
empty list; substring matches on code OR human-readable column; sort by
code ASC; capped at `limit`. Auth-guarded.
"""

import re

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient

from src.api.main import app

pytestmark = [
    pytest.mark.integration,
]

AUTH_HEADERS = {"X-ExeDev-UserID": "usr_test", "X-ExeDev-Email": "admin@test.com"}


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


@pytest_asyncio.fixture(loop_scope="session")
async def seeded_lookup_tables(db_pool):
    """Confirm the lookup tables are populated; skip if not seeded.

    Phase 2-prep is a hard pre-condition; running this suite against an
    unseeded DB makes no sense.
    """
    async with db_pool.acquire() as conn:
        n_loc = await conn.fetchval("SELECT COUNT(*) FROM bcp47_locales")
        n_scr = await conn.fetchval("SELECT COUNT(*) FROM iso15924_scripts")
    if n_loc == 0 or n_scr == 0:
        pytest.skip("lookup tables empty — seed via scripts/seed_locales_scripts.py")
    return n_loc, n_scr


def _option_codes(html: str) -> list[str]:
    """Extract data-id values from rendered <li> options, in document order."""
    return re.findall(r'<li[^>]*\bdata-id="([^"]+)"', html)


def _option_labels(html: str) -> list[str]:
    return re.findall(r'<li[^>]*\bdata-label="([^"]+)"', html)


# ---- Locale search ------------------------------------------------------


async def test_locale_search_empty_q_renders_no_options(client, seeded_lookup_tables):
    r = client.get("/admin/people/_locale_search?q=", headers=AUTH_HEADERS)
    assert r.status_code == 200
    assert _option_codes(r.text) == []


async def test_locale_search_missing_q_renders_no_options(client, seeded_lookup_tables):
    r = client.get("/admin/people/_locale_search", headers=AUTH_HEADERS)
    assert r.status_code == 200
    assert _option_codes(r.text) == []


async def test_locale_search_matches_on_code(client, seeded_lookup_tables):
    r = client.get("/admin/people/_locale_search?q=en-US", headers=AUTH_HEADERS)
    assert r.status_code == 200
    assert "en-US" in _option_codes(r.text)


async def test_locale_search_matches_on_display_name(client, seeded_lookup_tables):
    """Find Spanish-something via the human-readable column."""
    r = client.get("/admin/people/_locale_search?q=Spanish", headers=AUTH_HEADERS)
    assert r.status_code == 200
    labels = _option_labels(r.text)
    assert labels, "expected at least one match"
    assert all("spanish" in lbl.lower() for lbl in labels)


async def test_locale_search_option_shape(client, seeded_lookup_tables):
    r = client.get("/admin/people/_locale_search?q=en-US", headers=AUTH_HEADERS)
    assert 'role="option"' in r.text
    assert 'data-id="en-US"' in r.text
    assert 'data-label="en-US — ' in r.text  # "<code> — <display_name>"


async def test_locale_search_caps_at_limit(client, seeded_lookup_tables):
    r = client.get("/admin/people/_locale_search?q=en&limit=5", headers=AUTH_HEADERS)
    assert r.status_code == 200
    assert len(_option_codes(r.text)) <= 5


async def test_locale_search_default_limit_is_20(client, seeded_lookup_tables):
    r = client.get("/admin/people/_locale_search?q=en", headers=AUTH_HEADERS)
    assert r.status_code == 200
    assert len(_option_codes(r.text)) <= 20


async def test_locale_search_sorted_by_code_asc(client, seeded_lookup_tables):
    r = client.get("/admin/people/_locale_search?q=en", headers=AUTH_HEADERS)
    codes = _option_codes(r.text)
    assert codes == sorted(codes), f"not sorted: {codes}"


async def test_locale_search_requires_auth(client):
    r = client.get("/admin/people/_locale_search?q=en", follow_redirects=False)
    assert r.status_code in (302, 307)


async def test_locale_search_treats_percent_as_literal(client, seeded_lookup_tables):
    """`%` in user input must be ESCAPE'd, not interpreted as ILIKE wildcard.

    Real BCP 47 codes never contain `%`, so a query of `100%` should
    return zero rows. Without ESCAPE, the trailing `%` becomes a wildcard
    and the query matches every code starting with '100' (or, depending
    on quoting, far more rows).
    """
    r = client.get("/admin/people/_locale_search?q=100%25", headers=AUTH_HEADERS)
    assert r.status_code == 200
    assert _option_codes(r.text) == [], _option_codes(r.text)


# ---- Script search ------------------------------------------------------


async def test_script_search_empty_q_renders_no_options(client, seeded_lookup_tables):
    r = client.get("/admin/people/_script_search?q=", headers=AUTH_HEADERS)
    assert r.status_code == 200
    assert _option_codes(r.text) == []


async def test_script_search_matches_on_code(client, seeded_lookup_tables):
    r = client.get("/admin/people/_script_search?q=Latn", headers=AUTH_HEADERS)
    assert r.status_code == 200
    assert "Latn" in _option_codes(r.text)


async def test_script_search_matches_on_name(client, seeded_lookup_tables):
    r = client.get("/admin/people/_script_search?q=Latin", headers=AUTH_HEADERS)
    assert r.status_code == 200
    assert "Latn" in _option_codes(r.text)


async def test_script_search_option_shape(client, seeded_lookup_tables):
    r = client.get("/admin/people/_script_search?q=Latn", headers=AUTH_HEADERS)
    assert 'role="option"' in r.text
    assert 'data-id="Latn"' in r.text
    assert 'data-label="Latn — ' in r.text


async def test_script_search_caps_at_limit(client, seeded_lookup_tables):
    r = client.get("/admin/people/_script_search?q=a&limit=3", headers=AUTH_HEADERS)
    assert r.status_code == 200
    assert len(_option_codes(r.text)) <= 3


async def test_script_search_sorted_by_code_asc(client, seeded_lookup_tables):
    r = client.get("/admin/people/_script_search?q=a", headers=AUTH_HEADERS)
    codes = _option_codes(r.text)
    assert codes == sorted(codes)


async def test_script_search_requires_auth(client):
    r = client.get("/admin/people/_script_search?q=Latn", follow_redirects=False)
    assert r.status_code in (302, 307)


async def test_script_search_treats_underscore_as_literal(client, seeded_lookup_tables):
    """`_` in user input must be ESCAPE'd, not interpreted as ILIKE single-char wildcard.

    ISO 15924 codes are 4 letters, no underscores. A 4-char query like `L_tn`
    without escape would match `Latn`/`Lstn`/etc. via the `_` wildcard;
    with escape, only literal `L_tn` (no real code) is matched.
    """
    r = client.get("/admin/people/_script_search?q=L_tn", headers=AUTH_HEADERS)
    assert r.status_code == 200
    assert _option_codes(r.text) == [], _option_codes(r.text)
