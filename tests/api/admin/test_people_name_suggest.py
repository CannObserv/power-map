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

import re

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
HTMX_HEADERS = {**AUTH_HEADERS, "HX-Request": "true"}


def _input_has_value(body: str, name: str, value: str) -> bool:
    """Match an `<input>` with the given `name` and `value` attributes,
    tolerant of attribute order and either single or double quotes."""
    pattern = re.compile(
        r"<input\b(?=[^>]*\bname=[\"']" + re.escape(name) + r"[\"'])"
        r"(?=[^>]*\bvalue=[\"']" + re.escape(value) + r"[\"'])"
        r"[^>]*>"
    )
    return bool(pattern.search(body))


def _has_any_input_value(body: str, name: str) -> bool:
    """Match any `<input name="..." value="...">` with a non-empty value."""
    pattern = re.compile(
        r"<input\b(?=[^>]*\bname=[\"']" + re.escape(name) + r"[\"'])"
        r"(?=[^>]*\bvalue=[\"'](?!['\"]).+?[\"'])"
        r"[^>]*>"
    )
    return bool(pattern.search(body))


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
async def seeded_person(db):
    """One person row with no names. Tests insert their own name row to
    exercise each (name, name_type, locale, script) shape."""
    pid = generate_id()
    await db.execute("INSERT INTO people (id) VALUES ($1)", pid)
    return pid


async def _insert_name(
    db,
    pid: str,
    *,
    name: str,
    name_type: str = "legal",
    locale: str | None = "en-US",
    script: str | None = "Latn",
    is_canonical: bool = True,
    reading_of_id: str | None = None,
) -> str:
    """Insert one person_names row and return its id."""
    nid = generate_id()
    await db.execute(
        "INSERT INTO person_names"
        " (id, person_id, name, name_type, is_canonical, locale, script,"
        " reading_of_id)"
        " VALUES ($1, $2, $3, $4, $5, $6, $7, $8)",
        nid,
        pid,
        name,
        name_type,
        is_canonical,
        locale,
        script,
        reading_of_id,
    )
    return nid


async def _insert_parts(
    db,
    name_id: str,
    *,
    given_names: list[str] | None = None,
    family_names: list[str] | None = None,
) -> None:
    await db.execute(
        "INSERT INTO person_name_parts"
        " (person_name_id, given_names, family_names)"
        " VALUES ($1, $2, $3)",
        name_id,
        given_names,
        family_names,
    )


# ---------------------------------------------------------------------------
# Auth + 404 guards
# ---------------------------------------------------------------------------


async def test_suggest_parts_requires_admin_auth(client, seeded_person, db):
    nid = await _insert_name(db, seeded_person, name="Ada Lovelace")
    r = await client.get(
        f"/admin/people/{seeded_person}/names/{nid}/suggest-parts/",
        follow_redirects=False,
    )
    assert r.status_code in (307, 401, 403), r.status_code


async def test_suggest_parts_404_when_name_missing(client, seeded_person):
    bogus = generate_id()
    r = await client.get(
        f"/admin/people/{seeded_person}/names/{bogus}/suggest-parts/",
        headers=HTMX_HEADERS,
    )
    assert r.status_code == 404


async def test_suggest_parts_404_when_wrong_person(client, seeded_person, db):
    nid = await _insert_name(db, seeded_person, name="Ada Lovelace")
    other = generate_id()
    r = await client.get(
        f"/admin/people/{other}/names/{nid}/suggest-parts/",
        headers=HTMX_HEADERS,
    )
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# Trivial: pre-fill given/family + primary_identifier
# ---------------------------------------------------------------------------


async def test_suggest_parts_trivial_two_token_prefills(client, seeded_person, db):
    nid = await _insert_name(db, seeded_person, name="Ada Lovelace")
    r = await client.get(
        f"/admin/people/{seeded_person}/names/{nid}/suggest-parts/",
        headers=HTMX_HEADERS,
    )
    assert r.status_code == 200, r.text
    body = r.text
    # Advisory carries the raw bucket via the data attribute (stable
    # contract), the operator-facing label "High confidence" in the
    # rendered text, and the corresponding success-badge styling class.
    assert 'data-suggest-advisory="trivial"' in body
    assert "High confidence" in body
    assert "badge--success" in body
    # Pre-filled values — match the <input> with both name and value,
    # tolerant of attribute order and quote style.
    assert _input_has_value(body, "given_names", "Ada")
    assert _input_has_value(body, "family_names", "Lovelace")
    # Primary identifier selected to family.
    assert re.search(r'<option\b[^>]*value=[\'"]family[\'"][^>]*selected', body)


