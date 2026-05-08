"""Tests for the phase-2 analyser script (issue #135).

Read-only: feeds every `person_names` row through `suggest_parts` and
writes a triage CSV. The CSV is the input to a follow-up migration that
upserts confirmed parts.
"""

import asyncio
import csv
import io
import os
from pathlib import Path

import asyncpg
import pytest

from scripts.analyse_person_name_parts import (
    CSV_COLUMNS,
    _format_csv_row,
    run_analysis,
)
from src.core.db import apply_schema, generate_id
from src.core.normalizers.person_name import suggest_parts

# ---- Unit: row formatter ---------------------------------------------------


def _make_db_row(
    *,
    id_: str = "n1",
    person_id: str = "p1",
    name: str = "Jane Doe",
    name_type: str = "legal",
    locale: str | None = "en-US",
    script: str | None = "Latn",
    visibility: str = "public",
) -> dict:
    return {
        "id": id_,
        "person_id": person_id,
        "name": name,
        "name_type": name_type,
        "locale": locale,
        "script": script,
        "visibility": visibility,
    }


def test_format_csv_row_trivial_two_token():
    row = _make_db_row(name="Jane Doe")
    sug = suggest_parts(row["name"], locale="en-US", script="Latn", name_type="legal")
    out = _format_csv_row(row, sug)
    assert out["id"] == "n1"
    assert out["name"] == "Jane Doe"
    assert out["confidence"] == "trivial"
    assert out["given_names"] == "Jane"
    assert out["family_names"] == "Doe"
    assert out["additional_names"] == ""
    assert out["primary_identifier"] == "family"
    assert out["honorific_prefix"] == ""
    assert out["honorific_suffix"] == ""


def test_format_csv_row_arrays_are_pipe_joined():
    """Multi-element arrays use `|` so the CSV column itself splits cleanly."""
    row = _make_db_row(name="J. R. R. Tolkien")
    sug = suggest_parts(row["name"], locale="en-US", script="Latn", name_type="legal")
    out = _format_csv_row(row, sug)
    assert out["additional_names"] == "R.|R."
    assert out["confidence"] == "trivial"


def test_format_csv_row_skip_bucket_clears_part_columns():
    row = _make_db_row(name="Some MRZ", name_type="mrz")
    sug = suggest_parts(row["name"], locale="en-US", script="Latn", name_type="mrz")
    out = _format_csv_row(row, sug)
    assert out["confidence"] == "skip"
    assert out["given_names"] == ""
    assert out["family_names"] == ""
    assert "name_type=mrz" in out["reasons"]


def test_format_csv_row_includes_visibility_for_legal_context_review():
    row = _make_db_row(visibility="legal_only", name_type="deadname")
    sug = suggest_parts(row["name"], locale="en-US", script="Latn",
                        name_type="deadname")
    out = _format_csv_row(row, sug)
    # The CSV preserves visibility so operator can filter legal_only/hidden
    # rows for separate review (per issue #135 §"Edge cases").
    assert out["visibility"] == "legal_only"
    assert out["name_type"] == "deadname"


def test_csv_columns_includes_all_required_fields():
    """Columns are stable across runs — used as snapshot key."""
    required = {
        "id", "person_id", "name", "name_type", "locale", "script", "visibility",
        "confidence", "reasons",
        "honorific_prefix", "given_names", "additional_names",
        "family_names", "honorific_suffix", "primary_identifier",
    }
    assert required <= set(CSV_COLUMNS)


# ---- Integration: DB-backed analyser ---------------------------------------


pytestmark_int = pytest.mark.integration


def _dsn() -> str:
    dsn = os.environ.get("TEST_DATABASE_URL")
    if not dsn:
        pytest.skip("TEST_DATABASE_URL not set")
    return dsn


@pytest.fixture
async def db():
    conn = await asyncpg.connect(_dsn())
    try:
        await apply_schema(conn)
        tr = conn.transaction()
        await tr.start()
        try:
            yield conn
        finally:
            await tr.rollback()
    finally:
        await conn.close()


