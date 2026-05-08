"""Tests for the phase-3 person_name_parts migration script (issue #135).

Reads the triaged CSV from `scripts/analyse_person_name_parts.py` and
upserts parts rows via `upsert_or_delete_parts`. Default behaviour:
process only ``confidence='trivial'`` rows; ambiguous / skip entries
are reported and left untouched.
"""

import asyncio
import csv
import os
from pathlib import Path

import asyncpg
import pytest

from scripts.migrate_person_name_parts import (
    _parse_csv_row,
    run_migration,
)
from src.core.db import apply_schema, generate_id

pytestmark = pytest.mark.integration


# ---- Unit: CSV row parsing -------------------------------------------------


def test_parse_csv_row_splits_pipe_joined_arrays():
    row = {
        "id": "n1", "person_id": "p1", "name": "J. R. R. Tolkien",
        "name_type": "legal", "locale": "en-US", "script": "Latn",
        "visibility": "public", "confidence": "trivial", "reasons": "",
        "honorific_prefix": "", "given_names": "J.",
        "additional_names": "R.|R.", "family_names": "Tolkien",
        "honorific_suffix": "", "primary_identifier": "family",
    }
    parsed = _parse_csv_row(row)
    assert parsed["name_id"] == "n1"
    assert parsed["given_names"] == ["J."]
    assert parsed["additional_names"] == ["R.", "R."]
    assert parsed["family_names"] == ["Tolkien"]
    assert parsed["honorific_prefix"] is None
    assert parsed["honorific_suffix"] is None
    assert parsed["primary_identifier"] == "family"


def test_parse_csv_row_empty_string_arrays_yield_empty_lists():
    row = {
        "id": "n1", "person_id": "p1", "name": "Cher", "name_type": "legal",
        "locale": "en-US", "script": "Latn", "visibility": "public",
        "confidence": "trivial", "reasons": "mononym",
        "honorific_prefix": "", "given_names": "Cher",
        "additional_names": "", "family_names": "",
        "honorific_suffix": "", "primary_identifier": "mononym",
    }
    parsed = _parse_csv_row(row)
    assert parsed["given_names"] == ["Cher"]
    assert parsed["family_names"] == []
    assert parsed["additional_names"] == []
    assert parsed["primary_identifier"] == "mononym"


def test_parse_csv_row_passes_honorifics_through():
    row = {
        "id": "n1", "person_id": "p1", "name": "Dr. Jane Doe Jr.",
        "name_type": "legal", "locale": "en-US", "script": "Latn",
        "visibility": "public", "confidence": "trivial", "reasons": "",
        "honorific_prefix": "Dr.", "given_names": "Jane",
        "additional_names": "", "family_names": "Doe",
        "honorific_suffix": "Jr.", "primary_identifier": "family",
    }
    parsed = _parse_csv_row(row)
    assert parsed["honorific_prefix"] == "Dr."
    assert parsed["honorific_suffix"] == "Jr."


# ---- Integration: DB-backed migration --------------------------------------


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
        await conn.execute(
            "INSERT INTO iso15924_scripts (code, numeric_code, name)"
            " VALUES ('Latn', 215, 'Latin') ON CONFLICT (code) DO NOTHING"
        )
        await conn.execute(
            "INSERT INTO bcp47_locales (code, language, script, region, display_name)"
            " VALUES ('en-US', 'en', 'Latn', 'US', 'English (US)')"
            " ON CONFLICT (code) DO NOTHING"
        )
        try:
            yield conn
        finally:
            await tr.rollback()
    finally:
        await conn.close()


async def _seed_name(
    conn: asyncpg.Connection,
    *,
    name: str = "Jane Doe",
    name_type: str = "legal",
) -> tuple[str, str]:
    pid = generate_id()
    nid = generate_id()
    await conn.execute("INSERT INTO people (id) VALUES ($1)", pid)
    await conn.execute(
        "INSERT INTO person_names "
        "(id, person_id, name, name_type, is_canonical, locale, script)"
        " VALUES ($1, $2, $3, $4, TRUE, 'en-US', 'Latn')",
        nid, pid, name, name_type,
    )
    return pid, nid


def _write_csv(tmp_path: Path, rows: list[dict]) -> Path:
    """Mirror the analyser's CSV format."""
    columns = (
        "id", "person_id", "name", "name_type", "locale", "script", "visibility",
        "confidence", "reasons",
        "honorific_prefix", "given_names", "additional_names",
        "family_names", "honorific_suffix", "primary_identifier",
    )
    out = tmp_path / "in.csv"
    with out.open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(columns))
        writer.writeheader()
        for r in rows:
            writer.writerow({c: r.get(c, "") for c in columns})
    return out


def _csv_row(nid: str, pid: str, **overrides) -> dict:
    base = {
        "id": nid, "person_id": pid, "name": "Jane Doe", "name_type": "legal",
        "locale": "en-US", "script": "Latn", "visibility": "public",
        "confidence": "trivial", "reasons": "",
        "honorific_prefix": "", "given_names": "Jane",
        "additional_names": "", "family_names": "Doe",
        "honorific_suffix": "", "primary_identifier": "family",
    }
    base.update(overrides)
    return base


async def _fetch_parts(conn: asyncpg.Connection, name_id: str) -> dict | None:
    row = await conn.fetchrow(
        "SELECT given_names, family_names, additional_names,"
        " honorific_prefix, honorific_suffix, primary_identifier"
        " FROM person_name_parts WHERE person_name_id=$1",
        name_id,
    )
    return dict(row) if row else None