async def test_suggest_parts_trivial_mononym_prefills_given_only(client, seeded_person, db):
    nid = await _insert_name(db, seeded_person, name="Madonna")
    r = await client.get(
        f"/admin/people/{seeded_person}/names/{nid}/suggest-parts/",
        headers=HTMX_HEADERS,
    )
    assert r.status_code == 200, r.text
    body = r.text
    assert _input_has_value(body, "given_names", "Madonna")
    # mononym tag should appear in advisory reasons.
    assert "mononym" in body.lower()


async def test_suggest_parts_trivial_particle_surname(client, seeded_person, db):
    """`van der` particle: nameparser glues the particle onto the surname."""
    nid = await _insert_name(db, seeded_person, name="Vincent van der Berg")
    r = await client.get(
        f"/admin/people/{seeded_person}/names/{nid}/suggest-parts/",
        headers=HTMX_HEADERS,
    )
    assert r.status_code == 200, r.text
    body = r.text
    assert _input_has_value(body, "given_names", "Vincent")
    # Particle is reflected in the family field.
    assert _input_has_value(body, "family_names", "van der Berg")
    # Reasons surfaces the particle tag.
    assert "particle" in body.lower()


async def test_suggest_parts_trivial_with_honorifics(client, seeded_person, db):
    nid = await _insert_name(db, seeded_person, name="Dr. John Smith Jr.")
    r = await client.get(
        f"/admin/people/{seeded_person}/names/{nid}/suggest-parts/",
        headers=HTMX_HEADERS,
    )
    assert r.status_code == 200, r.text
    body = r.text
    assert _input_has_value(body, "given_names", "John")
    assert _input_has_value(body, "family_names", "Smith")
    # Honorifics pre-fill the prefix/suffix inputs.
    assert _input_has_value(body, "honorific_prefix", "Dr.")
    assert _input_has_value(body, "honorific_suffix", "Jr.")


async def test_suggest_parts_trivial_comma_form(client, seeded_person, db):
    """`Last, First` comma-form: nameparser anchors the partition."""
    nid = await _insert_name(db, seeded_person, name="Smith, John")
    r = await client.get(
        f"/admin/people/{seeded_person}/names/{nid}/suggest-parts/",
        headers=HTMX_HEADERS,
    )
    assert r.status_code == 200, r.text
    body = r.text
    assert _input_has_value(body, "given_names", "John")
    assert _input_has_value(body, "family_names", "Smith")


# ---------------------------------------------------------------------------
# Ambiguous: advisory but no pre-fill
# ---------------------------------------------------------------------------


async def test_suggest_parts_ambiguous_multi_token_middle_skips_prefill(client, seeded_person, db):
    """Three non-initial middle tokens → ambiguous, no pre-fill."""
    nid = await _insert_name(db, seeded_person, name="Maria Elena Rodriguez Lopez Garcia")
    r = await client.get(
        f"/admin/people/{seeded_person}/names/{nid}/suggest-parts/",
        headers=HTMX_HEADERS,
    )
    assert r.status_code == 200, r.text
    body = r.text
    # Advisory bucket carried by data attribute; operator-facing label
    # is "Needs review" rendered as a warning-styled badge.
    assert 'data-suggest-advisory="ambiguous"' in body
    assert "Needs review" in body
    assert "badge--warning" in body
    # No pre-filled inputs for given/family.
    assert not _has_any_input_value(body, "given_names")
    assert not _has_any_input_value(body, "family_names")


# ---------------------------------------------------------------------------
# Skip buckets: empty / NULL script / non-decomposable name_type
# ---------------------------------------------------------------------------


async def test_suggest_parts_empty_name_short_circuits(client, seeded_person, db):
    """Whitespace-only name returns the partial with an advisory and no
    pre-fill — does NOT call into suggest_parts."""
    # An effectively-empty name is harder to seed past CHECK constraints;
    # the DB allows any non-NULL string. Use a single space.
    nid = await _insert_name(db, seeded_person, name=" ")
    r = await client.get(
        f"/admin/people/{seeded_person}/names/{nid}/suggest-parts/",
        headers=HTMX_HEADERS,
    )
    assert r.status_code == 200, r.text
    body = r.text
    # Advisory mentions setting the name first.
    assert "name" in body.lower()
    # No pre-fill: no given-names input has a value.
    assert not _has_any_input_value(body, "given_names")


