"""The seed script around the crosswalk loader (#495).

The loader is tested in `tests/core/ingestion/`; what is left here is the part a
script gets wrong: reading the export off disk, refusing one whose digest does
not match its manifest, and refusing to write when the report says a human has
to look first.
"""

import json

import pytest
import pytest_asyncio

from scripts.seed_producer_crosswalk import BlockedSeed, read_export, seed
from src.core.db import generate_id
from src.core.ingestion.crosswalk import AnchorFormatError

pytestmark = [pytest.mark.integration]


def _write_export(tmp_path, rows: str, *, digest: str | None = None):
    csv_path = tmp_path / "anchors.csv"
    csv_path.write_text(rows)
    manifest = {
        "exported_at": "2026-09-03T00:00:00Z",
        "counts": {"person": rows.count("\n") - 1},
        "sha256": digest if digest is not None else _sha256(csv_path),
        "encoding": "ulid-base32",
    }
    (tmp_path / "manifest.json").write_text(json.dumps(manifest))
    return tmp_path


def _sha256(path):
    import hashlib

    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest_asyncio.fixture(loop_scope="session")
async def db(db_pool):
    async with db_pool.acquire() as conn:
        tr = conn.transaction()
        await tr.start()
        try:
            yield conn
        finally:
            await tr.rollback()


async def _person(db) -> str:
    pid = generate_id()
    await db.execute("INSERT INTO people (id) VALUES ($1)", pid)
    return pid


def test_read_export_returns_the_anchors_and_the_manifest(tmp_path):
    d = _write_export(tmp_path, f"kind,usa_wa_id,pm_id\nperson,{generate_id()},{generate_id()}\n")

    anchors, manifest = read_export(d)

    assert len(anchors) == 1
    assert manifest["encoding"] == "ulid-base32"


def test_read_export_refuses_a_digest_that_does_not_match(tmp_path):
    """A truncated copy parses as a shorter, valid export — the digest is the only guard."""
    d = _write_export(
        tmp_path,
        f"kind,usa_wa_id,pm_id\nperson,{generate_id()},{generate_id()}\n",
        digest="0" * 64,
    )

    with pytest.raises(AnchorFormatError, match="digest"):
        read_export(d)


async def test_seed_writes_nothing_when_the_report_blocks(db, tmp_path):
    """A blocking report is not advisory: an unresolvable anchor stops the run."""
    d = _write_export(tmp_path, f"kind,usa_wa_id,pm_id\nperson,{generate_id()},{generate_id()}\n")

    with pytest.raises(BlockedSeed):
        await seed(db, d, source="usa_wa", execute=True)

    assert await db.fetchval("SELECT count(*) FROM producer_crosswalk") == 0


async def test_seed_writes_a_clean_export(db, tmp_path):
    d = _write_export(
        tmp_path, f"kind,usa_wa_id,pm_id\nperson,{generate_id()},{await _person(db)}\n"
    )

    report = await seed(db, d, source="usa_wa", execute=True)

    assert report.counts == {"live": 1}
    assert await db.fetchval("SELECT count(*) FROM producer_crosswalk") == 1


async def test_seed_records_the_export_provenance_on_every_row(db, tmp_path):
    """Which export a row came from is the first question a triage pass asks."""
    d = _write_export(
        tmp_path, f"kind,usa_wa_id,pm_id\nperson,{generate_id()},{await _person(db)}\n"
    )

    await seed(db, d, source="usa_wa", execute=True)

    row = await db.fetchrow("SELECT export_generated_at, export_sha256 FROM producer_crosswalk")
    assert row["export_generated_at"].year == 2026
    assert row["export_sha256"] == _sha256(d / "anchors.csv")


async def test_a_blocking_report_still_returns_in_dry_run(db, tmp_path):
    """Dry run's whole job is to show the blocking diff, so it must not raise."""
    d = _write_export(tmp_path, f"kind,usa_wa_id,pm_id\nperson,{generate_id()},{generate_id()}\n")

    report = await seed(db, d, source="usa_wa", execute=False)

    assert report.is_blocking
    assert report.counts == {"missing": 1}


def test_read_export_names_a_manifest_key_it_cannot_find(tmp_path):
    """Every other malformed-input path here says what is wrong; this one said `KeyError`."""
    (tmp_path / "anchors.csv").write_text("kind,usa_wa_id,pm_id\n")
    (tmp_path / "manifest.json").write_text('{"exported_at": "2026-09-03T00:00:00Z"}')

    with pytest.raises(AnchorFormatError, match="sha256"):
        read_export(tmp_path)


def test_read_export_reads_a_pulled_snapshot_directory(tmp_path):
    """The puller (#496) writes `data.csv` + `snapshot.json`, not the VM-file shape.

    Teaching the seed to read what the store lands is what makes the puller
    actually replace the hand-staged fetch, rather than sitting beside it.
    """
    import hashlib
    import json as _json

    rows = f"kind,usa_wa_id,pm_id\nperson,{generate_id()},{generate_id()}\n"
    (tmp_path / "data.csv").write_text(rows)
    (tmp_path / "snapshot.json").write_text(
        _json.dumps(
            {
                "name": "pm_anchors",
                "version": "v20260909T043402Z-4f46dd",
                "sha256": hashlib.sha256(rows.encode()).hexdigest(),
                "generated_at": "2026-09-09T04:34:02Z",
            }
        )
    )

    anchors, manifest = read_export(tmp_path)

    assert len(anchors) == 1
    assert manifest["exported_at"] == "2026-09-09T04:34:02Z"


def test_read_export_verifies_a_pulled_snapshot_against_its_recorded_digest(tmp_path):
    """The store's own record is the guard once the catalog is out of reach."""
    import json as _json

    (tmp_path / "data.csv").write_text("kind,usa_wa_id,pm_id\n")
    (tmp_path / "snapshot.json").write_text(
        _json.dumps({"sha256": "0" * 64, "generated_at": "2026-09-09T04:34:02Z"})
    )

    with pytest.raises(AnchorFormatError, match="digest"):
        read_export(tmp_path)
