"""Parquet files as the models' input format (#497).

Two contracts the models lean on:

* **Presence means complete.** A file is written whole into a staging directory
  and moved into place, so a reader never sees a partial table — the same rule
  the snapshot store applies to a landed version (#496).
* **Timestamps stay tz-aware.** Written as ``TIMESTAMPTZ`` and read back in
  UTC, so an `updated_at` compared downstream never shifts by the VM's offset.

Lives here rather than in `scripts/` because writing the models' inputs is a
mapping-layer concern — #499 will read the same files — and because the
staging ``INSERT`` is duckdb's, not Postgres's, which the `scripts/` write-SQL
sweep cannot tell apart.
"""

import os
import re
import shutil
import tempfile
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

import duckdb

__all__ = ["PM_EXPORT_DIR", "TableSpec", "export_table", "read_rows", "write_parquet"]

# Where the export step puts PM's own tables, under the snapshot root.
PM_EXPORT_DIR = "_pm"

_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")


@dataclass(frozen=True)
class TableSpec:
    """A table's name and its columns in Parquet order, with duckdb types."""

    name: str
    columns: tuple[str, ...]
    types: tuple[str, ...]

    def __post_init__(self) -> None:
        if len(self.columns) != len(self.types):
            raise ValueError(
                f"{self.name}: {len(self.columns)} columns but {len(self.types)} types"
            )
        for ident in (self.name, *self.columns):
            if not _IDENTIFIER.match(ident):
                raise ValueError(f"{self.name}: {ident!r} is not a plain identifier")


def _sql_path(path: Path) -> str:
    return str(path).replace("'", "''")


def write_parquet(rows: Iterable[Sequence], spec: TableSpec, path: Path | str) -> int:
    """Write ``rows`` (tuples in ``spec.columns`` order) to ``path``; return the count.

    Every row is width-checked before anything touches disk, and the file is
    staged then ``os.replace``d, so a bad row or a crash leaves the previous
    file — or no file — never a half-written one. An empty ``rows`` still
    writes a file carrying the schema: ``read_parquet`` on a missing file is an
    error, on an empty one it is zero rows, and only the second is a state the
    models should ever see.
    """
    path = Path(path)
    width = len(spec.columns)
    staged_rows = [tuple(r) for r in rows]
    for r in staged_rows:
        if len(r) != width:
            raise ValueError(f"{spec.name}: expected {width} columns, got {len(r)}: {r!r}")

    path.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".incoming-{path.name}-", dir=path.parent))
    try:
        tmp = staging / path.name
        con = duckdb.connect(":memory:")
        try:
            cols = ", ".join(f'"{c}" {t}' for c, t in zip(spec.columns, spec.types, strict=True))
            con.execute(f"CREATE TABLE staged ({cols})")
            if staged_rows:
                marks = ", ".join("?" * width)
                con.executemany(f"INSERT INTO staged VALUES ({marks})", staged_rows)
            con.execute(f"COPY staged TO '{_sql_path(tmp)}' (FORMAT PARQUET)")
        finally:
            con.close()
        os.replace(tmp, path)
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    return len(staged_rows)


def read_rows(path: Path | str, *, order_by: str | None = None) -> list[tuple]:
    """Read a Parquet file back as tuples, timestamps in UTC. Test and audit helper."""
    if order_by is not None and not _IDENTIFIER.match(order_by):
        raise ValueError(f"order_by {order_by!r} is not a plain identifier")
    con = duckdb.connect(":memory:")
    try:
        con.execute("SET TimeZone = 'UTC'")
        sql = f"SELECT * FROM read_parquet('{_sql_path(Path(path))}')"
        if order_by:
            sql += f' ORDER BY "{order_by}"'
        return con.execute(sql).fetchall()
    finally:
        con.close()


def export_table(duckdb_path: Path | str, table: str, path: Path | str) -> int:
    """Copy ``table`` out of a built duckdb file to ``path``; return its row count.

    Same staging-then-replace contract as `write_parquet`, read-only on the
    source, so a desired-state file is either the whole table or absent.
    """
    if not _IDENTIFIER.match(table):
        raise ValueError(f"{table!r} is not a plain identifier")
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".incoming-{path.name}-", dir=path.parent))
    try:
        tmp = staging / path.name
        con = duckdb.connect(str(duckdb_path), read_only=True)
        try:
            con.execute(f"COPY \"{table}\" TO '{_sql_path(tmp)}' (FORMAT PARQUET)")
            count = con.execute(f'SELECT count(*) FROM "{table}"').fetchone()[0]
        finally:
            con.close()
        os.replace(tmp, path)
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    return int(count)
