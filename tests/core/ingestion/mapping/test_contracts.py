"""The build's own contract gate, and the producer heartbeat it carries (#553, #551).

The puller's pin (#536) gates **landing**. `resolved_versions` resolves each
source to the newest version the store holds and consults no pin, so between
deploying a re-pin and the next successful pull the build pairs the new models
with the old-contract data. These tests are that window.
"""

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

pytest.importorskip("dbt.adapters.duckdb")

from src.core.ingestion.datasets import Pin, Subscription  # noqa: E402
from src.core.ingestion.mapping import (  # noqa: E402
    check_contracts,
    held_contract,
    producer_state,
    write_build_info,
)

PINNED = "a1" * 32
OTHER = "b2" * 32
VERSION = "v20260919T080505Z-a09d08"
SUBSCRIPTION = Subscription({"persons": Pin(2, PINNED)})


def held(root: Path, *, snapshot=..., package=..., name="persons", version=VERSION) -> Path:
    """One landed version, with either provenance file stating a contract or not.

    ``...`` means "the file exists without a `contract_hash`" — the four
    datasets landed before #536 — and None means the file is absent entirely.
    """
    d = root / name / version
    d.mkdir(parents=True)
    (d / "data.csv").write_text("id\n")
    for filename, value in (("snapshot.json", snapshot), ("datapackage.json", package)):
        if value is None:
            continue
        body = {"name": name, "version": version}
        if value is not ...:
            body["contract_hash"] = value
        (d / filename).write_text(json.dumps(body))
    return d


# --------------------------------------------------------------------------
# held_contract
# --------------------------------------------------------------------------


def test_the_held_contract_comes_from_the_snapshot_record(tmp_path):
    """What the #536 puller writes, already bare."""
    held(tmp_path, snapshot=PINNED, package=f"sha256:{OTHER}")

    assert held_contract(tmp_path, "persons", VERSION) == PINNED


def test_a_version_landed_before_the_pins_falls_back_to_its_datapackage(tmp_path):
    """Four of the six sources are that version; reading `snapshot.json` alone
    would refuse every build until each dataset happened to re-mint (usa-wa#385)."""
    held(tmp_path, snapshot=..., package=f"sha256:{PINNED}")

    assert held_contract(tmp_path, "persons", VERSION) == PINNED


def test_a_version_stating_no_contract_anywhere_has_none(tmp_path):
    held(tmp_path, snapshot=..., package=...)

    assert held_contract(tmp_path, "persons", VERSION) is None


def test_a_version_with_no_provenance_files_at_all_has_none(tmp_path):
    held(tmp_path, snapshot=None, package=None)

    assert held_contract(tmp_path, "persons", VERSION) is None


def test_an_unreadable_provenance_file_does_not_take_down_the_read(tmp_path):
    d = held(tmp_path, snapshot=None, package=f"sha256:{PINNED}")
    (d / "snapshot.json").write_text("{")

    assert held_contract(tmp_path, "persons", VERSION) == PINNED


# --------------------------------------------------------------------------
# check_contracts
# --------------------------------------------------------------------------


def test_a_store_holding_the_pinned_contract_has_nothing_to_report(tmp_path):
    held(tmp_path, snapshot=PINNED)

    assert check_contracts(tmp_path, subscription=SUBSCRIPTION) == []


def test_a_mismatch_names_the_dataset_and_both_contracts(tmp_path):
    """The sentence is everything a person needs to decide pull-or-revert."""
    held(tmp_path, snapshot=OTHER)

    (finding,) = check_contracts(tmp_path, subscription=SUBSCRIPTION)

    assert "persons" in finding
    assert VERSION in finding
    assert OTHER in finding and PINNED in finding


def test_an_uncheckable_version_warns_and_does_not_refuse(tmp_path, caplog):
    """Pre-usa-wa#385: stated nowhere, so nothing can be compared. Refusing would
    stop every build against a store landed before #536 until it re-minted."""
    held(tmp_path, snapshot=..., package=...)

    with caplog.at_level("WARNING", logger="src.core.ingestion.mapping"):
        findings = check_contracts(tmp_path, subscription=SUBSCRIPTION)

    assert findings == []
    assert any("persons" in r.getMessage() for r in caplog.records)


