"""Integration tests for the suggest-parts endpoint (Issue #139).

Endpoint: GET /admin/people/{person_id}/names/{name_id}/suggest-parts/

Suggest-only contract — verifies the HTML partial returned for every
confidence bucket (trivial, ambiguous, skip) plus the UX guards:
- 404 on missing row / wrong person_id
- Auth required
- Empty/whitespace name short-circuits
- NULL script falls back to advisory-only (no pre-fill)
- Existing parts triggers confirm-before-overwrite intermediate state
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
HTMX_HEADERS = {**AUTH_HEADERS, "HX-Request": "true"}


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


def _dsn():
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        pytest.skip("DATABASE_URL not set")
    return dsn


@pytest.fixture
def seeded_person():
    """One person row with no names. Tests insert their own name row to
    exercise each (name, name_type, locale, script) shape."""
    dsn = _dsn()
    pid = generate_id()

    async def setup():
        conn = await asyncpg.connect(dsn)
        await apply_schema(conn)
        try:
            await conn.execute("INSERT INTO people (id) VALUES ($1)", pid)
        finally:
            await conn.close()

    async def teardown():
        conn = await asyncpg.connect(dsn)
        try:
            await conn.execute(
                "DELETE FROM person_name_parts WHERE person_name_id IN ("
                " SELECT id FROM person_names WHERE person_id=$1)",
                pid,
            )
            await conn.execute("DELETE FROM person_names WHERE person_id=$1", pid)
            await conn.execute("DELETE FROM people WHERE id=$1", pid)
        finally:
            await conn.close()

    asyncio.run(setup())
    yield pid
    asyncio.run(teardown())


async def _insert_name(
    pid: str,
    *,
    name: str,
    name_type: str = "legal",
    locale: str | None = "en-US",
    script: str | None = "Latn",
) -> str:
    """Insert one person_names row and return its id."""
    conn = await asyncpg.connect(_dsn())
    try:
        nid = generate_id()
        await conn.execute(
            "INSERT INTO person_names"
            " (id, person_id, name, name_type, is_canonical, locale, script)"
            " VALUES ($1, $2, $3, $4, TRUE, $5, $6)",
            nid, pid, name, name_type, locale, script,
        )
        return nid
    finally:
        await conn.close()


async def _insert_parts(
    name_id: str,
    *,
    given_names: list[str] | None = None,
    family_names: list[str] | None = None,
) -> None:
    conn = await asyncpg.connect(_dsn())
    try:
        await conn.execute(
            "INSERT INTO person_name_parts"
            " (person_name_id, given_names, family_names)"
            " VALUES ($1, $2, $3)",
            name_id, given_names, family_names,
        )
    finally:
        await conn.close()


# ---------------------------------------------------------------------------
# Auth + 404 guards
# ---------------------------------------------------------------------------


def test_suggest_parts_requires_admin_auth(client, seeded_person):
    nid = asyncio.run(_insert_name(seeded_person, name="Ada Lovelace"))
    r = client.get(
        f"/admin/people/{seeded_person}/names/{nid}/suggest-parts/",
        follow_redirects=False,
    )
    assert r.status_code in (307, 401, 403), r.status_code


def test_suggest_parts_404_when_name_missing(client, seeded_person):
    bogus = generate_id()
    r = client.get(
        f"/admin/people/{seeded_person}/names/{bogus}/suggest-parts/",
        headers=HTMX_HEADERS,
    )
    assert r.status_code == 404


def test_suggest_parts_404_when_wrong_person(client, seeded_person):
    nid = asyncio.run(_insert_name(seeded_person, name="Ada Lovelace"))
    other = generate_id()
    r = client.get(
        f"/admin/people/{other}/names/{nid}/suggest-parts/",
        headers=HTMX_HEADERS,
    )
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# Trivial: pre-fill given/family + primary_identifier
# ---------------------------------------------------------------------------


def test_suggest_parts_trivial_two_token_prefills(client, seeded_person):
    nid = asyncio.run(_insert_name(seeded_person, name="Ada Lovelace"))
    r = client.get(
        f"/admin/people/{seeded_person}/names/{nid}/suggest-parts/",
        headers=HTMX_HEADERS,
    )
    assert r.status_code == 200, r.text
    body = r.text
    # Advisory line surfaces confidence.
    assert "trivial" in body.lower()
    # Pre-filled values.
    assert 'value="Ada"' in body
    assert 'value="Lovelace"' in body
    # Primary identifier selected to family.
    assert ('value="family" selected' in body) or ('selected>family' in body)


def test_suggest_parts_trivial_mononym_prefills_given_only(client, seeded_person):
    nid = asyncio.run(_insert_name(seeded_person, name="Madonna"))
    r = client.get(
        f"/admin/people/{seeded_person}/names/{nid}/suggest-parts/",
        headers=HTMX_HEADERS,
    )
    assert r.status_code == 200, r.text
    body = r.text
    assert 'value="Madonna"' in body
    # mononym tag should appear in advisory reasons.
    assert "mononym" in body.lower()


def test_suggest_parts_trivial_particle_surname(client, seeded_person):
    """`van der` particle: nameparser glues the particle onto the surname."""
    nid = asyncio.run(_insert_name(seeded_person, name="Vincent van der Berg"))
    r = client.get(
        f"/admin/people/{seeded_person}/names/{nid}/suggest-parts/",
        headers=HTMX_HEADERS,
    )
    assert r.status_code == 200, r.text
    body = r.text
    assert 'value="Vincent"' in body
    # Particle is reflected in the family field.
    assert "van der Berg" in body
    # Reasons surfaces the particle tag.
    assert "particle" in body.lower()


def test_suggest_parts_trivial_with_honorifics(client, seeded_person):
    nid = asyncio.run(_insert_name(seeded_person, name="Dr. John Smith Jr."))
    r = client.get(
        f"/admin/people/{seeded_person}/names/{nid}/suggest-parts/",
        headers=HTMX_HEADERS,
    )
    assert r.status_code == 200, r.text
    body = r.text
    assert 'value="John"' in body
    assert 'value="Smith"' in body
    # Honorifics pre-fill the prefix/suffix inputs.
    assert 'value="Dr."' in body
    assert 'value="Jr."' in body


def test_suggest_parts_trivial_comma_form(client, seeded_person):
    """`Last, First` comma-form: nameparser anchors the partition."""
    nid = asyncio.run(_insert_name(seeded_person, name="Smith, John"))
    r = client.get(
        f"/admin/people/{seeded_person}/names/{nid}/suggest-parts/",
        headers=HTMX_HEADERS,
    )
    assert r.status_code == 200, r.text
    body = r.text
    assert 'value="John"' in body
    assert 'value="Smith"' in body


# ---------------------------------------------------------------------------
# Ambiguous: advisory but no pre-fill
# ---------------------------------------------------------------------------


def test_suggest_parts_ambiguous_multi_token_middle_skips_prefill(
    client, seeded_person,
):
    """Three non-initial middle tokens → ambiguous, no pre-fill."""
    nid = asyncio.run(
        _insert_name(seeded_person, name="Maria Elena Rodriguez Lopez Garcia"),
    )
    r = client.get(
        f"/admin/people/{seeded_person}/names/{nid}/suggest-parts/",
        headers=HTMX_HEADERS,
    )
    assert r.status_code == 200, r.text
    body = r.text
    assert "ambiguous" in body.lower()
    # No pre-filled value attributes for given/family/additional inputs.
    # (Honorific fields default to empty too.)
    assert 'name="given_names" value=' not in body
    assert 'name="family_names" value=' not in body


# ---------------------------------------------------------------------------
# Skip buckets: empty / NULL script / non-decomposable name_type
# ---------------------------------------------------------------------------


def test_suggest_parts_empty_name_short_circuits(client, seeded_person):
    """Whitespace-only name returns the partial with an advisory and no
    pre-fill — does NOT call into suggest_parts."""
    # An effectively-empty name is harder to seed past CHECK constraints;
    # the DB allows any non-NULL string. Use a single space.
    nid = asyncio.run(_insert_name(seeded_person, name=" "))
    r = client.get(
        f"/admin/people/{seeded_person}/names/{nid}/suggest-parts/",
        headers=HTMX_HEADERS,
    )
    assert r.status_code == 200, r.text
    body = r.text
    # Advisory mentions setting the name first.
    assert "name" in body.lower()
    # No pre-fill: no given/family value attributes.
    assert 'name="given_names" value=' not in body


def test_suggest_parts_null_script_falls_back_to_advisory(client, seeded_person):
    """script IS NULL → advisory line surfaces the data-quality signal, no pre-fill."""
    nid = asyncio.run(
        _insert_name(seeded_person, name="Ada Lovelace", script=None),
    )
    r = client.get(
        f"/admin/people/{seeded_person}/names/{nid}/suggest-parts/",
        headers=HTMX_HEADERS,
    )
    assert r.status_code == 200, r.text
    body = r.text
    # Advisory present.
    assert "script" in body.lower()
    # No pre-fill arrays.
    assert 'name="given_names" value=' not in body


def test_suggest_parts_non_decomposable_name_type_advisory(client, seeded_person):
    """name_type='initials' returns confidence=skip with an advisory."""
    nid = asyncio.run(
        _insert_name(seeded_person, name="JFK", name_type="initials"),
    )
    r = client.get(
        f"/admin/people/{seeded_person}/names/{nid}/suggest-parts/",
        headers=HTMX_HEADERS,
    )
    assert r.status_code == 200, r.text
    body = r.text
    assert "skip" in body.lower() or "initials" in body.lower()
    assert 'name="given_names" value=' not in body


# ---------------------------------------------------------------------------
# Confirm-before-overwrite: existing parts triggers a confirmation state
# ---------------------------------------------------------------------------


def test_suggest_parts_with_existing_parts_returns_confirm_state(
    client, seeded_person,
):
    nid = asyncio.run(_insert_name(seeded_person, name="Ada Lovelace"))
    asyncio.run(_insert_parts(nid, given_names=["Augusta"], family_names=["King"]))
    r = client.get(
        f"/admin/people/{seeded_person}/names/{nid}/suggest-parts/",
        headers=HTMX_HEADERS,
    )
    assert r.status_code == 200, r.text
    body = r.text
    # Confirmation copy.
    assert "replace" in body.lower()
    # Replace button hits the same endpoint with a confirm flag.
    assert "confirm=1" in body or "confirm=true" in body
    # No pre-filled value attributes from the suggestion yet — the user
    # has to click Replace first.
    assert 'value="Ada"' not in body
    assert 'value="Lovelace"' not in body


def test_suggest_parts_with_existing_parts_confirm_prefills(client, seeded_person):
    """`?confirm=1` bypasses the gate and returns the pre-filled partial."""
    nid = asyncio.run(_insert_name(seeded_person, name="Ada Lovelace"))
    asyncio.run(_insert_parts(nid, given_names=["Augusta"], family_names=["King"]))
    r = client.get(
        f"/admin/people/{seeded_person}/names/{nid}/suggest-parts/?confirm=1",
        headers=HTMX_HEADERS,
    )
    assert r.status_code == 200, r.text
    body = r.text
    # Now the suggestion's values appear.
    assert 'value="Ada"' in body
    assert 'value="Lovelace"' in body
    # Confirmation copy no longer appears.
    assert "Replace existing decomposition" not in body
