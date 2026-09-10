"""The export step: PM's own tables → Parquet the models can read (#497).

`run` is unit-tested through an injected fetcher; the real `fetch_rows` is an
integration test against the rolled-back `db` connection. The script itself
is read-only — it writes only files — so it carries no `--execute`.
"""

import asyncpg
import pytest
import pytest_asyncio

pytest.importorskip("duckdb")

from scripts.export_pm_tables import TABLES, fetch_rows, main, run  # noqa: E402
from src.core.db import generate_id  # noqa: E402
from src.core.ingestion.mapping.parquet import read_rows  # noqa: E402


@pytest_asyncio.fixture(loop_scope="session")
async def db(db_pool):
    async with db_pool.acquire() as conn:
        tr = conn.transaction()
        await tr.start()
        try:
            yield conn
        finally:
            await tr.rollback()


def test_the_two_tables_the_models_read_are_exported():
    assert set(TABLES) == {"producer_crosswalk", "curation_overlay"}


async def test_run_writes_one_file_per_table_under_the_pm_dir(tmp_path):
    rows = {
        "producer_crosswalk": [
            ("01A", "usa-wa", "person", "P1", "PM1", "PM1", "live", None, None, None, None)
        ],
        "curation_overlay": [],
    }

    async def fake_fetch(spec):
        return rows[spec.name]

    report = await run(fake_fetch, root=tmp_path)

    assert report == {"producer_crosswalk": 1, "curation_overlay": 0}
    assert read_rows(tmp_path / "_pm" / "producer_crosswalk.parquet")[0][0] == "01A"
    assert (tmp_path / "_pm" / "curation_overlay.parquet").exists()


@pytest.mark.integration
async def test_fetch_rows_returns_tuples_in_spec_order(db):
    """The SELECT's column order is the Parquet's column order — pin it."""
    entity_id = generate_id()
    await db.execute(
        """INSERT INTO curation_overlay (id, entity_type, entity_id, field, value)
           VALUES ($1, 'person', $2, 'name', 'Curated')""",
        generate_id(),
        entity_id,
    )

    rows = await fetch_rows(db, TABLES["curation_overlay"])

    match = [r for r in rows if r[2] == entity_id]
    assert len(match) == 1
    assert len(match[0]) == len(TABLES["curation_overlay"].columns)
    assert match[0][1] == "person" and match[0][3] == "name" and match[0][4] == "Curated"


def test_a_missing_table_is_reported_as_a_sentence_not_a_traceback(monkeypatch, capsys):
    """Until schema.sql is applied to a target, curation_overlay is not there.

    That is a deploy state, not a bug in this script — say so and exit 1.
    """

    async def missing(dsn, root):
        raise asyncpg.UndefinedTableError('relation "curation_overlay" does not exist')

    monkeypatch.setenv("DATABASE_URL", "postgres://u:p@db.example/pm")
    monkeypatch.setattr("scripts.export_pm_tables._run_against", missing)

    code = main(["--root", "/nonexistent"])

    out = capsys.readouterr().out
    assert code == 1
    assert "curation_overlay" in out and "schema.sql" in out
    assert "Traceback" not in out
