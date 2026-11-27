"""Tests for src/core/org_kind_question.py — the one question Jev is asked (#877).

The production client and the gold-set builder both build their requests here,
so what the gold set scores is what runs. Pure tests first; the DB-backed ones
(``load_kinds``, ``load_states``) run in a rolled-back transaction.
"""

import inspect

import pytest
import pytest_asyncio

from src.core import org_kind_question as q
from src.core.db import generate_id

KINDS = [("business", "A for-profit company"), ("nonprofit", "A charity")]


def test_criteria_are_the_kinds_plus_none():
    crit = q.criteria(KINDS)
    assert list(crit) == ["business", "nonprofit", "none"]
    assert crit["business"] == "A for-profit company"
    assert crit["none"] == q.NONE_CRITERION


def test_criteria_refuse_a_kind_named_none():
    with pytest.raises(ValueError, match="none"):
        q.criteria([("none", "x")])


def test_questions_are_one_choice_named_kind():
    questions = q.build_questions(q.criteria(KINDS))
    assert list(questions) == [q.QUESTION_NAME]
    assert questions["kind"]["type"] == "choice"
    assert questions["kind"]["criteria"] == q.criteria(KINDS)
    assert "most specific" in questions["kind"]["instructions"]


def test_cap_titles_keeps_whole_titles_within_the_limit():
    titles = ["Member", "Executive Director", "Board Chair", "Treasurer"]
    assert q.cap_titles(titles, limit=30) == "Member; Executive Director"
    assert len(q.cap_titles(titles * 50)) <= q.ROLE_TITLES_CHARS


def test_cap_titles_dedupes_case_insensitively_and_collapses_whitespace():
    assert (
        q.cap_titles(["Member", "member", "Vice\nChair", "  Vice   Chair "]) == "Member; Vice Chair"
    )


def test_cap_titles_keeps_a_lone_title_longer_than_the_limit_out():
    assert q.cap_titles(["x" * 400]) == ""


def test_build_state_shape_is_stable():
    org = q.OrgState(name="Acme Co", listed_in=("Registry A",), role_titles=("Owner",))
    state = q.build_state(org)
    assert state == {
        "context": q.CONTEXT,
        "org": {"name": "Acme Co", "listed_in": ["Registry A"], "role_titles": "Owner"},
    }
    assert q.build_state(org) == state


def test_context_is_generic():
    # src/core stays WA-free (tests/test_src_core_wa_free.py); say so here too.
    for word in ("Washington", "WA ", "PDC", "WSLCB"):
        assert word not in q.CONTEXT


def test_state_queries_read_no_person_tables():
    sql = [v for k, v in vars(q).items() if k.endswith("_SQL")]
    assert len(sql) == 3
    src = inspect.getsource(q.load_states) + "".join(sql)
    for table in ("person_names", "people", "role_assignments"):
        assert table not in src


# --- DB-backed -----------------------------------------------------------


@pytest_asyncio.fixture(loop_scope="session")
async def db(db_pool):
    async with db_pool.acquire() as conn:
        tr = conn.transaction()
        await tr.start()
        try:
            yield conn
        finally:
            await tr.rollback()


async def _org(db, name: str | None) -> str:
    oid = generate_id()
    await db.execute("INSERT INTO organizations (id) VALUES ($1)", oid)
    if name is not None:
        await db.execute(
            "INSERT INTO organization_names (id, organization_id, name, is_canonical)"
            " VALUES ($1,$2,$3,TRUE)",
            generate_id(),
            oid,
            name,
        )
    return oid


async def _role(db, org_id: str, title: str, *, archived: bool = False) -> None:
    await db.execute(
        "INSERT INTO roles (id, organization_id, title, archived_at)"
        " VALUES ($1,$2,$3, CASE WHEN $4 THEN NOW() END)",
        generate_id(),
        org_id,
        title,
        archived,
    )


async def _identify(db, org_id: str, slug: str) -> None:
    type_id = await db.fetchval("SELECT id FROM entity_identifier_types WHERE slug=$1", slug)
    await db.execute(
        "INSERT INTO identifiers (id, entity_id, entity_identifier_type_id, value)"
        " VALUES ($1,$2,$3,$4)",
        generate_id(),
        org_id,
        type_id,
        f"v-{generate_id()[-8:]}",
    )


@pytest.mark.integration
async def test_load_kinds_is_the_seeded_list_in_id_order(db):
    kinds = await q.load_kinds(db)
    assert [k for k, _ in kinds][:3] == ["business", "nonprofit", "association"]
    assert len(kinds) == 11
    assert all(desc for _, desc in kinds)


@pytest.mark.integration
async def test_load_states_reads_name_registries_and_active_titles(db):
    oid = await _org(db, "Kind Probe Guild")
    await _identify(db, oid, "org_ubi")
    await _identify(db, oid, "org_ubi")
    await _role(db, oid, "Treasurer")
    await _role(db, oid, "Member")
    await _role(db, oid, "Founder", archived=True)
    ubi = await db.fetchval("SELECT full_name FROM entity_identifier_types WHERE slug='org_ubi'")

    states = await q.load_states(db, [oid])

    assert states[oid] == q.OrgState(
        name="Kind Probe Guild", listed_in=(ubi,), role_titles=("Member", "Treasurer")
    )


@pytest.mark.integration
async def test_load_states_leaves_out_internal_identifier_types(db):
    oid = await _org(db, "Internal Probe")
    internal = await db.fetchval(
        "SELECT slug FROM entity_identifier_types"
        " WHERE entity_type='organization' AND is_internal LIMIT 1"
    )
    if internal is None:
        pytest.skip("no internal organization identifier type seeded")
    await _identify(db, oid, internal)
    assert (await q.load_states(db, [oid]))[oid].listed_in == ()


@pytest.mark.integration
async def test_load_states_gives_an_unnamed_org_an_empty_name(db):
    oid = await _org(db, None)
    assert (await q.load_states(db, [oid]))[oid] == q.OrgState(
        name="", listed_in=(), role_titles=()
    )
