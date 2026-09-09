"""The puller's operational surface (#496).

A nightly timer's only voice is its exit code, so what is tested here is mostly
which outcomes are allowed to look like success.
"""

import hashlib

import httpx
import pytest

from scripts.pull_datasets import build_subscription, main, run
from src.core.ingestion.datasets import CatalogEntry, CatalogError, SnapshotStore

DATA = b"kind,usa_wa_id,pm_id\n"
DIGEST = hashlib.sha256(DATA).hexdigest()

CATALOG = {
    "datasets": [
        {
            "name": "persons",
            "tier": "conformed",
            "latest_version": "v1-aaa",
            "schema_version": "1.5.0",
            "hash": f"sha256:{DIGEST}",
            "rows": 1,
            "bytes": len(DATA),
        },
        {
            "name": "stg_wsl_committees",
            "tier": "staging",
            "latest_version": "v1-bbb",
            "schema_version": "1.4.0",
            "hash": f"sha256:{DIGEST}",
            "rows": 1,
            "bytes": len(DATA),
        },
    ]
}


def _client(catalog=CATALOG, data=DATA):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("catalog.json"):
            return httpx.Response(200, json=catalog)
        if request.url.path.endswith("data.csv"):
            return httpx.Response(200, content=data, headers={"content-type": "text/csv"})
        return httpx.Response(404)

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def test_no_dataset_flag_means_the_conformed_tier():
    assert build_subscription([], schema_major=1).names is None


def test_named_datasets_replace_the_default_rather_than_extend_it():
    """Naming staging must not silently keep pulling every conformed product too."""
    sub = build_subscription(["stg_wsl_committees"], schema_major=1)

    assert sub.names == frozenset({"stg_wsl_committees"})


async def test_a_clean_pull_lands_the_conformed_tier_and_exits_zero(tmp_path):
    store = SnapshotStore(tmp_path)

    async with _client() as client:
        report = await run(
            "https://usa-wa.exe.xyz:8000",
            token="tok",
            store=store,
            subscription=build_subscription([], schema_major=1),
            keep=3,
            client=client,
        )

    assert report.landed == ["persons"]
    assert not report.failed_run
    assert store.has("persons", "v1-aaa")


async def test_a_corrupt_download_makes_the_run_fail(tmp_path):
    """The timer's only signal is the exit code; a bad digest must reach it."""
    async with _client(data=DATA + b"extra") as client:
        report = await run(
            "https://usa-wa.exe.xyz:8000",
            token="tok",
            store=SnapshotStore(tmp_path),
            subscription=build_subscription([], schema_major=1),
            keep=3,
            client=client,
        )

    assert report.failed_run


async def test_pruning_spares_the_version_this_run_landed(tmp_path):
    """`--keep 1` must not delete the snapshot the run just fetched."""
    store = SnapshotStore(tmp_path)
    for old in ("v0-000", "v0-111"):
        store.land(
            CatalogEntry("persons", "conformed", old, "1.5.0", DIGEST, 1, len(DATA), ""),
            {"data.csv": DATA},
        )

    async with _client() as client:
        await run(
            "https://usa-wa.exe.xyz:8000",
            token="tok",
            store=store,
            subscription=build_subscription([], schema_major=1),
            keep=1,
            client=client,
        )

    assert store.has("persons", "v1-aaa")
    assert store.versions("persons") == ["v1-aaa"]


def test_a_missing_token_fails_before_any_request(monkeypatch, tmp_path):
    """An unset token would otherwise surface as an unexplained login redirect."""
    monkeypatch.delenv("USA_WA_TOKEN", raising=False)

    with pytest.raises(SystemExit) as exc:
        main(["--root", str(tmp_path)])

    assert exc.value.code == 2


def test_an_unreadable_catalog_is_reported_as_a_sentence_not_a_traceback(
    monkeypatch, tmp_path, capsys
):
    """The whole module exists to make the operator look at the token.

    Ending in a stack trace buries the one sentence that says so; the exit code
    is right either way, so nothing else catches this.
    """

    async def refuse(*args, **kwargs):
        raise CatalogError(
            "https://usa-wa.exe.xyz:8000/datasets/catalog.json: authentication failed"
        )

    monkeypatch.setenv("USA_WA_TOKEN", "tok")
    monkeypatch.setattr("scripts.pull_datasets.fetch_catalog", refuse)

    code = main(["--root", str(tmp_path)])

    # Read from stdout rather than caplog: `main` calls `configure_logging()`,
    # which replaces the root handlers caplog installed. Stdout is what the
    # journal records anyway.
    logged = capsys.readouterr().out

    assert code == 1
    assert "authentication failed" in logged
    assert "Traceback" not in logged
