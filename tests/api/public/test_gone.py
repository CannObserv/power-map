"""A tombstoned id answers ``410 Gone`` with where it went (#607).

A merged or hard-deleted id used to ``404`` exactly like an id that never
existed, so a consumer reconciling by id could not follow a merge. The detail
GETs now read ``deleted_entities`` on a miss: ``410`` with ``merged_into`` = the
live end of the merge chain (``null`` for a genuine delete), ``404`` only when
there is no tombstone at all.

The live row is checked first, so a restore (#467) answers ``200`` even with a
stale tombstone left behind — and a tombstoned id has no ETag, so the answer is
the same with or without ``If-None-Match``.
"""

import ast
import hashlib
import os
from pathlib import Path

import pytest
import pytest_asyncio

from src.api.main import app
from src.core.db import generate_id
from src.core.merge_history import ENTITY_TABLE

PUBLIC_DIR = Path(__file__).resolve().parents[3] / "src" / "api" / "public"

# entity_type → detail path prefix. Jurisdictions are tombstoned by hard delete
# only (no merge folds one into another), so they sit outside the merge cases.
_MERGEABLE = {
    "person": "/api/v1/people",
    "organization": "/api/v1/orgs",
    "role": "/api/v1/roles",
    "role_assignment": "/api/v1/assignments",
}
_ALL = {**_MERGEABLE, "jurisdiction": "/api/v1/jurisdictions"}

_DETAIL_PATHS = {
    "/api/v1/people/{person_id}",
    "/api/v1/orgs/{org_id}",
    "/api/v1/roles/{role_id}",
    "/api/v1/assignments/{assignment_id}",
    "/api/v1/jurisdictions/{jurisdiction_id}",
}


@pytest_asyncio.fixture(loop_scope="session")
async def api_key(db):
    uid = generate_id()
    kid = generate_id()
    raw_key = "pm_" + os.urandom(16).hex()
    await db.execute("INSERT INTO app_users (id, email) VALUES ($1,$2)", uid, "gone@test.com")
    await db.execute(
        "INSERT INTO api_keys (id, user_id, label, key_prefix, key_hash) VALUES ($1,$2,$3,$4,$5)",
        kid,
        uid,
        "Gone Test Key",
        raw_key[:8],
        hashlib.sha256(raw_key.encode()).hexdigest(),
    )
    return raw_key


async def _live(db, entity_type: str, *, archived: bool = False) -> str:
    """Insert a minimal live row of ``entity_type`` and return its id."""
    eid = generate_id()
    if entity_type == "person":
        await db.execute("INSERT INTO people (id) VALUES ($1)", eid)
    elif entity_type == "organization":
        await db.execute("INSERT INTO organizations (id) VALUES ($1)", eid)
    elif entity_type == "role":
        org = await _live(db, "organization")
        await db.execute(
            "INSERT INTO roles (id, organization_id, title) VALUES ($1,$2,'Gone Tester')", eid, org
        )
    elif entity_type == "role_assignment":
        person, role = await _live(db, "person"), await _live(db, "role")
        await db.execute(
            "INSERT INTO role_assignments (id, person_id, role_id) VALUES ($1,$2,$3)",
            eid,
            person,
            role,
        )
    elif entity_type == "jurisdiction":
        type_id = await db.fetchval("SELECT id FROM jurisdiction_types LIMIT 1")
        await db.execute(
            "INSERT INTO jurisdictions (id, slug, name, type_id) VALUES ($1,$2,'Gone',$3)",
            eid,
            f"gone-{eid.lower()}",
            type_id,
        )
    else:
        raise AssertionError(entity_type)
    if archived:
        table = {
            "person": "people",
            "organization": "organizations",
            "role": "roles",
            "role_assignment": "role_assignments",
        }[entity_type]
        await db.execute(f"UPDATE {table} SET archived_at = NOW() WHERE id = $1", eid)  # noqa: S608
    return eid


async def _tombstone(db, entity_type: str, entity_id: str, merged_into: str | None) -> None:
    await db.execute(
        "INSERT INTO deleted_entities (entity_type, entity_id, merged_into) VALUES ($1,$2,$3)",
        entity_type,
        entity_id,
        merged_into,
    )


async def _get(client, api_key, entity_type, entity_id, **headers):
    return await client.get(
        f"{_ALL[entity_type]}/{entity_id}", headers={"X-API-Key": api_key, **headers}
    )


# ---------------------------------------------------------------------------
# Endpoint tier
# ---------------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.parametrize("entity_type", list(_MERGEABLE))
async def test_merged_id_answers_410_with_its_winner(client, db, api_key, entity_type):
    winner = await _live(db, entity_type)
    loser = generate_id()
    await _tombstone(db, entity_type, loser, winner)

    r = await _get(client, api_key, entity_type, loser)

    assert r.status_code == 410
    body = r.json()
    assert body == {
        "id": loser,
        "entity_type": entity_type,
        "deleted_at": body["deleted_at"],
        "merged_into": winner,
    }
    assert body["deleted_at"].endswith("Z")  # #440 wire format
    assert r.headers["cache-control"] == "no-cache"
    assert r.headers["vary"] == "X-API-Key"  # keyed per key, like every detail response
    assert "etag" not in r.headers


@pytest.mark.integration
@pytest.mark.parametrize("entity_type", list(_ALL))
async def test_genuine_delete_answers_410_without_a_winner(client, db, api_key, entity_type):
    dropped = generate_id()
    await _tombstone(db, entity_type, dropped, None)

    r = await _get(client, api_key, entity_type, dropped)

    assert r.status_code == 410
    assert r.json()["merged_into"] is None


