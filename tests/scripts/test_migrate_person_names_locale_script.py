"""Tests for the phase-1 locale/script backfill script (issue #135).

Two layers:
- Unit: ``_classify_rows`` partitions rows into per-(locale, script) buckets
  and a skip list, with no DB involvement.
- Integration: ``run_backfill`` against a live DB confirms dry-run rolls
  back, --execute persists, and the FK pre-flight aborts when the lookup
  tables are unseeded.

Run unit tests with ``uv run pytest``; integration with
``uv run pytest -m integration``.
"""

import asyncpg
import pytest
import pytest_asyncio

from scripts.migrate_person_names_locale_script import (
    _classify_rows,
    run_backfill,
)
from src.core.db import generate_id

# ---- Unit: row classification (no DB) --------------------------------------


def _row(
    name: str, *, id_: str = "n1", locale: str | None = None, script: str | None = None
) -> dict:
    return {"id": id_, "name": name, "locale": locale, "script": script}


def test_classify_pure_ascii_row_buckets_into_en_us_latn():
    rows = [_row("Jane Doe", id_="a"), _row("John A. Doe", id_="b")]
    by_locale, by_script, skipped = _classify_rows(rows)
    assert by_locale == {"en-US": ["a", "b"]}
    assert by_script == {"Latn": ["a", "b"]}
    assert skipped == []


def test_classify_skips_non_ascii_name():
    """Names containing non-ASCII letters need human review — never default."""
    rows = [_row("毛澤東", id_="cjk"), _row("Иван Иванов", id_="cyr")]
    by_locale, by_script, skipped = _classify_rows(rows)
    assert by_locale == {}
    assert by_script == {}
    assert {sid for sid, _ in skipped} == {"cjk", "cyr"}


def test_classify_respects_already_set_locale():
    """A row with locale already set is not re-bucketed for locale UPDATE."""
    rows = [_row("Jane Doe", id_="a", locale="fr-FR")]
    by_locale, by_script, skipped = _classify_rows(rows)
    assert by_locale == {}
    assert by_script == {"Latn": ["a"]}


def test_classify_respects_already_set_script():
    rows = [_row("Jane Doe", id_="a", script="Latn")]
    by_locale, by_script, skipped = _classify_rows(rows)
    assert by_locale == {"en-US": ["a"]}
    assert by_script == {}


def test_classify_partitions_mixed_input():
    rows = [
        _row("Jane Doe", id_="ascii"),
        _row("毛澤東", id_="cjk"),
        _row("Pedro García", id_="diac"),  # Latin-with-diacritics
        _row("Bob Smith", id_="ascii2", script="Latn"),  # locale only
    ]
    by_locale, by_script, skipped = _classify_rows(rows)
    assert sorted(by_locale["en-US"]) == ["ascii", "ascii2"]
    # `diac` rows get script=Latn but locale stays NULL (escalated).
    assert sorted(by_script["Latn"]) == ["ascii", "diac"]
    assert {sid for sid, _ in skipped} == {"cjk"}


def test_classify_diacritics_get_script_but_skip_locale():
    """Latin-with-diacritics rows: script auto-set to Latn, locale escalated.

    `Pedro García` is unambiguously Latin script even with `í`; we backfill
    that. The locale (es vs. pt vs. fr) is a judgment call — leave NULL.
    """
    rows = [_row("Pedro García", id_="d")]
    by_locale, by_script, skipped = _classify_rows(rows)
    assert by_locale == {}
    assert by_script == {"Latn": ["d"]}
    assert skipped == []


def test_classify_locale_overrides_apply():
    """Per-row locale overrides win — used to set #135-triaged rows like
    João to pt-BR while leaving the surrounding diacritic rows escalated."""
    rows = [
        _row("João Castel-Branco Goulão", id_="joao"),
        _row("Pedro García", id_="pedro"),
    ]
    overrides = {"joao": "pt-BR"}
    by_locale, by_script, skipped = _classify_rows(rows, locale_overrides=overrides)
    assert by_locale == {"pt-BR": ["joao"]}
    assert sorted(by_script["Latn"]) == ["joao", "pedro"]
    assert skipped == []


