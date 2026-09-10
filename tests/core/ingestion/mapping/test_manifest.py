"""The ownership manifest — the contract #499 reads (#497 step 6).

Every desired-state table declares its key, its retraction policy and the
columns the producer owns; the events table also declares the event types it
owns, which is what keeps retraction-as-absence away from the 315 org events
the producer has no column for.
"""

import pytest

pytest.importorskip("dbt.adapters.duckdb")

from src.core.ingestion.mapping import (  # noqa: E402
    PROJECT_DIR,
    load_manifest,
    write_desired_state,
)
from src.core.ingestion.mapping.parquet import read_rows  # noqa: E402

MARTS = sorted(p.stem for p in (PROJECT_DIR / "models" / "marts").glob("*.sql"))


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
