"""The verbatim snapshot store and the pull loop (#496).

The store's contract is that a version directory exists only when the snapshot
inside it is complete and verified. Everything else here — hash-skip, pruning,
the schema pin — is arranged so that a run which did not check something says so
rather than reporting the same green as a run that did.
"""

import hashlib
import json
import logging
from datetime import UTC, datetime

import httpx
import pytest

from src.core.ingestion.datasets import (
    Catalog,
    CatalogEntry,
    Pin,
    PullReport,
    SnapshotStore,
    Subscription,
    parse_catalog,
    pull,
)

DATA = b"kind,usa_wa_id,pm_id\nperson,01KV6T7RTS5PVF1HB94T5X23HY,01KV6SW78XKRQDWC4MPWNEPQ5A\n"
DIGEST = hashlib.sha256(DATA).hexdigest()
PACKAGE = b'{"name": "pm_anchors", "resources": []}'
# Any 64-hex fingerprint; what matters is only whether the pin and entry agree.
CONTRACT = "c0" * 32


def entry(
    name="pm_anchors",
    version="v1-aaa",
    *,
    sha256=DIGEST,
    schema="1.5.0",
    tier="cutover",
    contract_hash=CONTRACT,
):
    return CatalogEntry(
        name=name,
        tier=tier,
        latest_version=version,
        schema_version=schema,
        sha256=sha256,
        rows=1,
        bytes=len(DATA),
        generated_at="2026-09-09T04:34:02Z",
        contract_hash=contract_hash,
    )


def pinned(*names, major=1, contract_hash=CONTRACT):
    """A subscription pinning each of ``names`` to the same contract."""
    return Subscription({name: Pin(major, contract_hash) for name in names})


def serving(body=DATA, package=PACKAGE):
    """A transport that serves one dataset version."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("data.csv"):
            return httpx.Response(200, content=body, headers={"content-type": "text/csv"})
        return httpx.Response(200, content=package, headers={"content-type": "application/json"})

    return httpx.MockTransport(handler)


def _serving_package_status(status):
    """Serves data.csv normally and answers datapackage.json with `status`."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("data.csv"):
            return httpx.Response(200, content=DATA, headers={"content-type": "text/csv"})
        return httpx.Response(status)

    return httpx.MockTransport(handler)


async def _pull_one(store, transport):
    async with httpx.AsyncClient(transport=transport) as client:
        return await pull(
            "https://usa-wa.exe.xyz:8000",
            [entry()],
            store,
            token="tok",
            client=client,
            subscription=pinned("pm_anchors"),
        )


# --------------------------------------------------------------------------
# SnapshotStore
# --------------------------------------------------------------------------


def test_a_landed_version_is_readable_verbatim(tmp_path):
    store = SnapshotStore(tmp_path)

    path = store.land(entry(), {"data.csv": DATA, "datapackage.json": PACKAGE})

    assert (path / "data.csv").read_bytes() == DATA
    assert store.has("pm_anchors", "v1-aaa")


def test_a_digest_mismatch_leaves_no_version_directory(tmp_path):
    """A half-written snapshot must never be visible to the applier as a whole one."""
    store = SnapshotStore(tmp_path)

    with pytest.raises(ValueError, match="digest mismatch"):
        store.land(entry(sha256="0" * 64), {"data.csv": DATA})

    assert not store.has("pm_anchors", "v1-aaa")
    assert store.versions("pm_anchors") == []


def test_a_truncated_download_names_the_length_the_catalog_stated(tmp_path):
    """ "digest mismatch" is true but "10 bytes, not 105" is diagnostic."""
    store = SnapshotStore(tmp_path)

    with pytest.raises(ValueError, match=r"length mismatch.*catalog says"):
        store.land(entry(), {"data.csv": DATA[:10]})


def test_land_refuses_an_entry_whose_name_escapes_the_store(tmp_path):
    """`parse_catalog` guards the catalog; this guards a hand-built entry."""
    store = SnapshotStore(tmp_path / "store")

    with pytest.raises(ValueError, match="unsafe"):
        store.land(entry(name="../escaped"), {"data.csv": DATA})

    assert not (tmp_path / "escaped").exists()


