"""Settings tables re-render their whole ``<tbody>`` on every write (#687, #683).

The settings pages (API keys, identifier types, general/social link types)
follow ``docs/UI.md`` § The tbody contract: create, edit and delete answer
with the table's rows partial, so the server owns the empty-state row; a bad
save answers with the form row, retargeted onto itself (``HX-Retarget``),
200 for HTMX and 422 for everyone else.

The static half (no row-targeting write control) is ``test_empty_state_sweep.py``.
"""

import hashlib
import os
from dataclasses import dataclass

import pytest
import pytest_asyncio
from fastapi.templating import Jinja2Templates
from httpx import ASGITransport, AsyncClient

from src.api.admin.deps import get_db
from src.api.main import app
from src.core.db import generate_id
from tests.api.admin.html_slices import tbody_html

pytestmark = [pytest.mark.integration]

AUTH_HEADERS = {"X-ExeDev-UserID": "usr_test", "X-ExeDev-Email": "admin@test.com"}
HTMX_HEADERS = {**AUTH_HEADERS, "HX-Request": "true"}
EMPTY_MARKER = "data-empty-row"


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
    """Lifespan-less client sharing the rolled-back connection (#288)."""

    async def _get_db_override():
        yield db

    app.dependency_overrides[get_db] = _get_db_override
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c
    app.dependency_overrides.pop(get_db, None)


# ---- the two lookup tables: identifier types, link types (by scope) ------------


@dataclass(frozen=True)
class TypeTable:
    """One settings lookup table."""

    page: str
    base: str  # write-route prefix
    tbody_id: str
    new_row_id: str
    table: str
    extra: tuple[tuple[str, str], ...] = ()  # form fields beyond display_name/slug
    sql_extra: str = ""  # INSERT columns/values beyond display_name/slug

    def form(self, name: str, slug: str) -> dict:
        return {"display_name": name, "slug": slug, **dict(self.extra)}


IDENTIFIER_TYPES = TypeTable(
    page="/admin/settings/identifier-types/",
    base="/admin/settings/identifier-types/",
    tbody_id="identifier-types-body",
    new_row_id="identifier-type-row-new",
    table="entity_identifier_types",
    extra=(("full_name", "Tbody Full Name"), ("entity_type", "organization")),
)
TABLES = [
    IDENTIFIER_TYPES,
    *(
        TypeTable(
            page="/admin/settings/link-types/",
            base=f"/admin/settings/link-types/{scope}/",
            tbody_id=f"{scope}-link-types-body",
            new_row_id=f"{scope}-link-type-row-new",
            table="link_types",
        )
        for scope in ("general", "social")
    ),
]
_IDS = [t.tbody_id for t in TABLES]


async def _seed_type(db, t: TypeTable, name: str) -> tuple[str, str]:
    tid = generate_id()
    slug = f"tbody-{tid.lower()}"
    if t.table == "link_types":
        await db.execute(
            "INSERT INTO link_types (id, display_name, slug, is_social) VALUES ($1,$2,$3,$4)",
            tid,
            name,
            slug,
            t.tbody_id.startswith("social"),
        )
    else:
        await db.execute(
            "INSERT INTO entity_identifier_types (id, display_name, slug, full_name, entity_type)"
            " VALUES ($1,$2,$3,'Tbody Full Name','organization')",
            tid,
            name,
            slug,
        )
    return tid, slug


@pytest.mark.parametrize("t", TABLES, ids=_IDS)
async def test_page_tbody_and_new_row_ids(client, t):
    page = await client.get(t.page, headers=AUTH_HEADERS)
    assert page.status_code == 200
    assert f'id="{t.tbody_id}"' in page.text
    new = await client.get(f"{t.base}new-row/", headers=HTMX_HEADERS)
    assert f'id="{t.new_row_id}"' in new.text
    assert f'hx-target="#{t.tbody_id}"' in new.text


@pytest.mark.parametrize("t", TABLES, ids=_IDS)
async def test_create_returns_the_rows(client, db, t):
    sibling, _ = await _seed_type(db, t, "Tbody Sibling")  # don't lean on the reference seed
    slug = f"tbody-new-{generate_id().lower()}"
    r = await client.post(t.base, headers=HTMX_HEADERS, data=t.form("Tbody Created", slug))
    assert r.status_code == 200
    assert "HX-Retarget" not in r.headers
    assert slug in r.text
    assert f"row-{sibling}" in r.text  # every row, not just the new one
    assert EMPTY_MARKER not in r.text