def test_a_dataset_the_store_does_not_hold_is_not_a_contract_finding(tmp_path):
    """It resolves to no version, and the failure it causes is the model's read."""
    assert check_contracts(tmp_path, subscription=SUBSCRIPTION) == []


def test_a_named_version_is_checked_rather_than_the_newest_held(tmp_path):
    """`--versions` pins what is built; the check must follow it, not the store."""
    held(tmp_path, snapshot=OTHER, version="v1-old")
    held(tmp_path, snapshot=PINNED)

    assert check_contracts(tmp_path, subscription=SUBSCRIPTION) == []
    assert len(check_contracts(tmp_path, versions={"persons": "v1-old"}, subscription=SUBSCRIPTION))


# --------------------------------------------------------------------------
# What BUILD.json carries (#551, #553)
# --------------------------------------------------------------------------


def _pull_record(root: Path, stale_after: str | None) -> None:
    (root).mkdir(parents=True, exist_ok=True)
    (root / "pull.json").write_text(
        json.dumps(
            {
                "pulled_at": "2026-09-23T09:00:00.000000Z",
                "checked_at": "2026-09-23T08:05:12.345678Z",
                "stale_after": stale_after,
            }
        )
    )


def test_the_producer_is_judged_at_build_time_not_at_pull_time(tmp_path):
    """`stale_after` is a fixed deadline, so a build hours later reaches a truer
    verdict than the pull did — the nightly chain's build runs 30 minutes after."""
    _pull_record(tmp_path, "2026-09-24T08:45:00.000000Z")

    fresh = producer_state(tmp_path, now=datetime(2026, 9, 23, 9, 30, tzinfo=UTC))
    late = producer_state(tmp_path, now=datetime(2026, 9, 26, 9, 30, tzinfo=UTC))

    assert fresh["stale"] is False
    assert late["stale"] is True
    assert late["checked_at"] == "2026-09-23T08:05:12.345678Z"


def test_a_store_no_pull_has_recorded_is_unknown_rather_than_stale(tmp_path):
    """Absent is not late — a hand-made store, or a pull that predates #551."""
    assert producer_state(tmp_path) is None


def test_a_recorded_deadline_that_will_not_parse_is_not_read_as_late(tmp_path):
    """Someone edited the record; that is not evidence the producer is behind."""
    _pull_record(tmp_path, "whenever")

    assert producer_state(tmp_path, now=datetime(2026, 9, 26, tzinfo=UTC))["stale"] is False


def test_a_catalog_with_no_heartbeat_leaves_the_build_unjudged(tmp_path):
    _pull_record(tmp_path, None)

    assert producer_state(tmp_path, now=datetime(2030, 1, 1, tzinfo=UTC))["stale"] is False


def test_build_info_records_the_contract_each_source_resolved_to(tmp_path):
    """Beside the versions it already carries: a diff can then be traced to the
    contract, not just to the version (#553)."""
    store = tmp_path / "store"
    held(store, snapshot=PINNED)
    _pull_record(store, "2026-09-24T08:45:00.000000Z")

    info = write_build_info(tmp_path / "out", snapshot_root=store, counts={})

    assert info["contracts"] == {"persons": PINNED}
    assert info["producer"]["checked_at"] == "2026-09-23T08:05:12.345678Z"


def test_build_info_of_a_store_no_pull_recorded_carries_a_null_producer(tmp_path):
    store = tmp_path / "store"
    held(store, snapshot=PINNED)

    info = write_build_info(tmp_path / "out", snapshot_root=store, counts={})

    assert info["producer"] is None
    assert json.loads((tmp_path / "out" / "BUILD.json").read_text())["contracts"]


def test_a_held_source_nothing_pins_has_no_contract_to_compare(tmp_path):
    """`test_project.py`'s parity test forbids one; this is only not a KeyError."""
    held(tmp_path, snapshot=OTHER, name="roles")
    held(tmp_path, snapshot=PINNED)

    assert check_contracts(tmp_path, subscription=SUBSCRIPTION) == []