# ---- Migration behaviour ---------------------------------------------------


async def test_migration_dry_run_does_not_write_parts(db, tmp_path):
    pid, nid = await _seed_name(db, name="Jane Doe")
    csv_path = _write_csv(tmp_path, [_csv_row(nid, pid)])
    stats = await run_migration(db, csv_path=csv_path, dry_run=True)
    assert stats.applied == 1
    assert stats.dry_run is True
    parts = await _fetch_parts(db, nid)
    assert parts is None  # rolled back


async def test_migration_execute_inserts_parts_row(db, tmp_path):
    pid, nid = await _seed_name(db, name="Jane Doe")
    csv_path = _write_csv(tmp_path, [_csv_row(nid, pid)])
    stats = await run_migration(db, csv_path=csv_path, dry_run=False)
    assert stats.applied == 1
    parts = await _fetch_parts(db, nid)
    assert parts is not None
    assert parts["given_names"] == ["Jane"]
    assert parts["family_names"] == ["Doe"]
    assert parts["primary_identifier"] == "family"


async def test_migration_skips_ambiguous_by_default(db, tmp_path):
    pid, nid = await _seed_name(db, name="Sean or Shawn Collins")
    csv_path = _write_csv(tmp_path, [
        _csv_row(nid, pid, confidence="ambiguous", given_names="Sean",
                 additional_names="or|Shawn", family_names="Collins"),
    ])
    stats = await run_migration(db, csv_path=csv_path, dry_run=False)
    assert stats.applied == 0
    assert stats.skipped_by_confidence["ambiguous"] == 1
    parts = await _fetch_parts(db, nid)
    assert parts is None


async def test_migration_skips_skip_bucket(db, tmp_path):
    pid, nid = await _seed_name(db, name="JFK", name_type="initials")
    csv_path = _write_csv(tmp_path, [
        _csv_row(nid, pid, name_type="initials", confidence="skip",
                 reasons="name_type=initials",
                 given_names="", family_names="", primary_identifier=""),
    ])
    stats = await run_migration(db, csv_path=csv_path, dry_run=False)
    assert stats.applied == 0
    assert stats.skipped_by_confidence["skip"] == 1
    parts = await _fetch_parts(db, nid)
    assert parts is None


async def test_migration_includes_ambiguous_when_filter_widens(db, tmp_path):
    """`--include-ambiguous` lets the operator commit reviewed ambiguous rows."""
    pid, nid = await _seed_name(db, name="Sean or Shawn Collins")
    csv_path = _write_csv(tmp_path, [
        _csv_row(nid, pid, confidence="ambiguous", given_names="Sean",
                 additional_names="or|Shawn", family_names="Collins"),
    ])
    stats = await run_migration(
        db, csv_path=csv_path, dry_run=False,
        confidence_filter={"trivial", "ambiguous"},
    )
    assert stats.applied == 1
    parts = await _fetch_parts(db, nid)
    assert parts is not None
    assert parts["additional_names"] == ["or", "Shawn"]


async def test_migration_idempotent_re_run_replaces_in_place(db, tmp_path):
    """upsert_or_delete_parts is ON CONFLICT DO UPDATE — re-running just
    re-writes the same row, no duplicates."""
    pid, nid = await _seed_name(db, name="Jane Doe")
    csv_path = _write_csv(tmp_path, [_csv_row(nid, pid)])
    await run_migration(db, csv_path=csv_path, dry_run=False)
    await run_migration(db, csv_path=csv_path, dry_run=False)
    n_parts = await db.fetchval(
        "SELECT count(*) FROM person_name_parts WHERE person_name_id=$1", nid,
    )
    assert n_parts == 1


async def test_migration_atomic_on_validation_error(db, tmp_path):
    """upsert_or_delete_parts validation errors abort the whole run."""
    pid_a, nid_a = await _seed_name(db, name="Alice")
    pid_b, nid_b = await _seed_name(db, name="Bob")
    csv_path = _write_csv(tmp_path, [
        _csv_row(nid_a, pid_a, given_names="Alice", family_names="A"),
        # primary_identifier='not-allowed' fails the CHECK validation in
        # upsert_or_delete_parts.
        _csv_row(nid_b, pid_b, primary_identifier="not-allowed"),
    ])
    with pytest.raises(ValueError, match="primary_identifier"):
        await run_migration(db, csv_path=csv_path, dry_run=False)
    assert await _fetch_parts(db, nid_a) is None  # rolled back


async def test_migration_counts_buckets_for_summary(db, tmp_path):
    pid_t, nid_t = await _seed_name(db, name="Trivial")
    pid_a, nid_a = await _seed_name(db, name="Ambig")
    pid_s, nid_s = await _seed_name(db, name="Skipme", name_type="initials")
    csv_path = _write_csv(tmp_path, [
        _csv_row(nid_t, pid_t, given_names="Trivial", family_names=""),
        _csv_row(nid_a, pid_a, confidence="ambiguous"),
        _csv_row(nid_s, pid_s, name_type="initials", confidence="skip",
                 given_names="", family_names="", primary_identifier=""),
    ])
    stats = await run_migration(db, csv_path=csv_path, dry_run=False)
    assert stats.applied == 1
    assert stats.skipped_by_confidence["ambiguous"] == 1
    assert stats.skipped_by_confidence["skip"] == 1


asyncio  # silence collection-only warning
