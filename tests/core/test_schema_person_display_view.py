"""Integration tests for v_person_display_names determinism (issue #308a).

The view is the display-name pointer, but `uq_person_canonical_name` is keyed
per (person_id, name_type, locale, script) — so a person may legitimately carry
several canonical rows. Without disambiguation the view emits one row per
canonical name, duplicating the person in every list query that joins it.
"""

import pytest
import pytest_asyncio

from src.core.db import generate_id
from src.core.observation import _PERSON_NAME_TYPE_PRIORITY

pytestmark = [
    pytest.mark.integration,
]


@pytest_asyncio.fixture(loop_scope="session")
async def conn(db_pool):
    """Pool-acquired connection wrapped in a rolled-back transaction."""
    async with db_pool.acquire() as c:
        tr = c.transaction()
        await tr.start()
        try:
            yield c
        finally:
            await tr.rollback()


async def _insert_person(conn):
    pid = generate_id()
    await conn.execute("INSERT INTO people (id) VALUES ($1)", pid)
    return pid


async def _add_name(
    conn,
    pid,
    name,
    *,
    name_type="legal",
    is_canonical=True,
    visibility="public",
    locale=None,
    script=None,
    sort_as=None,
):
    nid = generate_id()
    await conn.execute(
        "INSERT INTO person_names"
        " (id, person_id, name, name_type, locale, script, sort_as,"
        "  visibility, is_canonical)"
        " VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)",
        nid,
        pid,
        name,
        name_type,
        locale,
        script,
        sort_as,
        visibility,
        is_canonical,
    )
    return nid


async def _display(conn, pid):
    return await conn.fetch(
        "SELECT display_name, sort_key FROM v_person_display_names WHERE person_id = $1",
        pid,
    )


async def test_view_returns_single_row_for_single_canonical(conn):
    pid = await _insert_person(conn)
    await _add_name(conn, pid, "Alice Smith")
    rows = await _display(conn, pid)
    assert len(rows) == 1
    assert rows[0]["display_name"] == "Alice Smith"


async def test_view_returns_single_row_when_multiple_canonical_name_types(conn):
    """Two canonical rows in different name_type slots → still one display row."""
    pid = await _insert_person(conn)
    await _add_name(conn, pid, "Alice Smith", name_type="legal")
    await _add_name(conn, pid, "Alice", name_type="preferred")
    rows = await _display(conn, pid)
    assert len(rows) == 1


async def test_view_prefers_preferred_over_legal(conn):
    pid = await _insert_person(conn)
    await _add_name(conn, pid, "Alice Smith", name_type="legal")
    await _add_name(conn, pid, "Alice", name_type="preferred")
    rows = await _display(conn, pid)
    assert rows[0]["display_name"] == "Alice"


async def test_view_falls_back_to_legal_when_no_preferred(conn):
    pid = await _insert_person(conn)
    await _add_name(conn, pid, "Alice Smith", name_type="legal")
    await _add_name(conn, pid, "A. Smith", name_type="alias")
    rows = await _display(conn, pid)
    assert rows[0]["display_name"] == "Alice Smith"


async def test_view_single_row_across_locale_script_variants(conn):
    """Same name_type, different locale/script → uq permits both; view must not dupe."""
    pid = await _insert_person(conn)
    await _add_name(conn, pid, "Yamada Taro", name_type="legal", locale="en", script="Latn")
    await _add_name(conn, pid, "山田太郎", name_type="legal", locale="ja", script="Jpan")
    rows = await _display(conn, pid)
    assert len(rows) == 1


async def test_view_excludes_non_public_canonical(conn):
    """A canonical legal_only row must not surface as the display name."""
    pid = await _insert_person(conn)
    await _add_name(conn, pid, "Deadname", name_type="deadname", visibility="legal_only")
    rows = await _display(conn, pid)
    assert len(rows) == 1
    assert rows[0]["display_name"] is None


async def test_view_keeps_person_with_no_names(conn):
    """LEFT JOIN semantics preserved — person still present, display NULL."""
    pid = await _insert_person(conn)
    rows = await _display(conn, pid)
    assert len(rows) == 1
    assert rows[0]["display_name"] is None


# The view's full ladder, promotable types first (mirroring
# _PERSON_NAME_TYPE_PRIORITY) then the never-auto-promoted tail. Asserted in full
# so adding a name_type to the CHECK constraint without touching the view's CASE
# is caught here rather than silently sorting to the ELSE default (#308, CR2 #13).
# `deadname` is omitted: trg_deadname_visibility forces it to legal_only, so it
# can never appear in this view regardless of rank.
_EXPECTED_VIEW_LADDER = [
    "preferred",
    "legal",
    "alias",
    "stage",
    "religious",
    "maiden",
    "variant",
    "former",
    "initials",
    "romanization",
    "reading",
    "mrz",
]


def test_app_ladder_is_a_prefix_of_view_ladder():
    """The app ranks only promotable types; those must lead the view's ordering."""
    promotable = [t for t, _ in sorted(_PERSON_NAME_TYPE_PRIORITY.items(), key=lambda kv: kv[1])]
    assert _EXPECTED_VIEW_LADDER[: len(promotable)] == promotable


async def test_view_priority_agrees_with_app_priority(conn):
    """The view's CASE ladder must match _EXPECTED_VIEW_LADDER end to end.

    write_names promotes the row the view would display; if the two orderings
    disagree, PM canonicalizes one name and shows another. Asserts every adjacent
    pair empirically rather than parsing the view SQL.
    """
    for better, worse in zip(_EXPECTED_VIEW_LADDER, _EXPECTED_VIEW_LADDER[1:], strict=False):
        pid = await _insert_person(conn)
        # Insert the lower-priority row first so list order can't explain a pass.
        await _add_name(conn, pid, f"{worse} name", name_type=worse)
        await _add_name(conn, pid, f"{better} name", name_type=better)
        rows = await _display(conn, pid)
        assert len(rows) == 1
        assert rows[0]["display_name"] == f"{better} name", (
            f"view ranked {worse!r} above {better!r}"
        )


async def test_view_sort_key_follows_chosen_display_name(conn):
    pid = await _insert_person(conn)
    await _add_name(conn, pid, "Alice Smith", name_type="legal", sort_as="Smith, Alice")
    await _add_name(conn, pid, "Alice", name_type="preferred", sort_as="Zzz, Alice")
    rows = await _display(conn, pid)
    assert rows[0]["display_name"] == "Alice"
    assert rows[0]["sort_key"] == "Zzz, Alice"