@pytest.mark.parametrize("t", TABLES, ids=_IDS)
async def test_edit_returns_the_rows(client, db, t):
    tid, slug = await _seed_type(db, t, "Tbody Before")
    sibling, _ = await _seed_type(db, t, "Tbody Sibling")
    edit = await client.get(f"{t.base}{tid}/edit-row/", headers=HTMX_HEADERS)
    assert f'hx-target="#{t.tbody_id}"' in edit.text

    r = await client.post(
        f"{t.base}{tid}/edit-row/", headers=HTMX_HEADERS, data=t.form("Tbody After", slug)
    )
    assert r.status_code == 200
    assert "Tbody After" in r.text
    assert f"row-{sibling}" in r.text
    assert EMPTY_MARKER not in r.text


@pytest.mark.parametrize("t", TABLES, ids=_IDS)
async def test_delete_returns_the_remaining_rows(client, db, t):
    tid, _ = await _seed_type(db, t, "Tbody Doomed")
    keep, _ = await _seed_type(db, t, "Tbody Kept")
    r = await client.delete(f"{t.base}{tid}/", headers=HTMX_HEADERS)
    assert r.status_code == 200
    assert f"row-{tid}" not in r.text
    assert f"row-{keep}" in r.text


@pytest.mark.parametrize("t", TABLES, ids=_IDS)
async def test_delete_non_htmx_redirects(client, db, t):
    tid, _ = await _seed_type(db, t, "Tbody Doomed")
    r = await client.delete(f"{t.base}{tid}/", headers=AUTH_HEADERS)
    assert r.status_code == 303
    assert r.headers["location"].endswith("?flash=removed")


@pytest.mark.parametrize("t", TABLES, ids=_IDS)
async def test_blank_slug_on_create_lands_on_the_new_row(client, db, t):
    r = await client.post(t.base, headers=HTMX_HEADERS, data=t.form("Tbody Typed", "  "))
    assert r.status_code == 200
    assert r.headers["HX-Retarget"] == f"#{t.new_row_id}"
    assert r.headers["HX-Reswap"] == "outerHTML"
    assert 'role="alert"' in r.text
    assert 'value="Tbody Typed"' in r.text  # the curator's input survives
    assert "Slug is required." in r.text

    plain = await client.post(t.base, headers=AUTH_HEADERS, data=t.form("Tbody Typed", "  "))
    assert plain.status_code == 422


@pytest.mark.parametrize("t", TABLES, ids=_IDS)
async def test_duplicate_slug_on_create_lands_on_the_new_row(client, db, t):
    _, slug = await _seed_type(db, t, "Tbody Original")
    r = await client.post(t.base, headers=HTMX_HEADERS, data=t.form("Tbody Clash", slug))
    assert r.status_code == 200
    assert r.headers["HX-Retarget"] == f"#{t.new_row_id}"
    assert "already in use" in r.text
    assert await db.fetchval(f"SELECT count(*) FROM {t.table} WHERE slug=$1", slug) == 1


@pytest.mark.parametrize("t", TABLES, ids=_IDS)
async def test_bad_edit_lands_on_its_row(client, db, t):
    tid, slug = await _seed_type(db, t, "Tbody Edited")
    _, other = await _seed_type(db, t, "Tbody Other")
    for data, needle in (
        (t.form("Tbody Edited", ""), "Slug is required."),
        (t.form("  ", slug), "Display name is required."),
        (t.form("Tbody Edited", other), "already in use"),
    ):
        r = await client.post(f"{t.base}{tid}/edit-row/", headers=HTMX_HEADERS, data=data)
        assert r.status_code == 200
        assert r.headers["HX-Retarget"].endswith(f"-row-{tid}")
        assert needle in r.text
    row = await db.fetchrow(f"SELECT slug, display_name FROM {t.table} WHERE id=$1", tid)
    assert (row["slug"], row["display_name"]) == (slug, "Tbody Edited")


async def test_invalid_entity_type_lands_on_the_new_row(client):
    data = {**IDENTIFIER_TYPES.form("Tbody", "tbody-bad-et"), "entity_type": "role"}
    r = await client.post(IDENTIFIER_TYPES.base, headers=HTMX_HEADERS, data=data)
    assert r.status_code == 200
    assert r.headers["HX-Retarget"] == "#identifier-type-row-new"
    assert "entity type" in r.text.lower()


