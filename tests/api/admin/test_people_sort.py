"""Phase 2b Task 4 — diacritic-aware ICU sort + sort_as override.

Person list and typeahead must order by COALESCE(sort_as, name) under
the "und-x-icu" collation. ASCII-default sort places "Zebra" before
"Åberg" (capital ASCII < high-Unicode); ICU "und" places Å near A.
"""

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from src.api.admin.deps import get_db
from src.api.main import app
from src.core.db import generate_id

pytestmark = [
    pytest.mark.integration,
]

AUTH_HEADERS = {"X-ExeDev-UserID": "usr_test", "X-ExeDev-Email": "admin@test.com"}


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


@pytest_asyncio.fixture(loop_scope="session")
async def people_with_diacritic_names(db):
    """Three people: 'Zebra', 'Åberg', 'Aaron' — ICU sort: Aaron, Åberg, Zebra."""
    p_aaron = generate_id()
    p_aberg = generate_id()
    p_zebra = generate_id()

    for pid, label in (
        (p_aaron, "Aaron Smoketest"),
        (p_aberg, "Åberg Smoketest"),
        (p_zebra, "Zebra Smoketest"),
    ):
        await db.execute("INSERT INTO people (id) VALUES ($1)", pid)
        await db.execute(
            "INSERT INTO person_names"
            " (id, person_id, name, name_type, is_canonical, visibility)"
            " VALUES ($1, $2, $3, 'legal', TRUE, 'public')",
            generate_id(),
            pid,
            label,
        )

    yield p_aaron, p_aberg, p_zebra


@pytest_asyncio.fixture(loop_scope="session")
async def person_with_sort_as_override(db):
    """One person: visible name 'van der Meer', sort_as 'Meer, van der'.

    Default sort by name puts 'van der Meer' near 'v'. With sort_as,
    the row should sort under 'm'.
    """
    pid = generate_id()

    await db.execute("INSERT INTO people (id) VALUES ($1)", pid)
    await db.execute(
        "INSERT INTO person_names"
        " (id, person_id, name, name_type, is_canonical, visibility, sort_as)"
        " VALUES ($1, $2, 'van der Meer Smoketest', 'legal', TRUE, 'public',"
        " 'Meer, van der Smoketest')",
        generate_id(),
        pid,
    )

    yield pid


def _name_positions(html: str, names: list[str]) -> dict[str, int]:
    """Return {name: index_in_html} for the first occurrence of each name."""
    return {n: html.find(n) for n in names}


# ---- Diacritic-aware sort under ICU "und" collation -----------------------


async def test_people_list_sorts_diacritics_with_und_x_icu(client, people_with_diacritic_names):
    r = await client.get("/admin/people/?q=Smoketest", headers=AUTH_HEADERS)
    assert r.status_code == 200
    pos = _name_positions(r.text, ["Aaron Smoketest", "Åberg Smoketest", "Zebra Smoketest"])
    # ICU "und": Aaron < Åberg < Zebra (A-with-ring near A).
    assert pos["Aaron Smoketest"] < pos["Åberg Smoketest"] < pos["Zebra Smoketest"], (
        f"ICU sort order broken: {pos}"
    )


async def test_people_search_sorts_diacritics_with_und_x_icu(client, people_with_diacritic_names):
    r = await client.get("/admin/people/search/?q=Smoketest", headers=AUTH_HEADERS)
    assert r.status_code == 200
    pos = _name_positions(r.text, ["Aaron Smoketest", "Åberg Smoketest", "Zebra Smoketest"])
    assert pos["Aaron Smoketest"] < pos["Åberg Smoketest"] < pos["Zebra Smoketest"], pos


# ---- sort_as override ----------------------------------------------------


async def test_sort_as_overrides_visible_name_in_list(
    client,
    people_with_diacritic_names,
    person_with_sort_as_override,
):
    """`van der Meer Smoketest` with sort_as='Meer, van der Smoketest'
    should sort under 'M', between 'Aaron' and 'Zebra' — not under 'V'."""
    r = await client.get("/admin/people/?q=Smoketest", headers=AUTH_HEADERS)
    pos = _name_positions(
        r.text,
        [
            "Aaron Smoketest",
            "Åberg Smoketest",
            "van der Meer Smoketest",
            "Zebra Smoketest",
        ],
    )
    # All four must be present.
    assert all(v >= 0 for v in pos.values()), pos
    # van der Meer (sort_as=Meer...) sits between Åberg and Zebra in ICU order.
    assert pos["Åberg Smoketest"] < pos["van der Meer Smoketest"] < pos["Zebra Smoketest"], pos