async def _seed(
    conn: asyncpg.Connection,
    *,
    name: str,
    name_type: str = "legal",
    visibility: str = "public",
    locale: str | None = "en-US",
    script: str | None = "Latn",
) -> str:
    """Seed a person + person_names row.

    Defaults to en-US/Latn (the post-Phase-1 state). The analyser is
    intended to run *after* the locale/script backfill, so that's the
    realistic scenario to test against.
    """
    pid = generate_id()
    nid = generate_id()
    # FK lookups: ensure the seed values are present.
    if locale and locale != "en-US":
        await conn.execute(
            "INSERT INTO bcp47_locales (code, language, script, region, display_name)"
            " VALUES ($1, 'xx', 'Latn', 'XX', $1)"
            " ON CONFLICT (code) DO NOTHING",
            locale,
        )
    if locale == "en-US":
        await conn.execute(
            "INSERT INTO bcp47_locales (code, language, script, region, display_name)"
            " VALUES ('en-US', 'en', 'Latn', 'US', 'English (US)')"
            " ON CONFLICT (code) DO NOTHING"
        )
    if script:
        await conn.execute(
            "INSERT INTO iso15924_scripts (code, numeric_code, name)"
            " VALUES ($1, 215, 'Latin') ON CONFLICT (code) DO NOTHING",
            script,
        )
    await conn.execute("INSERT INTO people (id) VALUES ($1)", pid)
    await conn.execute(
        "INSERT INTO person_names "
        "(id, person_id, name, name_type, visibility, locale, script)"
        " VALUES ($1, $2, $3, $4, $5, $6, $7)",
        nid, pid, name, name_type, visibility, locale, script,
    )
    return nid


@pytestmark_int
async def test_analysis_emits_one_csv_row_per_person_name(db, tmp_path: Path):
    nid_a = await _seed(db, name="Jane Doe")
    nid_b = await _seed(db, name="Hans van der Berg")
    # CJK row seeded with no locale/script — the analyser sees an empty
    # script and emits 'skip' with reason 'unsupported-script:'.
    nid_c = await _seed(db, name="毛澤東", locale=None, script=None)

    out_path = tmp_path / "analysis.csv"
    stats = await run_analysis(db, output_path=out_path)

    assert stats.rows_analysed == 3
    text = out_path.read_text()
    reader = csv.DictReader(io.StringIO(text))
    rows = {r["id"]: r for r in reader}
    assert set(rows.keys()) == {nid_a, nid_b, nid_c}
    assert rows[nid_a]["confidence"] == "trivial"
    assert rows[nid_b]["confidence"] == "trivial"
    assert rows[nid_c]["confidence"] == "skip"
    assert "particle" in rows[nid_b]["reasons"]


@pytestmark_int
async def test_analysis_includes_non_public_visibility_rows(db, tmp_path: Path):
    """Decomposition is *allowed* on legal_only / hidden rows but must be
    flagged for separate review — the analyser includes them in the CSV
    with visibility populated so the operator can filter."""
    nid = await _seed(db, name="Old Name", name_type="deadname",
                       visibility="legal_only")
    out_path = tmp_path / "analysis.csv"
    stats = await run_analysis(db, output_path=out_path)
    assert stats.rows_analysed == 1
    rows = list(csv.DictReader(io.StringIO(out_path.read_text())))
    assert rows[0]["id"] == nid
    assert rows[0]["visibility"] == "legal_only"
    assert rows[0]["name_type"] == "deadname"


@pytestmark_int
async def test_analysis_summary_buckets_by_confidence(db, tmp_path: Path):
    await _seed(db, name="Jane Doe")           # trivial
    await _seed(db, name="Mary Jane Watson Parker")  # ambiguous
    await _seed(db, name="毛澤東", locale=None, script=None)  # skip (no script)
    await _seed(db, name="Some MRZ", name_type="mrz")  # skip (name_type)
    out_path = tmp_path / "analysis.csv"
    stats = await run_analysis(db, output_path=out_path)
    assert stats.bucket_counts["trivial"] == 1
    assert stats.bucket_counts["ambiguous"] == 1
    assert stats.bucket_counts["skip"] == 2


asyncio  # silence unused import on collection-only runs
