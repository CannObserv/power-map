"""The operator entry point for the mapping project (#497).

`dbt build` against the snapshot store, then every manifest table copied out
as Parquet. No database, so no --execute; the timer's only voice is the exit
code, so a failed build — or a dbt *error* — must reach it, while the warnings
the fixture is built to raise must not.
"""

import pytest

pytest.importorskip("dbt.adapters.duckdb")

from scripts.build_desired_state import main  # noqa: E402
from src.core.ingestion.mapping.parquet import read_rows  # noqa: E402
from tests.core.ingestion.mapping.conftest import (  # noqa: E402
    DEFAULT_CROSSWALK,
    DEFAULT_OVERLAY,
    FIXTURE_STORE,
)


@pytest.fixture
def store(tmp_path):
    import shutil

    from scripts.export_pm_tables import TABLES
    from src.core.ingestion.mapping.parquet import PM_EXPORT_DIR, write_parquet

    root = tmp_path / "store"
    shutil.copytree(FIXTURE_STORE, root)
    write_parquet(
        DEFAULT_CROSSWALK,
        TABLES["producer_crosswalk"],
        root / PM_EXPORT_DIR / "producer_crosswalk.parquet",
    )
    write_parquet(
        DEFAULT_OVERLAY,
        TABLES["curation_overlay"],
        root / PM_EXPORT_DIR / "curation_overlay.parquet",
    )
    return root


def test_a_clean_build_writes_every_desired_state_table_and_exits_zero(store, tmp_path):
    out = tmp_path / "desired_state"

    code = main(["--root", str(store), "--out", str(out), "--duckdb", str(tmp_path / "m.duckdb")])

    assert code == 0
    assert (out / "desired_people.parquet").exists()
    assert (out / "desired_entity_events.parquet").exists()
    assert len(read_rows(out / "desired_people.parquet")) == 4


def test_a_store_with_no_export_fails_the_build_and_exits_one(tmp_path):
    """Models read the _pm/ files; without them the build errors — loudly."""
    import shutil

    root = tmp_path / "store"
    shutil.copytree(FIXTURE_STORE, root)

    code = main(
        [
            "--root",
            str(root),
            "--out",
            str(tmp_path / "out"),
            "--duckdb",
            str(tmp_path / "m.duckdb"),
        ]
    )

    assert code == 1
    assert not (tmp_path / "out" / "desired_people.parquet").exists()