def test_classify_locale_override_for_unknown_id_is_ignored():
    rows = [_row("Jane Doe", id_="a")]
    by_locale, by_script, _ = _classify_rows(
        rows,
        locale_overrides={"nonexistent": "fr-FR"},
    )
    assert by_locale == {"en-US": ["a"]}
    assert by_script == {"Latn": ["a"]}


def test_classify_locale_override_does_not_overwrite_already_set_locale():
    rows = [_row("João Goulão", id_="joao", locale="en-US")]  # already en-US
    by_locale, by_script, _ = _classify_rows(
        rows,
        locale_overrides={"joao": "pt-BR"},
    )
    # Already set — don't overwrite from the override map either.
    assert by_locale == {}
    assert by_script == {"Latn": ["joao"]}


# ---- Integration: real DB --------------------------------------------------


pytestmark_int = pytest.mark.integration


@pytest_asyncio.fixture(loop_scope="session")
async def db(db_pool):
    async with db_pool.acquire() as conn:
        # Seed minimal FK rows the backfill needs. Tests run inside a
        # transaction that gets rolled back, so this is per-test cleanup-free.
        tr = conn.transaction()
        await tr.start()
        try:
            await conn.execute(
                "INSERT INTO iso15924_scripts (code, numeric_code, name)"
                " VALUES ('Latn', 215, 'Latin') ON CONFLICT (code) DO NOTHING"
            )
            await conn.execute(
                "INSERT INTO bcp47_locales (code, language, script, region, display_name)"
                " VALUES ('en-US', 'en', 'Latn', 'US', 'English (US)')"
                " ON CONFLICT (code) DO NOTHING"
            )
            yield conn
        finally:
            await tr.rollback()


async def _seed_person_with_name(
    conn: asyncpg.Connection,
    *,
    name: str,
    locale: str | None = None,
    script: str | None = None,
) -> tuple[str, str]:
    pid = generate_id()
    nid = generate_id()
    await conn.execute(
        "INSERT INTO people (id) VALUES ($1)",
        pid,
    )
    await conn.execute(
        "INSERT INTO person_names (id, person_id, name, name_type, locale, script)"
        " VALUES ($1, $2, $3, 'legal', $4, $5)",
        nid,
        pid,
        name,
        locale,
        script,
    )
    return pid, nid


@pytestmark_int
async def test_dry_run_does_not_modify_db(db):
    _, nid = await _seed_person_with_name(db, name="Jane Doe")
    result = await run_backfill(db, dry_run=True)
    assert result.dry_run is True
    assert result.locale_updated >= 1
    assert result.script_updated >= 1
    row = await db.fetchrow(
        "SELECT locale, script FROM person_names WHERE id=$1",
        nid,
    )
    assert row["locale"] is None  # dry_run — seeded row not modified
    assert row["script"] is None


@pytestmark_int
async def test_execute_writes_locale_and_script(db):
    _, nid = await _seed_person_with_name(db, name="Jane Doe")
    result = await run_backfill(db, dry_run=False)
    assert result.dry_run is False
    assert result.locale_updated >= 1
    assert result.script_updated >= 1
    row = await db.fetchrow(
        "SELECT locale, script FROM person_names WHERE id=$1",
        nid,
    )
    assert row["locale"] == "en-US"
    assert row["script"] == "Latn"


