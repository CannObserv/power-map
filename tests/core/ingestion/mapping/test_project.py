"""The dbt-duckdb mapping project's scaffold (#497).

Guarded like the browser and seed tiers: `dbt` is an opt-in group, and
`tests/optional_groups.py` announces its absence rather than letting the skip
pass as coverage.
"""

import pytest

pytest.importorskip("dbt.adapters.duckdb")

import duckdb  # noqa: E402

from src.core.ingestion.mapping import (  # noqa: E402
    PM_SOURCES,
    PROJECT_DIR,
    USA_WA_SOURCES,
    run_dbt,
)


def test_the_project_parses_with_no_snapshot_present(tmp_path):
    """`dbt parse` needs the project and profile, not the data."""
    result = run_dbt(["parse"], snapshot_root=tmp_path, duckdb_path=":memory:")

    assert result.success, result.exception
    assert (PROJECT_DIR / "dbt_project.yml").exists()


def test_every_registered_source_is_declared_and_vice_versa(tmp_path):
    """`USA_WA_SOURCES` / `PM_SOURCES` and models/sources.yml must name the same tables.

    A source declared in YAML but absent from the registry never gets its env
    var set, so it renders as '' and every model reading it fails at run time.
    """
    result = run_dbt(["parse"], snapshot_root=tmp_path, duckdb_path=":memory:")
    assert result.success, result.exception

    manifest = result.result
    declared = {(s.source_name, s.name) for s in manifest.sources.values()}
    registered = {("usa_wa", n) for n in USA_WA_SOURCES} | {("pm", n) for n in PM_SOURCES}

    assert declared == registered


def test_a_built_file_is_openable_read_only_the_moment_run_dbt_returns(tmp_path):
    """dbt's adapter holds the file open in-process; run_dbt must let go.

    duckdb refuses a second connection to the same file with a different
    configuration, so without this the export step and every assertion
    against a built model failed with "Can't open a connection to same
    database file".
    """
    db = tmp_path / "m.duckdb"
    result = run_dbt(["parse"], snapshot_root=tmp_path, duckdb_path=str(db))
    assert result.success

    duckdb.connect(str(db)).close()  # create it, as a build would
    result = run_dbt(["parse"], snapshot_root=tmp_path, duckdb_path=str(db))
    assert result.success

    con = duckdb.connect(str(db), read_only=True)
    con.close()


def test_dbt_scratch_lives_beside_the_duckdb_file_not_in_the_store(tmp_path):
    """CR 4: the store is a verbatim mirror of upstream; dbt's target/logs are not part of it."""
    store = tmp_path / "store"
    store.mkdir()
    db = tmp_path / "work" / "mapping.duckdb"

    result = run_dbt(["parse"], snapshot_root=store, duckdb_path=str(db))

    assert result.success
    assert (db.parent / ".dbt-target").is_dir()
    assert not (store / ".dbt-target").exists()
