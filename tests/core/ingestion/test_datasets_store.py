"""The verbatim snapshot store and the pull loop (#496).

The store's contract is that a version directory exists only when the snapshot
inside it is complete and verified. Everything else here — hash-skip, pruning,
the schema pin — is arranged so that a run which did not check something says so
rather than reporting the same green as a run that did.
"""

import hashlib
import json
import logging

import httpx
import pytest

from src.core.ingestion.datasets import (
    CatalogEntry,
    SnapshotStore,
    Subscription,
    pull,
)

DATA = b"kind,usa_wa_id,pm_id\nperson,01KV6T7RTS5PVF1HB94T5X23HY,01KV6SW78XKRQDWC4MPWNEPQ5A\n"
DIGEST = hashlib.sha256(DATA).hexdigest()
PACKAGE = b'{"name": "pm_anchors", "resources": []}'


def entry(name="pm_anchors", version="v1-aaa", *, sha256=DIGEST, schema="1.5.0", tier="cutover"):
    return CatalogEntry(
        name=name,
        tier=tier,
        latest_version=version,
        schema_version=schema,
        sha256=sha256,
        rows=1,
        bytes=len(DATA),
        generated_at="2026-09-09T04:34:02Z",
    )


def serving(body=DATA, package=PACKAGE):
    """A transport that serves one dataset version."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("data.csv"):
            return httpx.Response(200, content=body, headers={"content-type": "text/csv"})
        return httpx.Response(200, content=package, headers={"content-type": "application/json"})

    return httpx.MockTransport(handler)


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


def test_landing_the_same_version_twice_is_allowed_and_replaces_it(tmp_path):
    """Re-landing is how a corrupted local copy is repaired; it must not error."""
    store = SnapshotStore(tmp_path)
    store.land(entry(), {"data.csv": DATA})

    store.land(entry(), {"data.csv": DATA, "datapackage.json": PACKAGE})

    assert (store.version_dir("pm_anchors", "v1-aaa") / "datapackage.json").exists()


def test_versions_are_returned_newest_last(tmp_path):
    store = SnapshotStore(tmp_path)
    for v in ("v20260901T000000Z-aaa", "v20260909T000000Z-bbb", "v20260905T000000Z-ccc"):
        store.land(entry(version=v), {"data.csv": DATA})

    assert store.versions("pm_anchors")[-1] == "v20260909T000000Z-bbb"


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
            subscription=Subscription(names=frozenset({"pm_anchors"}), schema_major=1),
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
            subscription=Subscription(names=frozenset({"pm_anchors"}), schema_major=1),
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
            subscription=Subscription(names=frozenset({"pm_anchors"}), schema_major=1),
        )

    assert report.landed == ["pm_anchors"]
    assert not store.has("stg_wsl_committees", "v1-aaa")


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
            subscription=Subscription(names=frozenset({"pm_anchors"}), schema_major=1),
        )

    assert report.incompatible == [("pm_anchors", "2.0.0")]
    assert report.failed_run
    assert not store.has("pm_anchors", "v1-aaa")


async def test_pull_records_a_corrupt_download_as_a_failure(tmp_path):
    store = SnapshotStore(tmp_path)

    async with httpx.AsyncClient(transport=serving(body=DATA + b"truncated")) as client:
        report = await pull(
            "https://usa-wa.exe.xyz:8000",
            [entry()],
            store,
            token="tok",
            client=client,
            subscription=Subscription(names=frozenset({"pm_anchors"}), schema_major=1),
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
            subscription=Subscription(names=frozenset({"broken", "pm_anchors"}), schema_major=1),
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
            subscription=Subscription(names=frozenset({"pm_anchors", "gone"}), schema_major=1),
        )

    assert report.missing == ["gone"]
    assert report.failed_run


async def test_the_default_subscription_takes_conformed_products_only(tmp_path):
    """Staging is the triage surface, not something PM applies."""
    store = SnapshotStore(tmp_path)
    catalog = [
        entry(name="persons", tier="conformed"),
        entry(name="stg_wsl_committees", tier="staging"),
    ]

    async with httpx.AsyncClient(transport=serving()) as client:
        report = await pull(
            "https://usa-wa.exe.xyz:8000",
            catalog,
            store,
            token="tok",
            client=client,
            subscription=Subscription(names=None, schema_major=1),
        )

    assert report.landed == ["persons"]
    assert report.missing == []


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
    assert meta["generated_at"] == "2026-09-09T04:34:02Z"


def test_the_recorded_digest_matches_the_file_it_describes(tmp_path):
    """Belt and braces: the metadata is only useful if it describes this copy."""
    store = SnapshotStore(tmp_path)

    path = store.land(entry(), {"data.csv": DATA})

    meta = json.loads((path / "snapshot.json").read_text())
    assert hashlib.sha256((path / "data.csv").read_bytes()).hexdigest() == meta["sha256"]


def test_land_refuses_an_entry_whose_name_escapes_the_store(tmp_path):
    """`parse_catalog` guards the catalog; this guards a hand-built entry."""
    store = SnapshotStore(tmp_path / "store")

    with pytest.raises(ValueError, match="unsafe"):
        store.land(entry(name="../escaped"), {"data.csv": DATA})

    assert not (tmp_path / "escaped").exists()


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
            subscription=Subscription(names=frozenset({"broken", "pm_anchors"}), schema_major=1),
        )

    assert report.landed == ["pm_anchors"]
    assert [name for name, _ in report.failed] == ["broken"]


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
            subscription=Subscription(names=frozenset({"pm_anchors"}), schema_major=1),
        )


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


async def test_a_genuinely_absent_package_still_lands_and_says_so(tmp_path, caplog):
    """404 is the publisher stating there is none; the digest covers data.csv."""
    store = SnapshotStore(tmp_path)

    with caplog.at_level(logging.WARNING, logger="src.core.ingestion.datasets"):
        report = await _pull_one(store, _serving_package_status(404))

    assert report.landed == ["pm_anchors"]
    assert not (store.version_dir("pm_anchors", "v1-aaa") / "datapackage.json").exists()
    assert any("datapackage.json" in r.getMessage() for r in caplog.records)


async def test_an_auth_failure_on_the_package_fails_the_dataset(tmp_path):
    """An expired token mid-pull is not a statement that no schema exists."""
    store = SnapshotStore(tmp_path)

    report = await _pull_one(store, _serving_package_status(403))

    assert [name for name, _ in report.failed] == ["pm_anchors"]


def test_a_crashed_run_leaves_no_version_behind_in_the_listing(tmp_path):
    """`.incoming-*` is a staging directory, not a stored version.

    Reported as one it inflates the count `prune` reasons about and would be
    handed to a consumer as a version that was never verified.
    """
    store = SnapshotStore(tmp_path)
    store.land(entry(), {"data.csv": DATA})
    (store.dataset_dir("pm_anchors") / ".incoming-v2-bbb").mkdir()

    assert store.versions("pm_anchors") == ["v1-aaa"]