@pytestmark_int
async def test_execute_skips_non_latin_rows_and_reports_them(db):
    _, nid_ok = await _seed_person_with_name(db, name="Jane Doe")
    _, nid_cjk = await _seed_person_with_name(db, name="毛澤東")
    result = await run_backfill(db, dry_run=False)
    assert result.locale_updated >= 1  # Jane Doe row at minimum
    assert result.script_updated >= 1
    assert any(sid == nid_cjk for sid, _ in result.skipped)
    ok_row = await db.fetchrow(
        "SELECT locale FROM person_names WHERE id=$1",
        nid_ok,
    )
    assert ok_row["locale"] == "en-US"  # ASCII row correctly processed
    cjk_row = await db.fetchrow(
        "SELECT locale, script FROM person_names WHERE id=$1",
        nid_cjk,
    )
    assert cjk_row["locale"] is None  # CJK row not touched
    assert cjk_row["script"] is None


@pytestmark_int
async def test_execute_sets_script_on_latin_diacritic_rows_but_not_locale(db):
    _, nid = await _seed_person_with_name(db, name="Pedro García")
    result = await run_backfill(db, dry_run=False)
    assert result.script_updated >= 1
    row = await db.fetchrow(
        "SELECT locale, script FROM person_names WHERE id=$1",
        nid,
    )
    assert row["locale"] is None  # escalated — not set automatically
    assert row["script"] == "Latn"


@pytestmark_int
async def test_execute_applies_locale_override_for_specific_row(db):
    """João's row gets locale='pt-BR' from the triage override map; the row
    next to it (also Latin-with-diacritics) only gets script='Latn'."""
    # Seed a 'pt-BR' lookup row so the FK passes.
    await db.execute(
        "INSERT INTO bcp47_locales (code, language, script, region, display_name)"
        " VALUES ('pt-BR', 'pt', 'Latn', 'BR', 'Portuguese (Brazil)')"
        " ON CONFLICT (code) DO NOTHING"
    )
    _, nid_joao = await _seed_person_with_name(db, name="João Goulão")
    _, nid_pedro = await _seed_person_with_name(db, name="Pedro García")
    result = await run_backfill(
        db,
        dry_run=False,
        locale_overrides={nid_joao: "pt-BR"},
    )
    assert result.locale_updated >= 1  # João's row at minimum
    assert result.script_updated >= 2  # João and Pedro at minimum
    joao = await db.fetchrow(
        "SELECT locale, script FROM person_names WHERE id=$1",
        nid_joao,
    )
    pedro = await db.fetchrow(
        "SELECT locale, script FROM person_names WHERE id=$1",
        nid_pedro,
    )
    assert joao["locale"] == "pt-BR"
    assert joao["script"] == "Latn"
    assert pedro["locale"] is None
    assert pedro["script"] == "Latn"


@pytestmark_int
async def test_execute_does_not_overwrite_already_set_locale(db):
    _, nid = await _seed_person_with_name(db, name="Jane Doe", locale="en-US")
    # locale is set, script is not — only script should be updated on this row.
    result = await run_backfill(db, dry_run=False)
    assert result.script_updated >= 1
    row = await db.fetchrow(
        "SELECT locale, script FROM person_names WHERE id=$1",
        nid,
    )
    assert row["locale"] == "en-US"  # pre-set locale unchanged
    assert row["script"] == "Latn"  # script was populated


@pytestmark_int
async def test_idempotent_second_run_is_a_no_op(db):
    await _seed_person_with_name(db, name="Jane Doe")
    first = await run_backfill(db, dry_run=False)
    second = await run_backfill(db, dry_run=False)
    assert first.locale_updated >= 1
    assert first.script_updated >= 1
    assert second.locale_updated == 0
    assert second.script_updated == 0


@pytestmark_int
async def test_preflight_aborts_when_lookup_tables_unseeded(db):
    """If 'en-US' is missing from bcp47_locales the backfill must refuse —
    otherwise the UPDATE would fail with a FK violation mid-batch."""
    await db.execute("DELETE FROM bcp47_locales WHERE code='en-US'")
    await _seed_person_with_name(db, name="Jane Doe")
    with pytest.raises(SystemExit, match="bcp47_locales|seed"):
        await run_backfill(db, dry_run=False)
