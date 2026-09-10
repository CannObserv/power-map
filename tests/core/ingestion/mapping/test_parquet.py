"""Writing PM tables to Parquet for the models to read (#497).

The models' contract is "presence means complete": a Parquet file either
carries the whole table or is not there. Timestamps must survive tz-aware, or
`updated_at` comparisons downstream silently shift by the VM's offset.
"""

from datetime import UTC, datetime

import pytest

pytest.importorskip("duckdb")

import duckdb  # noqa: E402

from src.core.ingestion.mapping.parquet import (  # noqa: E402
    TableSpec,
    read_records,
    read_rows,
    write_parquet,
)

SPEC = TableSpec(
    name="things",
    columns=("id", "label", "created_at"),
    types=("TEXT", "TEXT", "TIMESTAMPTZ"),
)
TS = datetime(2026, 9, 9, 4, 34, 2, 497392, tzinfo=UTC)


def test_rows_round_trip_with_tz_aware_timestamps(tmp_path):
    path = tmp_path / "things.parquet"

    n = write_parquet([("a", "one", TS), ("b", None, None)], SPEC, path)

    assert n == 2
    assert read_rows(path, order_by="id") == [("a", "one", TS), ("b", None, None)]


def test_an_empty_table_still_writes_a_file_with_its_schema(tmp_path):
    """`read_parquet` on a missing file fails; on an empty one it returns zero rows."""
    path = tmp_path / "things.parquet"

    assert write_parquet([], SPEC, path) == 0
    assert path.exists()
    cols = [c[0] for c in duckdb.sql(f"DESCRIBE SELECT * FROM read_parquet('{path}')").fetchall()]
    assert cols == list(SPEC.columns)


def test_writing_replaces_a_previous_file_atomically(tmp_path):
    path = tmp_path / "things.parquet"
    write_parquet([("old", None, None)], SPEC, path)

    write_parquet([("new", None, None)], SPEC, path)

    assert [r[0] for r in read_rows(path)] == ["new"]
    assert list(tmp_path.glob(".incoming*")) == []


def test_a_row_of_the_wrong_width_is_refused_before_anything_is_written(tmp_path):
    path = tmp_path / "things.parquet"

    with pytest.raises(ValueError, match="3 columns"):
        write_parquet([("a", "one")], SPEC, path)

    assert not path.exists()
    assert list(tmp_path.glob(".incoming*")) == []


def test_read_records_returns_dicts_keyed_by_column(tmp_path):
    """The applier reads by name (#499): a reordered SELECT must not swap two TEXT columns."""
    path = tmp_path / "things.parquet"
    write_parquet([("a", "one", TS), ("b", None, None)], SPEC, path)

    assert read_records(path, order_by="id") == [
        {"id": "a", "label": "one", "created_at": TS},
        {"id": "b", "label": None, "created_at": None},
    ]
