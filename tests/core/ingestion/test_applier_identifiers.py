"""The diff and writer for person identifiers (#710): a presence-only child binding.

`desired_person_identifiers` asserts (type, value) pairs on a person, matched by
key; nothing is owned beyond the key, so a match is a noop and a miss is an
insert. `exclusive`: a key another PM row already holds is a conflict, never a
second holder — `resolve_entity` could not choose between them. Every other
identifier on the person is invisible, and nothing is ever removed.
"""

import dataclasses
import itertools

import pytest

pytest.importorskip("duckdb")

from src.core.ingestion.applier import DesiredState, diff_desired  # noqa: E402
from src.core.ingestion.applier_report import verdict_for  # noqa: E402
from src.core.ingestion.applier_write import plan_statements  # noqa: E402
from src.core.ingestion.crosswalk import PRODUCER_SOURCE  # noqa: E402
from src.core.ingestion.mapping import load_manifest  # noqa: E402
from tests.core.ingestion.applier_fakes import FakeLiveStore  # noqa: E402

MANIFEST = load_manifest()
IDS = "desired_person_identifiers"
P1, P2, P3 = "01P1", "01P2", "01P3"
PM1, PM2, PMX = "01M1", "01M2", "01MX"  # PMX: a PM person no anchor names
ROSTER, MEMBER, WIKIDATA = "EIT_ROSTER", "EIT_MEMBER", "EIT_WIKIDATA"
LOOKUPS = {
    ("entity_identifier_types", "slug", "id"): {
        "person_wa_legislature_roster": ROSTER,
        "person_wa_legislature_member_id": MEMBER,
        "person_wikidata": WIKIDATA,
    }
}


def xw(producer_id, pm_id):
    return {
        "source": PRODUCER_SOURCE,
        "kind": "person",
        "producer_id": producer_id,
        "pm_id": pm_id,
        "resolution": "live",
    }


def ident(row_id, entity_id, type_id, value):
    return {
        "id": row_id,
        "entity_id": entity_id,
        "entity_identifier_type_id": type_id,
        "value": value,
    }


def claim(pm_id, producer_id, value, identifier_type="person_wa_legislature_roster"):
    return {
        "pm_id": pm_id,
        "producer_id": producer_id,
        "identifier_type": identifier_type,
        "value": value,
    }


def _store(*rows):
    return FakeLiveStore(
        crosswalk=[xw(P1, PM1), xw(P2, PM2)],
        tables={
            "people": [{"id": m, "archived_at": None} for m in (PM1, PM2, PMX)],
            "identifiers": list(rows),
        },
        lookups=LOOKUPS,
    )


def _state(*claims, people=((P1, PM1), (P2, PM2))) -> DesiredState:
    tables = {n: [] for n in MANIFEST.tables}
    tables["desired_people"] = [{"pm_id": m, "producer_id": p} for p, m in people]
    tables[IDS] = list(claims)
    return DesiredState(tables=tables, build_info=None)


def _ids(diff) -> dict[str, list]:
    out: dict[str, list] = {}
    for e in diff.entries:
        if e.table == IDS:
            out.setdefault(e.kind, []).append(e)
    return out


# --- diff -------------------------------------------------------------------------


async def test_a_key_pm_lacks_is_an_insert_of_type_and_value():
    diff = await diff_desired(_state(claim(PM1, P1, "juriebsmith:1943")), MANIFEST, _store())

    (insert,) = _ids(diff)["insert"]
    assert insert.entry_id == f"{IDS}:{P1}|person_wa_legislature_roster|juriebsmith:1943"
    assert insert.pm_id == PM1
    assert insert.changes == {
        "entity_identifier_type_id": (None, ROSTER),
        "value": (None, "juriebsmith:1943"),
    }


async def test_a_key_the_person_holds_is_a_noop():
    store = _store(ident("i1", PM1, ROSTER, "juriebsmith:1933"))

    diff = await diff_desired(_state(claim(PM1, P1, "juriebsmith:1933")), MANIFEST, store)

    assert set(_ids(diff)) == {"noop"}
    assert _ids(diff)["noop"][0].row_id == "i1"


async def test_a_second_key_of_one_type_is_an_insert_beside_the_first():
    """A returning legislator: every published key, no one-per-person rule (#710 decision 2)."""
    store = _store(ident("i1", PM1, ROSTER, "peterabbarno:2019"))

    diff = await diff_desired(
        _state(claim(PM1, P1, "peterabbarno:2019"), claim(PM1, P1, "peterabbarno:2021")),
        MANIFEST,
        store,
    )

    assert {k: len(v) for k, v in _ids(diff).items()} == {"noop": 1, "insert": 1}