async def test_suggest_parts_null_script_falls_back_to_advisory(client, seeded_person, db):
    """script IS NULL → advisory line surfaces the data-quality signal, no pre-fill."""
    nid = await _insert_name(db, seeded_person, name="Ada Lovelace", script=None)
    r = await client.get(
        f"/admin/people/{seeded_person}/names/{nid}/suggest-parts/",
        headers=HTMX_HEADERS,
    )
    assert r.status_code == 200, r.text
    body = r.text
    # Advisory present.
    assert "script" in body.lower()
    # No pre-fill arrays.
    assert not _has_any_input_value(body, "given_names")


async def test_suggest_parts_non_decomposable_name_type_advisory(client, seeded_person, db):
    """name_type='initials' returns confidence=skip with an advisory."""
    nid = await _insert_name(db, seeded_person, name="JFK", name_type="initials")
    r = await client.get(
        f"/admin/people/{seeded_person}/names/{nid}/suggest-parts/",
        headers=HTMX_HEADERS,
    )
    assert r.status_code == 200, r.text
    body = r.text
    # Skip bucket → "Cannot decompose" label rendered as an
    # inactive-styled badge; the suggester's advisory text mentions the
    # name_type that triggered the skip.
    assert 'data-suggest-advisory="skip"' in body
    assert "Cannot decompose" in body
    assert "badge--inactive" in body
    assert "initials" in body.lower()
    assert not _has_any_input_value(body, "given_names")


# ---------------------------------------------------------------------------
# Confirm-before-overwrite: existing parts triggers a confirmation state
# ---------------------------------------------------------------------------


async def test_suggest_parts_with_existing_parts_returns_confirm_state(client, seeded_person, db):
    nid = await _insert_name(db, seeded_person, name="Ada Lovelace")
    await _insert_parts(db, nid, given_names=["Augusta"], family_names=["King"])
    r = await client.get(
        f"/admin/people/{seeded_person}/names/{nid}/suggest-parts/",
        headers=HTMX_HEADERS,
    )
    assert r.status_code == 200, r.text
    body = r.text
    # Confirmation copy.
    assert "replace" in body.lower()
    # Replace button hits the same endpoint with a confirm flag.
    assert "confirm=1" in body or "confirm=true" in body
    # No suggestion-derived values pre-filled — existing parts inputs
    # render their current values (Augusta/King), but the suggestion's
    # decomposition (Ada/Lovelace) must NOT appear until Replace is
    # clicked.
    assert not _input_has_value(body, "given_names", "Ada")
    assert not _input_has_value(body, "family_names", "Lovelace")


async def test_suggest_parts_with_existing_parts_confirm_prefills(client, seeded_person, db):
    """`?confirm=1` bypasses the gate and returns the pre-filled partial."""
    nid = await _insert_name(db, seeded_person, name="Ada Lovelace")
    await _insert_parts(db, nid, given_names=["Augusta"], family_names=["King"])
    r = await client.get(
        f"/admin/people/{seeded_person}/names/{nid}/suggest-parts/?confirm=1",
        headers=HTMX_HEADERS,
    )
    assert r.status_code == 200, r.text
    body = r.text
    # Now the suggestion's values appear.
    assert _input_has_value(body, "given_names", "Ada")
    assert _input_has_value(body, "family_names", "Lovelace")
    # Confirmation copy no longer appears.
    assert "Replace existing decomposition" not in body


@pytest.mark.parametrize("confirm_value", ["1", "true", "yes", "Y", "T", "On"])
async def test_suggest_parts_confirm_accepts_truthy_variants(
    client, seeded_person, db, confirm_value
):
    """`?confirm=` accepts every truthy-like variant via the central
    `is_truthy_like` helper — case-insensitive, accepts single-letter
    forms."""
    nid = await _insert_name(db, seeded_person, name="Ada Lovelace")
    await _insert_parts(db, nid, given_names=["Augusta"], family_names=["King"])
    r = await client.get(
        f"/admin/people/{seeded_person}/names/{nid}/suggest-parts/?confirm={confirm_value}",
        headers=HTMX_HEADERS,
    )
    assert r.status_code == 200, r.text
    body = r.text
    # Truthy confirm bypasses the gate → suggestion pre-fills.
    assert _input_has_value(body, "given_names", "Ada")
    assert "Replace existing decomposition" not in body