@pytest.mark.integration
@pytest.mark.parametrize("entity_type", list(_ALL))
async def test_unknown_id_still_answers_404(client, api_key, entity_type):
    r = await _get(client, api_key, entity_type, generate_id())
    assert r.status_code == 404


@pytest.mark.integration
async def test_merge_chain_resolves_to_its_live_end(client, db, api_key):
    """A→B→C: only C exists, so A answers with C, not with the dead B."""
    final = await _live(db, "organization")
    middle, first = generate_id(), generate_id()
    await _tombstone(db, "organization", middle, final)
    await _tombstone(db, "organization", first, middle)

    r = await _get(client, api_key, "organization", first)

    assert r.status_code == 410
    assert r.json()["merged_into"] == final


@pytest.mark.integration
async def test_merge_chain_ending_in_a_delete_has_no_winner(client, db, api_key):
    middle, first = generate_id(), generate_id()
    await _tombstone(db, "person", middle, None)
    await _tombstone(db, "person", first, middle)

    r = await _get(client, api_key, "person", first)

    assert r.status_code == 410
    assert r.json()["merged_into"] is None


@pytest.mark.integration
async def test_archived_winner_is_still_named(client, db, api_key):
    """An archived row answers 200 on its own GET, so it is a valid place to land."""
    winner = await _live(db, "person", archived=True)
    loser = generate_id()
    await _tombstone(db, "person", loser, winner)

    r = await _get(client, api_key, "person", loser)

    assert r.json()["merged_into"] == winner


@pytest.mark.integration
@pytest.mark.parametrize("entity_type", list(_ALL))
async def test_live_row_wins_over_a_stale_tombstone(client, db, api_key, entity_type):
    """A restore that un-merges an id (#467) answers 200 — the row is read first."""
    eid = await _live(db, entity_type)
    await _tombstone(db, entity_type, eid, None)

    r = await _get(client, api_key, entity_type, eid)

    assert r.status_code == 200


@pytest.mark.integration
async def test_if_none_match_does_not_change_a_410(client, db, api_key):
    winner = await _live(db, "organization")
    loser = generate_id()
    await _tombstone(db, "organization", loser, winner)

    plain = await _get(client, api_key, "organization", loser)
    conditional = await _get(
        client, api_key, "organization", loser, **{"If-None-Match": f'"{loser}-0", *'}
    )

    assert conditional.status_code == plain.status_code == 410
    assert conditional.json() == plain.json()


# ---------------------------------------------------------------------------
# Contract tier — OpenAPI + source sweep (pure unit)
# ---------------------------------------------------------------------------


def test_detail_routes_declare_410_with_typed_body():
    schema = app.openapi()
    for path in sorted(_DETAIL_PATHS):
        gone = schema["paths"][path]["get"]["responses"].get("410")
        assert gone, f"GET {path} missing 410 in OpenAPI"
        ref = gone["content"]["application/json"]["schema"]["$ref"]
        assert ref.endswith("/EntityGone"), f"GET {path} 410 is not EntityGone: {ref}"


def _routes_calling_not_found_or_gone(tree: ast.AST) -> list[tuple[str, bool]]:
    """``(handler_name, declares_410)`` for each ``@router.get`` handler using the helper."""
    out = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.AsyncFunctionDef | ast.FunctionDef):
            continue
        gets = [
            d
            for d in node.decorator_list
            if isinstance(d, ast.Call)
            and isinstance(d.func, ast.Attribute)
            and d.func.attr == "get"
        ]
        if not gets:
            continue
        body = "\n".join(ast.unparse(stmt) for stmt in node.body)
        if "not_found_or_gone" not in body:
            continue
        declares = any(
            kw.arg == "responses" and ast.unparse(kw.value) == "DETAIL_RESPONSES"
            for d in gets
            for kw in d.keywords
        )
        out.append((node.name, declares))
    return out


def test_every_tombstone_aware_route_declares_410():
    """A route that can answer 410 must say so, or a generated client cannot type it."""
    found = [
        (path.name, name, declares)
        for path in sorted(PUBLIC_DIR.glob("*.py"))
        for name, declares in _routes_calling_not_found_or_gone(ast.parse(path.read_text()))
    ]
    undeclared = [f"{f}::{n}" for f, n, ok in found if not ok]
    assert not undeclared, f"410-capable GET without responses=DETAIL_RESPONSES: {undeclared}"
    # Pins the count, so a detail route that drops the helper is noticed too.
    assert len(found) == len(_DETAIL_PATHS), f"expected {len(_DETAIL_PATHS)}, found {found}"


def _types_passed_to_not_found_or_gone() -> set[str]:
    """The ``entity_type`` literal of every ``not_found_or_gone(db, "<type>", …)`` call."""
    found = set()
    for path in sorted(PUBLIC_DIR.glob("*.py")):
        for node in ast.walk(ast.parse(path.read_text())):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "not_found_or_gone"
            ):
                arg = node.args[1]
                assert isinstance(arg, ast.Constant), f"{path.name}: entity_type must be a literal"
                found.add(arg.value)
    return found


def test_every_walkable_type_has_a_tombstone_aware_detail_route():
    """``ENTITY_TABLE`` is the set of types a detail GET can be asked about.

    The declaration sweep above only sees routes that already call the helper; a
    type added to the walk without a route calling it — or a detail route left
    raising a bare 404 — would lose the read-path merge signal unnoticed.
    """
    assert _types_passed_to_not_found_or_gone() == set(ENTITY_TABLE)