_RENDER = Jinja2Templates(directory="src/templates").env


@pytest.mark.parametrize(
    ("partial", "ctx", "text"),
    [
        ("_identifier_type_rows.html", {}, "No identifier types."),
        ("_link_type_rows.html", {"scope": "general"}, "No general link types."),
        ("_link_type_rows.html", {"scope": "social"}, "No social link types."),
        ("_api_key_rows.html", {}, "No API keys yet."),
    ],
)
def test_rows_partial_renders_the_empty_row(partial, ctx, text):
    html = _RENDER.get_template(f"admin/settings/partials/{partial}").render(rows=[], **ctx)
    assert EMPTY_MARKER in html
    assert text in html


# ---- API keys: per-user, so the table really can be emptied ---------------------

KEYS = "/admin/settings/api-keys/"


async def _seed_key(db, label: str) -> str:
    await db.execute(
        "INSERT INTO app_users (id, email) VALUES ('usr_test','admin@test.com')"
        " ON CONFLICT (id) DO NOTHING"
    )
    await db.execute("DELETE FROM api_keys WHERE user_id='usr_test'")
    kid = generate_id()
    raw = "pm_" + os.urandom(16).hex()
    await db.execute(
        "INSERT INTO api_keys (id, user_id, label, key_prefix, key_hash) VALUES ($1,$2,$3,$4,$5)",
        kid,
        "usr_test",
        label,
        raw[:8],
        hashlib.sha256(raw.encode()).hexdigest(),
    )
    return kid


async def test_api_key_round_trip(client, db):
    kid = await _seed_key(db, "Tbody Key")
    page = await client.get(KEYS, headers=AUTH_HEADERS)
    assert f"api-key-row-{kid}" in tbody_html(page.text, "api-keys-body")
    assert EMPTY_MARKER not in tbody_html(page.text, "api-keys-body")

    edit = await client.get(f"{KEYS}{kid}/edit-row/", headers=HTMX_HEADERS)
    assert 'hx-target="#api-keys-body"' in edit.text
    r = await client.post(f"{KEYS}{kid}/edit-row/", headers=HTMX_HEADERS, data={"label": "Renamed"})
    assert r.status_code == 200
    assert "Renamed" in r.text and f"api-key-scopes-{kid}" in r.text
    assert EMPTY_MARKER not in r.text

    r = await client.delete(f"{KEYS}{kid}/", headers=HTMX_HEADERS)
    assert r.status_code == 200
    assert EMPTY_MARKER in r.text  # deleting the last key brings "No API keys yet." back
    assert "No API keys yet." in r.text


async def test_api_key_create_refreshes_the_tbody_out_of_band(client, db):
    await _seed_key(db, "Tbody Existing")
    await db.execute("DELETE FROM api_keys WHERE user_id='usr_test'")
    r = await client.post(KEYS, headers=HTMX_HEADERS, data={"label": "Tbody First"})
    assert r.status_code == 200
    assert "not be shown again" in r.text  # the one-time modal is the main swap
    oob = tbody_html(r.text, "api-keys-body")
    assert 'hx-swap-oob="innerHTML"' in oob
    assert "Tbody First" in oob
    assert EMPTY_MARKER not in oob
    assert "<template>" in r.text  # a bare <tbody> outside a table is dropped by the parser


async def test_api_key_blank_label_lands_on_its_row(client, db):
    kid = await _seed_key(db, "Tbody Key")
    r = await client.post(KEYS, headers=HTMX_HEADERS, data={"label": "  "})
    assert r.status_code == 200
    assert r.headers["HX-Retarget"] == "#api-key-row-new"
    assert 'role="alert"' in r.text
    r = await client.post(f"{KEYS}{kid}/edit-row/", headers=HTMX_HEADERS, data={"label": ""})
    assert r.status_code == 200
    assert r.headers["HX-Retarget"] == f"#api-key-row-{kid}"
    assert await db.fetchval("SELECT label FROM api_keys WHERE id=$1", kid) == "Tbody Key"
    plain = await client.post(f"{KEYS}{kid}/edit-row/", headers=AUTH_HEADERS, data={"label": ""})
    assert plain.status_code == 422
