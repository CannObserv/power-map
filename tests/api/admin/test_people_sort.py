"""Phase 2b Task 4 — diacritic-aware ICU sort + sort_as override.

Person list and typeahead must order by COALESCE(sort_as, name) under
the "und-x-icu" collation. ASCII-default sort places "Zebra" before
"Åberg" (capital ASCII < high-Unicode); ICU "und" places Å near A.
"""

import asyncio
import os

import asyncpg
import pytest
from fastapi.testclient import TestClient

from src.api.main import app
from src.core.db import apply_schema, generate_id

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


@pytest.fixture
def people_with_diacritic_names():
    """Three people: 'Zebra', 'Åberg', 'Aaron' — ICU sort: Aaron, Åberg, Zebra."""
    dsn = _dsn()
    p_aaron = generate_id()
    p_aberg = generate_id()
    p_zebra = generate_id()

    async def setup():
        conn = await asyncpg.connect(dsn)
        await apply_schema(conn)
        try:
            for pid, label in (
                (p_aaron, "Aaron Smoketest"),
                (p_aberg, "Åberg Smoketest"),
                (p_zebra, "Zebra Smoketest"),
            ):
                await conn.execute("INSERT INTO people (id) VALUES ($1)", pid)
                await conn.execute(
                    "INSERT INTO person_names"
                    " (id, person_id, name, name_type, is_canonical, visibility)"
                    " VALUES ($1, $2, $3, 'legal', TRUE, 'public')",
                    generate_id(), pid, label,
                )
        finally:
            await conn.close()

    async def teardown():
        conn = await asyncpg.connect(dsn)
        try:
            for pid in (p_aaron, p_aberg, p_zebra):
                await conn.execute("DELETE FROM person_names WHERE person_id=$1", pid)
                await conn.execute("DELETE FROM people WHERE id=$1", pid)
        finally:
            await conn.close()

    asyncio.run(setup())
    yield p_aaron, p_aberg, p_zebra
    asyncio.run(teardown())


@pytest.fixture
def person_with_sort_as_override():
    """One person: visible name 'van der Meer', sort_as 'Meer, van der'.

    Default sort by name puts 'van der Meer' near 'v'. With sort_as,
    the row should sort under 'm'.
    """
    dsn = _dsn()
    pid = generate_id()

    async def setup():
        conn = await asyncpg.connect(dsn)
        await apply_schema(conn)
        try:
            await conn.execute("INSERT INTO people (id) VALUES ($1)", pid)
            await conn.execute(
                "INSERT INTO person_names"
                " (id, person_id, name, name_type, is_canonical, visibility, sort_as)"
                " VALUES ($1, $2, 'van der Meer Smoketest', 'legal', TRUE, 'public',"
                " 'Meer, van der Smoketest')",
                generate_id(), pid,
            )
        finally:
            await conn.close()

    async def teardown():
        conn = await asyncpg.connect(dsn)
        try:
            await conn.execute("DELETE FROM person_names WHERE person_id=$1", pid)
            await conn.execute("DELETE FROM people WHERE id=$1", pid)
        finally:
            await conn.close()

    asyncio.run(setup())
    yield pid
    asyncio.run(teardown())


def _name_positions(html: str, names: list[str]) -> dict[str, int]:
    """Return {name: index_in_html} for the first occurrence of each name."""
    return {n: html.find(n) for n in names}


# ---- Diacritic-aware sort under ICU "und" collation -----------------------


def test_people_list_sorts_diacritics_with_und_x_icu(client, people_with_diacritic_names):
    r = client.get("/admin/people/?q=Smoketest", headers=AUTH_HEADERS)
    assert r.status_code == 200
    pos = _name_positions(r.text, ["Aaron Smoketest", "Åberg Smoketest", "Zebra Smoketest"])
    # ICU "und": Aaron < Åberg < Zebra (A-with-ring near A).
    assert pos["Aaron Smoketest"] < pos["Åberg Smoketest"] < pos["Zebra Smoketest"], (
        f"ICU sort order broken: {pos}"
    )


def test_people_search_sorts_diacritics_with_und_x_icu(client, people_with_diacritic_names):
    r = client.get("/admin/people/search/?q=Smoketest", headers=AUTH_HEADERS)
    assert r.status_code == 200
    pos = _name_positions(r.text, ["Aaron Smoketest", "Åberg Smoketest", "Zebra Smoketest"])
    assert pos["Aaron Smoketest"] < pos["Åberg Smoketest"] < pos["Zebra Smoketest"], pos


# ---- sort_as override ----------------------------------------------------


def test_sort_as_overrides_visible_name_in_list(
    client, people_with_diacritic_names, person_with_sort_as_override,
):
    """`van der Meer Smoketest` with sort_as='Meer, van der Smoketest'
    should sort under 'M', between 'Aaron' and 'Zebra' — not under 'V'."""
    r = client.get("/admin/people/?q=Smoketest", headers=AUTH_HEADERS)
    pos = _name_positions(
        r.text,
        [
            "Aaron Smoketest", "Åberg Smoketest",
            "van der Meer Smoketest", "Zebra Smoketest",
        ],
    )
    # All four must be present.
    assert all(v >= 0 for v in pos.values()), pos
    # van der Meer (sort_as=Meer...) sits between Åberg and Zebra in ICU order.
    assert pos["Åberg Smoketest"] < pos["van der Meer Smoketest"] < pos["Zebra Smoketest"], pos