def test_land_refuses_a_filename_that_would_escape_the_version(tmp_path):
    """The third path input into the same function; `pull` passes constants today."""
    store = SnapshotStore(tmp_path / "store")

    with pytest.raises(ValueError, match="unsafe filename"):
        store.land(entry(), {"data.csv": DATA, "../escaped.json": b"{}"})

    assert not (tmp_path / "escaped.json").exists()


def test_landing_the_same_version_twice_is_allowed_and_replaces_it(tmp_path):
    """Re-landing is how a corrupted local copy is repaired; it must not error."""
    store = SnapshotStore(tmp_path)
    store.land(entry(), {"data.csv": DATA})

    store.land(entry(), {"data.csv": DATA, "datapackage.json": PACKAGE})

    assert (store.version_dir("pm_anchors", "v1-aaa") / "datapackage.json").exists()


def test_landing_does_not_disturb_another_runs_staging_directory(tmp_path):
    """The timer and a manual run can land the same version at the same moment.

    A staging name derived only from the version is shared across processes: one
    run deletes the other's half-written directory, and whichever reaches
    `os.replace` first can promote a snapshot the other was midway through.
    """
    store = SnapshotStore(tmp_path)
    other_run = store.dataset_dir("pm_anchors") / ".incoming-v1-aaa"
    other_run.mkdir(parents=True)
    (other_run / "half-written.csv").write_bytes(b"partial")

    store.land(entry(), {"data.csv": DATA})

    assert (other_run / "half-written.csv").exists()


def test_a_failed_write_leaves_no_staging_directory_behind(tmp_path):
    """Unique staging names would otherwise accumulate one leak per failure."""
    store = SnapshotStore(tmp_path)

    with pytest.raises(TypeError):
        store.land(entry(), {"data.csv": DATA, "datapackage.json": "not bytes"})

    assert list(store.dataset_dir("pm_anchors").glob(".incoming*")) == []


def test_a_landed_version_records_its_own_provenance(tmp_path):
    """The store is self-describing, so nothing downstream re-reads the catalog.

    A consumer of a pulled snapshot — the crosswalk seed, later the applier —
    needs the digest and the version that produced it. Making the store carry
    them means verification does not depend on a network round trip that may
    return a different version by then.
    """
    store = SnapshotStore(tmp_path)

    path = store.land(entry(), {"data.csv": DATA})

    meta = json.loads((path / "snapshot.json").read_text())
    assert meta["name"] == "pm_anchors"
    assert meta["version"] == "v1-aaa"
    assert meta["sha256"] == DIGEST
    assert meta["contract_hash"] == CONTRACT
    assert meta["generated_at"] == "2026-09-09T04:34:02Z"


def test_the_recorded_digest_matches_the_file_it_describes(tmp_path):
    """Belt and braces: the metadata is only useful if it describes this copy."""
    store = SnapshotStore(tmp_path)

    path = store.land(entry(), {"data.csv": DATA})

    meta = json.loads((path / "snapshot.json").read_text())
    assert hashlib.sha256((path / "data.csv").read_bytes()).hexdigest() == meta["sha256"]


def test_versions_are_returned_newest_last(tmp_path):
    store = SnapshotStore(tmp_path)
    for v in ("v20260901T000000Z-aaa", "v20260909T000000Z-bbb", "v20260905T000000Z-ccc"):
        store.land(entry(version=v), {"data.csv": DATA})

    assert store.versions("pm_anchors")[-1] == "v20260909T000000Z-bbb"


def test_a_crashed_run_leaves_no_version_behind_in_the_listing(tmp_path):
    """`.incoming-*` is a staging directory, not a stored version.

    Reported as one it inflates the count `prune` reasons about and would be
    handed to a consumer as a version that was never verified.
    """
    store = SnapshotStore(tmp_path)
    store.land(entry(), {"data.csv": DATA})
    (store.dataset_dir("pm_anchors") / ".incoming-v2-bbb").mkdir()

    assert store.versions("pm_anchors") == ["v1-aaa"]


