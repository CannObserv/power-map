"""The operator entry point for the mapping project (#497).

`dbt build` against the snapshot store, then every manifest table copied out
as Parquet. No database, so no --execute; the timer's only voice is the exit
code, so a failed build — or a dbt *error* — must reach it, while the warnings
the fixture is built to raise must not.
"""

import hashlib
import json
import shutil

import pytest

pytest.importorskip("dbt.adapters.duckdb")

from scripts.build_desired_state import main  # noqa: E402
from scripts.export_pm_tables import TABLES  # noqa: E402
from src.core.ingestion.datasets import load_subscription  # noqa: E402
from src.core.ingestion.mapping.parquet import PM_EXPORT_DIR, read_rows, write_parquet  # noqa: E402
from tests.core.ingestion.mapping.conftest import (  # noqa: E402
    DEFAULT_CROSSWALK,
    DEFAULT_OVERLAY,
    DEFAULT_ROLE_TYPES,
    FIXTURE_STORE,
)


@pytest.fixture
def store(tmp_path):
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
    write_parquet(
        DEFAULT_ROLE_TYPES,
        TABLES["role_types"],
        root / PM_EXPORT_DIR / "role_types.parquet",
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


def test_a_build_records_its_provenance(store, tmp_path):
    """#499 step 1 (round-2 finding 24): BUILD.json says which inputs the artifact came from —
    the dataset versions, the digests of PM's exports — so a run summary can carry it."""
    out = tmp_path / "desired_state"

    args = ["--root", str(store), "--out", str(out), "--duckdb", str(tmp_path / "m.duckdb")]
    assert main(args) == 0

    info = json.loads((out / "BUILD.json").read_text())
    assert info["datasets"] == {
        "persons": "v1",
        "organizations": "v1",
        "person_crosswalk": "v1",
        "org_crosswalk": "v1",
        "assignments": "v1",
        "roles": "v1",
    }
    crosswalk = (store / PM_EXPORT_DIR / "producer_crosswalk.parquet").read_bytes()
    assert info["pm_exports"]["producer_crosswalk"] == hashlib.sha256(crosswalk).hexdigest()
    assert "curation_overlay" in info["pm_exports"]
    assert info["tables"]["desired_people"] == 4
    assert info["built_at"].endswith("Z") and "T" in info["built_at"]


# --------------------------------------------------------------------------
# The contract gate (#553)
# --------------------------------------------------------------------------


def _args(store, tmp_path):
    return [
        "--root",
        str(store),
        "--out",
        str(tmp_path / "out"),
        "--duckdb",
        str(tmp_path / "m.duckdb"),
    ]


def test_a_held_snapshot_that_is_not_its_pin_fails_the_build(store, tmp_path, capsys):
    """The window a re-pin opens: new models, old-contract data still newest held.
    `power-map-desired-state.service` stops the chain, so the applier never sees it."""
    (store / "persons" / "v1" / "snapshot.json").write_text('{"contract_hash": "%s"}' % ("f0" * 32))

    code = main(_args(store, tmp_path))

    # Read from stdout, not caplog: `main` calls `configure_logging()`, which
    # replaces the handlers caplog installed. Stdout is what the journal keeps.
    logged = capsys.readouterr().out
    assert code == 1
    assert not (tmp_path / "out" / "desired_people.parquet").exists()
    assert "f0" * 32 in logged and "pinned to" in logged
    assert "dddb4e9f" in logged


def test_a_store_stating_no_contracts_still_builds(store, tmp_path):
    """The fixture store is every pre-usa-wa#385 version: uncheckable warns, never refuses."""
    assert main(_args(store, tmp_path)) == 0


def test_a_build_records_the_contract_each_source_held(store, tmp_path):
    """Null where a version states none — the honest record of what could be checked."""
    (store / "persons" / "v1" / "datapackage.json").write_text(
        json.dumps({"contract_hash": load_subscription().pins["persons"].contract_hash})
    )

    assert main(_args(store, tmp_path)) == 0

    contracts = json.loads((tmp_path / "out" / "BUILD.json").read_text())["contracts"]
    assert contracts["persons"] == load_subscription().pins["persons"].contract_hash
    assert contracts["roles"] is None


def test_an_unreadable_pin_file_is_a_usage_error_not_a_traceback(
    store, tmp_path, capsys, monkeypatch
):
    """CR 2: the puller answers the same bad file with a sentence and exit 2, so
    one malformed pin must not read as configuration in step 1 and a crash in step 3."""

    def unreadable(*a, **kw):
        raise ValueError("sources.yml: usa_wa.persons: meta.schema_major None is not an integer")

    monkeypatch.setattr("src.core.ingestion.mapping.load_subscription", unreadable)

    with pytest.raises(SystemExit) as exc:
        main(_args(store, tmp_path))

    assert exc.value.code == 2
    assert "usa_wa.persons" in capsys.readouterr().err
