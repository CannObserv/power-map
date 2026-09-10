"""The ownership manifest — the contract #499 reads (#497 step 6).

Every desired-state table declares its key, its retraction policy and the
columns the producer owns; the events table also declares the event types it
owns, which is what keeps retraction-as-absence away from the 315 org events
the producer has no column for.
"""

import pytest
import yaml

pytest.importorskip("dbt.adapters.duckdb")

from src.core.ingestion.mapping import (  # noqa: E402
    PROJECT_DIR,
    load_manifest,
    write_desired_state,
)
from src.core.ingestion.mapping.parquet import read_rows  # noqa: E402

MARTS = sorted(p.stem for p in (PROJECT_DIR / "models" / "marts").glob("*.sql"))


def _unique_columns(model: str) -> set[str]:
    """Columns of ``model`` carrying dbt's single-column `unique` test in schema.yml."""
    with (PROJECT_DIR / "models" / "schema.yml").open() as f:
        models = {m["name"]: m for m in yaml.safe_load(f)["models"]}
    unique = set()
    for col in models[model].get("columns", []):
        for test in col.get("tests", []):
            if test == "unique" or (isinstance(test, dict) and "unique" in test):
                unique.add(col["name"])
    return unique


def test_every_mart_is_declared_and_every_declaration_is_a_mart():
    manifest = load_manifest()

    assert sorted(manifest["tables"]) == MARTS


@pytest.mark.parametrize("table", MARTS)
def test_each_table_declares_key_retraction_and_owned_columns(table):
    spec = load_manifest()["tables"][table]

    assert spec["entity"] in {"person", "organization"}
    assert spec["key"] and all(isinstance(k, str) for k in spec["key"])
    assert spec["retraction"] in {"none", "report", "archive"}
    assert isinstance(spec["owned_columns"], list)


def test_events_own_exactly_dissolved():
    spec = load_manifest()["tables"]["desired_entity_events"]

    assert spec["owned_event_types"] == ["dissolved"]
    assert spec["owned_columns"] == ["event_year"]  # month/day: PM holds finer precision on 5


def test_events_are_keyed_by_producer_id_like_every_other_table():
    """CR 19: an org create has no pm_id, so a key naming it could not identify its event."""
    spec = load_manifest()["tables"]["desired_entity_events"]

    assert spec["key"] == ["producer_id", "event_type"]
    assert spec["pm_key"] == "pm_id"


@pytest.mark.parametrize("table", MARTS)
def test_no_key_names_pm_id(table):
    """`pm_id` is the row's *target*, resolved from the key — null for a create — never the key."""
    spec = load_manifest()["tables"][table]

    assert "pm_id" not in spec["key"]


def test_no_other_table_claims_event_types():
    tables = load_manifest()["tables"]

    assert [t for t, s in tables.items() if "owned_event_types" in s] == ["desired_entity_events"]


def test_write_desired_state_emits_one_parquet_per_declared_table(build, tmp_path):
    b = build()
    out = tmp_path / "desired_state"

    counts = write_desired_state(b.duckdb_path, out)

    assert sorted(counts) == MARTS
    assert sorted(p.stem for p in out.glob("*.parquet")) == MARTS
    assert counts["desired_entity_events"] == len(read_rows(out / "desired_entity_events.parquet"))
    assert list(out.glob(".incoming*")) == []


def test_write_desired_state_removes_parquet_the_manifest_no_longer_names(build, tmp_path):
    """CR 3: data/desired_state is #499's input; a ghost table reads as a live claim."""
    b = build()
    out = tmp_path / "desired_state"
    out.mkdir()
    ghost = out / "desired_ghost.parquet"
    ghost.write_bytes(b"not a table")
    unrelated = out / "README.txt"
    unrelated.write_text("mine")

    write_desired_state(b.duckdb_path, out)

    assert not ghost.exists()
    assert unrelated.exists()


@pytest.mark.parametrize("table", MARTS)
def test_the_projects_uniqueness_tests_match_the_declared_key(table):
    """CR 28: the manifest key is #499's contract; the tests must assert *that* key.

    A one-column key carries dbt's `unique`. A composite key carries a singular
    test on the tuple (tests/<table>_key_unique.sql) and no single-column
    `unique` on any of its columns — otherwise the day a second owned event
    type or name type lands, a legitimate row halts the build.
    """
    key = load_manifest()["tables"][table]["key"]
    unique = _unique_columns(table)

    if len(key) == 1:
        assert key[0] in unique, f"{table}: key {key} has no unique test"
    else:
        assert not (set(key) & unique), f"{table}: composite key {key} but unique on {unique}"
        assert (PROJECT_DIR / "tests" / f"{table}_key_unique.sql").exists(), table