def test_prune_keeps_the_newest_n_and_never_the_applied_one(tmp_path):
    """`keep_version` is the last-applied snapshot: dropping it would strand the applier."""
    store = SnapshotStore(tmp_path)
    for v in ("v1-aaa", "v2-bbb", "v3-ccc", "v4-ddd"):
        store.land(entry(version=v), {"data.csv": DATA})

    removed = store.prune("pm_anchors", keep=2, keep_version="v1-aaa")

    assert removed == ["v2-bbb"]
    assert set(store.versions("pm_anchors")) == {"v1-aaa", "v3-ccc", "v4-ddd"}


def test_prune_on_an_unknown_dataset_is_not_an_error(tmp_path):
    assert SnapshotStore(tmp_path).prune("never-pulled", keep=2, keep_version=None) == []


# --------------------------------------------------------------------------
# PullReport
# --------------------------------------------------------------------------


def test_two_reports_do_not_share_a_list():
    """The None-sentinel dance existed to avoid this; `default_factory` is the tool."""
    first, second = PullReport(), PullReport()

    first.landed.append("pm_anchors")

    assert second.landed == []


def test_a_report_starts_empty_and_is_typed_as_such():
    report = PullReport()

    assert (report.landed, report.skipped, report.failed) == ([], [], [])
    assert not report.failed_run


# --------------------------------------------------------------------------
# pull
# --------------------------------------------------------------------------


async def test_pull_lands_a_subscribed_dataset(tmp_path):
    store = SnapshotStore(tmp_path)

    async with httpx.AsyncClient(transport=serving()) as client:
        report = await pull(
            "https://usa-wa.exe.xyz:8000",
            [entry()],
            store,
            token="tok",
            client=client,
            subscription=pinned("pm_anchors"),
        )

    assert report.landed == ["pm_anchors"]
    assert store.has("pm_anchors", "v1-aaa")


async def test_pull_skips_a_version_already_stored(tmp_path):
    """Hash-skip is what makes a nightly timer free when nothing has moved."""
    store = SnapshotStore(tmp_path)
    store.land(entry(), {"data.csv": DATA})
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        return httpx.Response(200, content=DATA)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        report = await pull(
            "https://usa-wa.exe.xyz:8000",
            [entry()],
            store,
            token="tok",
            client=client,
            subscription=pinned("pm_anchors"),
        )

    assert report.skipped == ["pm_anchors"]
    assert calls == []


async def test_pull_ignores_a_dataset_outside_the_subscription(tmp_path):
    store = SnapshotStore(tmp_path)

    async with httpx.AsyncClient(transport=serving()) as client:
        report = await pull(
            "https://usa-wa.exe.xyz:8000",
            [entry(), entry(name="stg_wsl_committees", tier="staging")],
            store,
            token="tok",
            client=client,
            subscription=pinned("pm_anchors"),
        )

    assert report.landed == ["pm_anchors"]
    assert not store.has("stg_wsl_committees", "v1-aaa")


async def test_a_conformed_dataset_with_no_pin_is_neither_pulled_nor_a_failure(tmp_path):
    """The pins are the subscription (#536), not a filter over the conformed tier.

    A new upstream product the mapping models do not read is nothing to land and
    nothing to refuse: failing the nightly over it would page someone for a
    dataset PM cannot consume.
    """
    store = SnapshotStore(tmp_path)
    catalog = [entry(name="persons", tier="conformed"), entry(name="brand_new", tier="conformed")]

    async with httpx.AsyncClient(transport=serving()) as client:
        report = await pull(
            "https://usa-wa.exe.xyz:8000",
            catalog,
            store,
            token="tok",
            client=client,
            subscription=pinned("persons"),
        )

    assert report.landed == ["persons"]
    assert not report.failed_run
    assert not store.has("brand_new", "v1-aaa")


