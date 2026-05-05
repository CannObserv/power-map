"""Phase 2c Task 3 — visual-first display ordering + reading_of enrichment.

The person detail names table must:
- Render visual rows (name_type NOT IN reading/romanization/mrz) sorted
  canonical-first by name.
- Render each visual row's child reading rows immediately after it,
  sorted by name_type then name.
- Surface `reading_of_name` and `reading_child_count` in the row context
  so the read-row template can show "↳ romanization of: <parent>" and
  the cascade-aware delete confirm.
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
def person_with_two_visuals_and_readings():
    """One person with:
      - Visual A (legal, canonical) + Reading A (romanization, child of A)
      - Visual B (preferred) + MRZ B (mrz, child of B) + Reading B (reading, child of B)

    Expected order on the detail page:
      Visual A
        Reading A
      Visual B
        MRZ B
        Reading B
    """
    dsn = _dsn()
    pid = generate_id()
    nid_a_visual = generate_id()
    nid_a_reading = generate_id()
    nid_b_visual = generate_id()
    nid_b_mrz = generate_id()
    nid_b_reading = generate_id()

    async def setup():
        conn = await asyncpg.connect(dsn)
        await apply_schema(conn)
        try:
            await conn.execute("INSERT INTO people (id) VALUES ($1)", pid)
            await conn.execute(
                "INSERT INTO person_names"
                " (id, person_id, name, name_type, is_canonical, visibility)"
                " VALUES ($1, $2, 'Visual A Smoketest', 'legal', TRUE, 'public')",
                nid_a_visual, pid,
            )
            await conn.execute(
                "INSERT INTO person_names"
                " (id, person_id, name, name_type, is_canonical, visibility, reading_of_id)"
                " VALUES ($1, $2, 'reading-a', 'romanization', FALSE, 'public', $3)",
                nid_a_reading, pid, nid_a_visual,
            )
            await conn.execute(
                "INSERT INTO person_names"
                " (id, person_id, name, name_type, is_canonical, visibility)"
                " VALUES ($1, $2, 'Visual B Smoketest', 'preferred', FALSE, 'public')",
                nid_b_visual, pid,
            )
            await conn.execute(
                "INSERT INTO person_names"
                " (id, person_id, name, name_type, is_canonical, visibility, reading_of_id)"
                " VALUES ($1, $2, 'MRZ-B', 'mrz', FALSE, 'public', $3)",
                nid_b_mrz, pid, nid_b_visual,
            )
            await conn.execute(
                "INSERT INTO person_names"
                " (id, person_id, name, name_type, is_canonical, visibility, reading_of_id)"
                " VALUES ($1, $2, 'reading-b', 'reading', FALSE, 'public', $3)",
                nid_b_reading, pid, nid_b_visual,
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
    yield {
        "pid": pid,
        "a_visual": nid_a_visual, "a_reading": nid_a_reading,
        "b_visual": nid_b_visual, "b_mrz": nid_b_mrz, "b_reading": nid_b_reading,
    }
    asyncio.run(teardown())


def _row_positions(html: str, ids: list[str]) -> dict[str, int]:
    """Return {row_id: position} via the `id="name-row-<row_id>"` markup."""
    return {rid: html.find(f'name-row-{rid}') for rid in ids}


# ---- Display order: visual rows first, children grouped underneath -----


def test_visual_row_precedes_its_readings(client, person_with_two_visuals_and_readings):
    f = person_with_two_visuals_and_readings
    r = client.get(f"/admin/people/{f['pid']}/", headers=AUTH_HEADERS)
    assert r.status_code == 200
    pos = _row_positions(
        r.text,
        [f["a_visual"], f["a_reading"], f["b_visual"], f["b_mrz"], f["b_reading"]],
    )
    # Visual A (canonical) → Reading A → Visual B → MRZ-B / Reading-B
    # (children sorted by name_type then name within each parent).
    assert pos[f["a_visual"]] < pos[f["a_reading"]], pos
    assert pos[f["a_reading"]] < pos[f["b_visual"]], pos
    assert pos[f["b_visual"]] < pos[f["b_mrz"]], pos
    assert pos[f["b_visual"]] < pos[f["b_reading"]], pos


def test_canonical_visual_row_precedes_non_canonical(
    client, person_with_two_visuals_and_readings,
):
    """Canonical=TRUE comes first among the visual rows."""
    f = person_with_two_visuals_and_readings
    r = client.get(f"/admin/people/{f['pid']}/", headers=AUTH_HEADERS)
    pos = _row_positions(r.text, [f["a_visual"], f["b_visual"]])
    assert pos[f["a_visual"]] < pos[f["b_visual"]], pos


def test_children_sorted_by_name_type_within_group(
    client, person_with_two_visuals_and_readings,
):
    """B's children: 'mrz' < 'reading' alphabetically."""
    f = person_with_two_visuals_and_readings
    r = client.get(f"/admin/people/{f['pid']}/", headers=AUTH_HEADERS)
    pos = _row_positions(r.text, [f["b_mrz"], f["b_reading"]])
    assert pos[f["b_mrz"]] < pos[f["b_reading"]], pos


# ---- reading_of_name surfaces in the subtitle --------------------------


def test_subtitle_shows_parent_name_for_linked_rows(
    client, person_with_two_visuals_and_readings,
):
    f = person_with_two_visuals_and_readings
    r = client.get(f"/admin/people/{f['pid']}/", headers=AUTH_HEADERS)
    # Reading A's subtitle should reference its parent visible name.
    assert "↳ romanization of:" in r.text
    assert "Visual A Smoketest" in r.text  # the parent name renders as <em> body
    # MRZ B's subtitle.
    assert "↳ mrz of:" in r.text
    assert "Visual B Smoketest" in r.text


def test_subtitle_absent_for_unlinked_rows(
    client, person_with_two_visuals_and_readings,
):
    """Visual rows don't have a reading_of_id and shouldn't render the subtitle."""
    f = person_with_two_visuals_and_readings
    r = client.get(f"/admin/people/{f['pid']}/", headers=AUTH_HEADERS)
    # The "↳" arrow only appears for linked rows; with 3 children we expect 3 occurrences.
    assert r.text.count("↳") == 3, r.text.count("↳")


# ---- reading_child_count surfaces on the parent's delete confirm -------


def test_parent_delete_confirm_mentions_cascade(
    client, person_with_two_visuals_and_readings,
):
    f = person_with_two_visuals_and_readings
    r = client.get(f"/admin/people/{f['pid']}/", headers=AUTH_HEADERS)
    # Visual A has 1 child (Reading A); confirm copy mentions it.
    assert "1 linked reading row" in r.text or "1 linked reading rows" in r.text
    # Visual B has 2 children; confirm copy mentions it.
    assert "2 linked reading row" in r.text or "2 linked reading rows" in r.text


def test_child_delete_confirm_uses_default_copy(
    client, person_with_two_visuals_and_readings,
):
    """Reading rows have no children themselves; their delete confirm
    should be the default 'Delete this name?' copy."""
    f = person_with_two_visuals_and_readings
    r = client.get(f"/admin/people/{f['pid']}/", headers=AUTH_HEADERS)
    # The default "Delete this name?" should appear at least 3 times
    # (once per child row, since none of them have descendants).
    assert r.text.count('hx-confirm="Delete this name?"') >= 3