async def test_the_same_value_under_another_type_does_not_satisfy_the_claim():
    store = _store(ident("i1", PM1, MEMBER, "31526"))

    diff = await diff_desired(_state(claim(PM1, P1, "31526")), MANIFEST, store)

    assert set(_ids(diff)) == {"insert"}


async def test_a_key_another_pm_person_holds_is_a_conflict_not_a_second_holder():
    """The hidden duplicate the first run surfaces; measured 0 in prod on 2026-10-28."""
    store = _store(ident("i9", PMX, ROSTER, "juriebsmith:1943"))

    diff = await diff_desired(_state(claim(PM1, P1, "juriebsmith:1943")), MANIFEST, store)

    (conflict,) = _ids(diff)["conflict"]
    assert "insert" not in _ids(diff)
    assert PMX in conflict.reason
    assert verdict_for(diff, MANIFEST.thresholds).verdict == "blocked"


async def test_a_create_whose_key_pm_already_holds_is_a_conflict():
    """A key on a PM person is a stronger twin signal than a name (#533's hint)."""
    store = _store(ident("i9", PMX, ROSTER, "juriebsmith:1943"))
    state = _state(claim(None, P3, "juriebsmith:1943"), people=((P1, PM1), (P2, PM2), (P3, None)))

    diff = await diff_desired(state, MANIFEST, store)

    (conflict,) = _ids(diff)["conflict"]
    assert conflict.producer_id == P3 and PMX in conflict.reason


async def test_a_creates_new_key_is_an_insert_pending_the_create():
    state = _state(claim(None, P3, "emilyalvarado:2023"), people=((P1, PM1), (P3, None)))

    diff = await diff_desired(state, MANIFEST, _store())

    (insert,) = _ids(diff)["insert"]
    assert insert.pm_id is None and "create" in insert.reason


async def test_one_key_claimed_for_two_persons_in_one_run_conflicts_on_both():
    diff = await diff_desired(
        _state(claim(PM1, P1, "dup:2001"), claim(PM2, P2, "dup:2001")), MANIFEST, _store()
    )

    assert {e.producer_id for e in _ids(diff)["conflict"]} == {P1, P2}
    assert "insert" not in _ids(diff)


async def test_identifiers_of_types_the_binding_does_not_claim_are_invisible():
    """Presence only: PM's wikidata row, and a roster key the snapshot dropped, stay."""
    store = _store(
        ident("i1", PM1, WIKIDATA, "Q42"),
        ident("i2", PM1, ROSTER, "dropped:1999"),
    )

    diff = await diff_desired(_state(claim(PM1, P1, "kept:2001")), MANIFEST, store)

    assert {k: len(v) for k, v in _ids(diff).items()} == {"insert": 1}


async def test_inserts_count_under_the_updates_threshold():
    """`--max-updates` caps a supervised first run (#710 correction 2)."""
    diff = await diff_desired(
        _state(claim(PM1, P1, "a:2001"), claim(PM2, P2, "b:2001")), MANIFEST, _store()
    )
    capped = dataclasses.replace(MANIFEST.thresholds, updates=1)

    assert verdict_for(diff, capped).exceeded == {"updates": (2, 1)}


# --- write ------------------------------------------------------------------------


async def test_an_insert_writes_one_identifiers_row_on_the_person():
    diff = await diff_desired(_state(claim(PM1, P1, "juriebsmith:1943")), MANIFEST, _store())

    statements, _ = plan_statements(
        diff, MANIFEST, source=PRODUCER_SOURCE, ids=(f"NEW{n}" for n in itertools.count())
    )

    (insert,) = [s for s in statements if s.kind == "insert"]
    assert insert.sql == (
        "INSERT INTO identifiers (id, entity_id, entity_identifier_type_id, value)"
        " VALUES ($1, $2, $3, $4)"
    )
    assert insert.args == ("NEW0", PM1, ROSTER, "juriebsmith:1943")


async def test_a_stale_row_does_not_make_anothers_insert_a_conflict():
    """CR 2: a row that will be stale writes nothing, so it claims nothing."""
    state = _state(claim(PM1, P1, "dup:2001"), claim(None, P3, "dup:2001"))

    diff = await diff_desired(state, MANIFEST, _store())

    assert {k: len(v) for k, v in _ids(diff).items()} == {"insert": 1, "stale": 1}