async def test_a_corpus_spanning_majors_lands_when_each_dataset_is_pinned(tmp_path):
    """The #536 failure: no single major accepted persons@2 and roles@1 together."""
    store = SnapshotStore(tmp_path)
    catalog = [entry(name="persons", schema="2.0.0"), entry(name="roles", schema="1.3.0")]
    subscription = Subscription({"persons": Pin(2, CONTRACT), "roles": Pin(1, CONTRACT)})

    async with httpx.AsyncClient(transport=serving()) as client:
        report = await pull(
            "https://usa-wa.exe.xyz:8000",
            catalog,
            store,
            token="tok",
            client=client,
            subscription=subscription,
        )

    assert report.landed == ["persons", "roles"]
    assert not report.failed_run


async def test_pull_refuses_a_dataset_whose_schema_major_moved(tmp_path):
    """A major bump is a contract break: report it loudly, never land it quietly."""
    store = SnapshotStore(tmp_path)

    async with httpx.AsyncClient(transport=serving()) as client:
        report = await pull(
            "https://usa-wa.exe.xyz:8000",
            [entry(schema="2.0.0")],
            store,
            token="tok",
            client=client,
            subscription=pinned("pm_anchors"),
        )

    ((name, reason),) = report.incompatible
    assert name == "pm_anchors"
    assert "2.0.0" in reason and "pinned to major 1" in reason
    assert report.failed_run
    assert not store.has("pm_anchors", "v1-aaa")


async def test_a_moved_major_names_the_contract_to_re_pin_to(tmp_path):
    """A re-pin needs both values; the line should not send anyone to catalog.json (CR 2)."""
    store = SnapshotStore(tmp_path)

    async with httpx.AsyncClient(transport=serving()) as client:
        report = await pull(
            "https://usa-wa.exe.xyz:8000",
            [entry(schema="2.0.0", contract_hash="d1" * 32)],
            store,
            token="tok",
            client=client,
            subscription=pinned("pm_anchors"),
        )

    ((_, reason),) = report.incompatible
    assert f"sha256:{'d1' * 32}" in reason


def test_a_moved_major_with_no_contract_hash_says_so_rather_than_printing_none():
    reason = pinned("pm_anchors").refusal(entry(schema="2.0.0", contract_hash=None))

    assert "no contract_hash" in reason
    assert "None" not in reason


async def test_a_contract_change_within_the_pinned_major_is_refused(tmp_path):
    """usa-wa's gate is one-way: a contract change needs *a* bump, not a major one.

    A column dropped under a minor bump passes a major-only pin, and the models
    read every column as varchar, so a type change never errors at all. The hash
    is the thing that cannot drift from the shape.
    """
    store = SnapshotStore(tmp_path)

    async with httpx.AsyncClient(transport=serving()) as client:
        report = await pull(
            "https://usa-wa.exe.xyz:8000",
            [entry(schema="1.6.0", contract_hash="d1" * 32)],
            store,
            token="tok",
            client=client,
            subscription=pinned("pm_anchors"),
        )

    ((name, reason),) = report.incompatible
    assert name == "pm_anchors"
    assert "within major 1" in reason
    assert report.failed_run
    assert not store.has("pm_anchors", "v1-aaa")


async def test_an_entry_that_stops_publishing_its_contract_hash_is_refused(tmp_path):
    """Treating "no hash" as "no check" would let a publisher regression switch the gate off."""
    store = SnapshotStore(tmp_path)

    async with httpx.AsyncClient(transport=serving()) as client:
        report = await pull(
            "https://usa-wa.exe.xyz:8000",
            [entry(contract_hash=None)],
            store,
            token="tok",
            client=client,
            subscription=pinned("pm_anchors"),
        )

    ((name, reason),) = report.incompatible
    assert name == "pm_anchors"
    assert "no contract_hash" in reason
    assert not store.has("pm_anchors", "v1-aaa")


async def test_an_unparseable_schema_version_fails_only_its_own_dataset(tmp_path):
    """The schema check ran outside the per-entry guard, so one bad row aborted the run.

    Datasets that had already landed stayed on disk while the rest were never
    attempted — the opposite of what `test_one_dataset_failing_does_not_stop_the_others`
    promises.
    """
    store = SnapshotStore(tmp_path)

    async with httpx.AsyncClient(transport=serving()) as client:
        report = await pull(
            "https://usa-wa.exe.xyz:8000",
            [entry(name="broken", schema="unversioned"), entry()],
            store,
            token="tok",
            client=client,
            subscription=pinned("broken", "pm_anchors"),
        )

    assert report.landed == ["pm_anchors"]
    assert [name for name, _ in report.failed] == ["broken"]


