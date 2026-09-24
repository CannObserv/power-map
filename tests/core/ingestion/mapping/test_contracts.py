"""The build's own contract gate, and what it records of its inputs (#553, #551, #535).

The puller's pin (#536) gates **landing**. `resolved_versions` resolves each
source to the newest version the store holds and consults no pin, so between
deploying a re-pin and the next successful pull the build pairs the new models
with the old-contract data. These tests are that window.

The same resolution consults no catalog either: a version the pin refused, or
one that failed verification, leaves the store's newest behind the publisher's
with the heartbeat still fresh. `BUILD.json`'s `currency` block is where the
build says so (#535).
"""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

pytest.importorskip("dbt.adapters.duckdb")

from src.core.ingestion.datasets import PULL_MAX_AGE, Pin, Subscription  # noqa: E402
from src.core.ingestion.mapping import (  # noqa: E402
    check_contracts,
    held_contract,
    input_currency,
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


def _pull_record(
    root: Path,
    stale_after: str | None = None,
    *,
    offered: dict | None = None,
    pulled_at: str = "2026-09-23T09:00:00.000000Z",
) -> None:
    """A `pull.json`; ``offered`` None is a record written before #535 carried one."""
    (root).mkdir(parents=True, exist_ok=True)
    record = {
        "pulled_at": pulled_at,
        "checked_at": "2026-09-23T08:05:12.345678Z",
        "stale_after": stale_after,
    }
    if offered is not None:
        record["offered"] = offered
    (root / "pull.json").write_text(json.dumps(record))


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


def test_a_provenance_file_truncated_mid_character_reads_as_unstated(tmp_path):
    """CR 1: `read_text` raises `UnicodeDecodeError`, not a JSON error, so the
    narrower catch let the one shape of truncation through that it named."""
    d = held(tmp_path, snapshot=None, package=f"sha256:{PINNED}")
    (d / "snapshot.json").write_bytes(b'{"contract_hash": "\xff\xfe')

    assert held_contract(tmp_path, "persons", VERSION) == PINNED


def test_build_info_judges_the_producer_at_the_moment_it_is_given(tmp_path):
    """CR 6: the field deciding whether a run counts towards the `--execute`
    streak was the one thing in BUILD.json no test could pin to a moment."""
    store = tmp_path / "store"
    held(store, snapshot=PINNED)
    _pull_record(store, "2026-09-24T08:45:00.000000Z")

    info = write_build_info(
        tmp_path / "out",
        snapshot_root=store,
        counts={},
        now=datetime(2026, 9, 26, tzinfo=UTC),
    )

    assert info["producer"]["stale"] is True


def test_the_moment_given_stamps_built_at_as_well_as_the_verdict(tmp_path):
    """CR 11: `now` is the moment of the build, so one parameter must not leave
    `built_at` reading a second clock."""
    store = tmp_path / "store"
    held(store, snapshot=PINNED)

    info = write_build_info(
        tmp_path / "out",
        snapshot_root=store,
        counts={},
        now=datetime(2026, 9, 26, 9, 30, tzinfo=UTC),
    )

    assert info["built_at"] == "2026-09-26T09:30:00.000000Z"


# --------------------------------------------------------------------------
# Whether the build's inputs are what usa-wa offers (#535)
# --------------------------------------------------------------------------

NEWER = "v20260920T080505Z-b1c2d3"
PULLED = datetime(2026, 9, 23, 9, 0, tzinfo=UTC)
BUILT = PULLED + timedelta(minutes=30)


def test_inputs_the_publisher_still_offers_are_current(tmp_path):
    _pull_record(tmp_path, offered={"persons": VERSION})

    currency = input_currency(tmp_path, datasets={"persons": VERSION}, now=BUILT)

    assert currency == {
        "pulled_at": "2026-09-23T09:00:00.000000Z",
        "pull_overdue": False,
        "superseded": {},
    }


def test_a_version_the_publisher_has_moved_on_from_is_superseded(tmp_path):
    """The 2026-09-18 night: the pin refused the new version, the heartbeat was
    fresh, and the build read the store's newest as though nothing had moved."""
    _pull_record(tmp_path, offered={"persons": NEWER})

    currency = input_currency(tmp_path, datasets={"persons": VERSION}, now=BUILT)

    assert currency["superseded"] == {"persons": {"built": VERSION, "offered": NEWER}}


def test_a_dataset_the_publisher_no_longer_offers_is_superseded_by_nothing(tmp_path):
    """The pull exits 1 on it (`missing`); the build must not read it as current."""
    _pull_record(tmp_path, offered={"roles": NEWER})

    currency = input_currency(tmp_path, datasets={"persons": VERSION}, now=BUILT)

    assert currency["superseded"] == {"persons": {"built": VERSION, "offered": None}}


def test_a_pull_record_from_before_the_offer_was_recorded_is_unknown_not_behind(tmp_path):
    """A deploy must not restart the streak the nightly has been building: the
    first build after it reads the record the pre-#535 pull left."""
    _pull_record(tmp_path)

    currency = input_currency(tmp_path, datasets={"persons": VERSION}, now=BUILT)

    assert currency["superseded"] is None


def test_a_store_no_pull_has_recorded_has_no_currency_to_judge(tmp_path):
    assert input_currency(tmp_path, datasets={"persons": VERSION}, now=BUILT) is None


def test_a_pull_older_than_the_bound_is_overdue_whatever_it_offered(tmp_path):
    """The catalog fetch itself failed, so `pull.json` is yesterday's: its offer
    says nothing about today's. #551 catches this only while usa-wa's own
    `stale_after` happens to lapse before the build; this does not depend on it."""
    _pull_record(tmp_path, offered={"persons": VERSION})

    within = input_currency(tmp_path, datasets={"persons": VERSION}, now=PULLED + PULL_MAX_AGE)
    beyond = input_currency(
        tmp_path, datasets={"persons": VERSION}, now=PULLED + PULL_MAX_AGE + timedelta(seconds=1)
    )

    assert within["pull_overdue"] is False
    assert beyond["pull_overdue"] is True
    assert beyond["superseded"] == {}


def test_a_recorded_pull_time_that_will_not_parse_is_not_read_as_overdue(tmp_path):
    """Someone edited the record; that is not evidence the pull failed."""
    _pull_record(tmp_path, offered={"persons": VERSION}, pulled_at="whenever")

    currency = input_currency(tmp_path, datasets={"persons": VERSION}, now=BUILT)

    assert currency["pull_overdue"] is False


def test_build_info_records_the_currency_of_the_versions_it_built_from(tmp_path):
    store = tmp_path / "store"
    held(store, snapshot=PINNED)
    _pull_record(store, offered={"persons": NEWER})

    info = write_build_info(tmp_path / "out", snapshot_root=store, counts={}, now=BUILT)

    assert info["currency"]["superseded"] == {"persons": {"built": VERSION, "offered": NEWER}}
    assert (
        json.loads((tmp_path / "out" / "BUILD.json").read_text())["currency"] == (info["currency"])
    )


def test_a_named_version_that_is_not_the_offer_is_superseded(tmp_path):
    """A hand-picked `versions=` is not what the publisher currently offers either."""
    store = tmp_path / "store"
    held(store, snapshot=PINNED)
    held(store, snapshot=PINNED, version=NEWER)
    _pull_record(store, offered={"persons": NEWER})

    info = write_build_info(
        tmp_path / "out", snapshot_root=store, counts={}, versions={"persons": VERSION}, now=BUILT
    )

    assert info["currency"]["superseded"] == {"persons": {"built": VERSION, "offered": NEWER}}


def test_the_producer_block_carries_the_heartbeat_and_not_the_offer(tmp_path):
    """The offer is judged in `currency`; copied into `producer` too it would be
    a second record of every dataset's version, and only one would be read."""
    store = tmp_path / "store"
    held(store, snapshot=PINNED)
    _pull_record(store, "2026-09-24T08:45:00.000000Z", offered={"persons": VERSION})

    info = write_build_info(tmp_path / "out", snapshot_root=store, counts={}, now=BUILT)

    assert "offered" not in info["producer"]
    assert info["producer"]["pulled_at"] == "2026-09-23T09:00:00.000000Z"
