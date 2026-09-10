"""The dbt-duckdb mapping project's scaffold (#497).

Guarded like the browser and seed tiers: `dbt` is an opt-in group, and
`tests/optional_groups.py` announces its absence rather than letting the skip
pass as coverage.
"""

import pytest

pytest.importorskip("dbt.adapters.duckdb")

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