async def test_pull_records_a_corrupt_download_as_a_failure(tmp_path):
    store = SnapshotStore(tmp_path)

    async with httpx.AsyncClient(transport=serving(body=DATA + b"truncated")) as client:
        report = await pull(
            "https://usa-wa.exe.xyz:8000",
            [entry()],
            store,
            token="tok",
            client=client,
            subscription=pinned("pm_anchors"),
        )

    assert [name for name, _ in report.failed] == ["pm_anchors"]
    assert report.failed_run
    assert not store.has("pm_anchors", "v1-aaa")


async def test_one_dataset_failing_does_not_stop_the_others(tmp_path):
    """A nightly pull that abandons ten good datasets over one bad one is worse."""
    store = SnapshotStore(tmp_path)
    bad = entry(name="broken", sha256="0" * 64)

    async with httpx.AsyncClient(transport=serving()) as client:
        report = await pull(
            "https://usa-wa.exe.xyz:8000",
            [bad, entry()],
            store,
            token="tok",
            client=client,
            subscription=pinned("broken", "pm_anchors"),
        )

    assert report.landed == ["pm_anchors"]
    assert [name for name, _ in report.failed] == ["broken"]


async def test_a_filesystem_failure_fails_its_dataset_not_the_run(tmp_path, monkeypatch):
    """A full disk raises OSError, which sat outside the per-entry guard.

    Same isolation failure CR 2 fixed, through the other door: `land()` is all
    filesystem calls, and this VM has run out of disk before.
    """
    store = SnapshotStore(tmp_path)
    real_land = store.land

    def land(entry, files):
        if entry.name == "broken":
            raise OSError(28, "No space left on device")
        return real_land(entry, files)

    monkeypatch.setattr(store, "land", land)

    async with httpx.AsyncClient(transport=serving()) as client:
        report = await pull(
            "https://usa-wa.exe.xyz:8000",
            [entry(name="broken"), entry()],
            store,
            token="tok",
            client=client,
            subscription=pinned("broken", "pm_anchors"),
        )

    assert report.landed == ["pm_anchors"]
    assert [name for name, _ in report.failed] == ["broken"]


async def test_a_subscription_naming_a_dataset_the_catalog_lacks_is_reported(tmp_path):
    """Silently pulling nothing is how a renamed dataset goes unnoticed for weeks."""
    store = SnapshotStore(tmp_path)

    async with httpx.AsyncClient(transport=serving()) as client:
        report = await pull(
            "https://usa-wa.exe.xyz:8000",
            [entry()],
            store,
            token="tok",
            client=client,
            subscription=pinned("gone", "pm_anchors"),
        )

    assert report.missing == ["gone"]
    assert report.failed_run


async def test_a_transient_error_on_the_package_fails_the_dataset(tmp_path):
    """Landing anyway makes the damage permanent, not transient.

    `store.has()` is true afterwards, so hash-skip never re-fetches this version:
    a 500 that lasted one second would leave a schema-less snapshot that #497
    reads for the life of the version.
    """
    store = SnapshotStore(tmp_path)

    report = await _pull_one(store, _serving_package_status(500))

    assert [name for name, _ in report.failed] == ["pm_anchors"]
    assert not store.has("pm_anchors", "v1-aaa")


async def test_an_auth_failure_on_the_package_fails_the_dataset(tmp_path):
    """An expired token mid-pull is not a statement that no schema exists."""
    store = SnapshotStore(tmp_path)

    report = await _pull_one(store, _serving_package_status(403))

    assert [name for name, _ in report.failed] == ["pm_anchors"]