# ---------------------------------------------------------------------------
# Narrow parts-editor endpoint — Keep current button uses this to swap
# only the parts editor `<details>` back, preserving in-flight edits
# in the surrounding row inputs.
# ---------------------------------------------------------------------------


async def test_parts_editor_endpoint_returns_original_editor(client, seeded_person, db):
    """GET /parts-editor/ returns the un-suggested editor with existing parts."""
    nid = await _insert_name(db, seeded_person, name="Ada Lovelace")
    await _insert_parts(db, nid, given_names=["Augusta"], family_names=["King"])
    r = await client.get(
        f"/admin/people/{seeded_person}/names/{nid}/parts-editor/",
        headers=HTMX_HEADERS,
    )
    assert r.status_code == 200, r.text
    body = r.text
    # Existing parts render.
    assert _input_has_value(body, "given_names", "Augusta")
    assert _input_has_value(body, "family_names", "King")
    # No advisory / confirm copy — this is the plain editor.
    assert "Suggested decomposition" not in body
    assert "Replace existing decomposition" not in body
    # Suggest button is back — this is the original editor render.
    assert "Suggest decomposition" in body


async def test_parts_editor_endpoint_404_when_name_missing(client, seeded_person):
    bogus = generate_id()
    r = await client.get(
        f"/admin/people/{seeded_person}/names/{bogus}/parts-editor/",
        headers=HTMX_HEADERS,
    )
    assert r.status_code == 404


async def test_parts_editor_endpoint_requires_admin_auth(client, seeded_person, db):
    nid = await _insert_name(db, seeded_person, name="Ada Lovelace")
    r = await client.get(
        f"/admin/people/{seeded_person}/names/{nid}/parts-editor/",
        follow_redirects=False,
    )
    assert r.status_code in (307, 401, 403), r.status_code


# ---------------------------------------------------------------------------
# reading_of_name populates the typeahead display input on both swap paths
# (CR Round 2 #8) — without the LEFT JOIN, the visible typeahead input
# would render blank even when the row has reading_of_id set.
# ---------------------------------------------------------------------------


async def test_suggest_parts_populates_reading_of_name_for_reading_rows(client, seeded_person, db):
    """A reading row's `reading_of_name` (typeahead display value) must
    survive the Suggest swap so the operator sees what they pointed at."""
    target = await _insert_name(
        db,
        seeded_person,
        name="山田 太郎",
        name_type="legal",
        locale="ja-JP",
        script="Jpan",
        is_canonical=True,
    )
    reading = await _insert_name(
        db,
        seeded_person,
        name="やまだ たろう",
        name_type="reading",
        locale="ja-JP",
        script="Hira",
        is_canonical=False,
        reading_of_id=target,
    )
    r = await client.get(
        f"/admin/people/{seeded_person}/names/{reading}/suggest-parts/",
        headers=HTMX_HEADERS,
    )
    assert r.status_code == 200, r.text
    # Target's distinctive visible name reaches the swapped partial
    # (substring is uniquely identifying — the reading row's own value
    # is `やまだ たろう`, not `山田 太郎`).
    assert "山田 太郎" in r.text


async def test_parts_editor_endpoint_populates_reading_of_name(client, seeded_person, db):
    """`/parts-editor/` must populate `reading_of_name` so Keep current
    swaps don't blank out the typeahead display input."""
    target = await _insert_name(
        db,
        seeded_person,
        name="山田 太郎",
        name_type="legal",
        locale="ja-JP",
        script="Jpan",
        is_canonical=True,
    )
    reading = await _insert_name(
        db,
        seeded_person,
        name="やまだ たろう",
        name_type="reading",
        locale="ja-JP",
        script="Hira",
        is_canonical=False,
        reading_of_id=target,
    )
    r = await client.get(
        f"/admin/people/{seeded_person}/names/{reading}/parts-editor/",
        headers=HTMX_HEADERS,
    )
    assert r.status_code == 200, r.text
    # The target's name must appear in the rendered partial (in the
    # reading-of typeahead display input). Substring check is adequate
    # — the partial's other inputs render `value="<reading.name>"` =
    # `やまだ たろう`, distinct from `山田 太郎`.
    assert "山田 太郎" in r.text