async def test_a_genuinely_absent_package_still_lands_and_says_so(tmp_path, caplog):
    """404 is the publisher stating there is none; the digest covers data.csv."""
    store = SnapshotStore(tmp_path)

    with caplog.at_level(logging.WARNING, logger="src.core.ingestion.datasets"):
        report = await _pull_one(store, _serving_package_status(404))

    assert report.landed == ["pm_anchors"]
    assert not (store.version_dir("pm_anchors", "v1-aaa") / "datapackage.json").exists()
    assert any("datapackage.json" in r.getMessage() for r in caplog.records)


async def test_the_report_says_which_snapshots_landed_without_a_package(tmp_path):
    """The WARNING is interleaved with httpx INFO; the report is what is read.

    Exit stays 0 — a genuinely absent package is the publisher's statement, not
    a failure — but #497 reads these files, so the run must say it happened.
    """
    store = SnapshotStore(tmp_path)

    report = await _pull_one(store, _serving_package_status(404))

    assert report.landed == ["pm_anchors"]
    assert report.landed_without_package == ["pm_anchors"]
    assert not report.failed_run


# --------------------------------------------------------------------------
# The pull's run record (#551)
# --------------------------------------------------------------------------


def test_the_run_record_carries_the_heartbeat_verbatim(tmp_path):
    """What the build reads later to tell whether it is building on a late producer."""
    store = SnapshotStore(tmp_path)
    catalog = parse_catalog(
        {
            "checked_at": "2026-09-23T08:05:12.345678Z",
            "stale_after": "2026-09-24T08:45:00.000000Z",
            "datasets": [],
        }
    )

    store.record_pull(catalog, at=datetime(2026, 9, 23, 9, 0, tzinfo=UTC))

    assert store.pull_record() == {
        "pulled_at": "2026-09-23T09:00:00.000000Z",
        "checked_at": "2026-09-23T08:05:12.345678Z",
        "stale_after": "2026-09-24T08:45:00.000000Z",
        "offered": {},
    }


def test_the_run_record_carries_what_the_publisher_offers_of_every_dataset(tmp_path):
    """#535: what the build compares its resolved versions against. Every entry,
    not only the subscribed ones: a `--dataset` narrowed pull rewrites this file
    too, and must not leave the build blind to the datasets it did not name."""
    store = SnapshotStore(tmp_path)
    catalog = Catalog(entries=(entry("persons", "v2-bbb"), entry("roles", "v1-aaa")))

    store.record_pull(catalog, at=datetime(2026, 9, 23, tzinfo=UTC))

    assert store.pull_record()["offered"] == {"persons": "v2-bbb", "roles": "v1-aaa"}


def test_a_record_of_a_catalog_with_no_heartbeat_says_so_rather_than_omitting_it(tmp_path):
    """A null is "the publisher stated none"; a missing key is "nobody looked"."""
    store = SnapshotStore(tmp_path)

    store.record_pull(parse_catalog({"datasets": []}), at=datetime(2026, 9, 23, tzinfo=UTC))

    assert store.pull_record()["stale_after"] is None


def test_an_unpulled_store_has_no_run_record(tmp_path):
    """Absent is not stale: a build against a hand-made store reads None, not a finding."""
    assert SnapshotStore(tmp_path).pull_record() is None


def test_an_unreadable_run_record_reads_as_none(tmp_path):
    """A truncated write must not take down the build that reads it."""
    (tmp_path / "pull.json").write_text("{")

    assert SnapshotStore(tmp_path).pull_record() is None


def test_a_stale_producer_fails_the_run(tmp_path):
    """The one outcome that otherwise looks exactly like a quiet night: everything
    unchanged, exit 0, and inputs nobody has been able to refresh."""
    report = PullReport(skipped=["pm_anchors"])

    report.producer_stale = "usa-wa is behind the clock: …"

    assert report.failed_run


def test_a_run_record_truncated_mid_character_reads_as_none(tmp_path):
    """CR 1: the docstring promises a truncated write cannot stop a build, and
    `UnicodeDecodeError` is a ValueError rather than a `json.JSONDecodeError`."""
    (tmp_path / "pull.json").write_bytes(b'{"checked_at": "\xff\xfe')

    assert SnapshotStore(tmp_path).pull_record() is None
