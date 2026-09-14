"""The seed script around the crosswalk loader (#495).

The loader is tested in `tests/core/ingestion/`; what is left here is the part a
script gets wrong: reading the export off disk, refusing one whose digest does
not match its manifest, and refusing to write when the report says a human has
to look first.
"""

import hashlib
import json
import logging
from datetime import date

import asyncpg
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
    d = _write_export(
        tmp_path, f"kind,usa_wa_id,pm_id,span_key\nperson,{generate_id()},{generate_id()},\n"
    )

    anchors, manifest = read_export(d)

    assert len(anchors) == 1
    assert manifest["encoding"] == "ulid-base32"


def test_read_export_refuses_a_digest_that_does_not_match(tmp_path):
    """A truncated copy parses as a shorter, valid export — the digest is the only guard."""
    d = _write_export(
        tmp_path,
        f"kind,usa_wa_id,pm_id,span_key\nperson,{generate_id()},{generate_id()},\n",
        digest="0" * 64,
    )

    with pytest.raises(AnchorFormatError, match="digest"):
        read_export(d)


async def test_seed_writes_nothing_when_the_report_blocks(db, tmp_path):
    """A blocking report is not advisory: an unresolvable anchor stops the run."""
    d = _write_export(
        tmp_path, f"kind,usa_wa_id,pm_id,span_key\nperson,{generate_id()},{generate_id()},\n"
    )

    with pytest.raises(BlockedSeed):
        await seed(db, d, source="usa_wa", execute=True)

    assert await db.fetchval("SELECT count(*) FROM producer_crosswalk") == 0


async def test_seed_writes_a_clean_export(db, tmp_path):
    d = _write_export(
        tmp_path, f"kind,usa_wa_id,pm_id,span_key\nperson,{generate_id()},{await _person(db)},\n"
    )

    report = await seed(db, d, source="usa_wa", execute=True)

    assert report.counts == {"live": 1}
    assert await db.fetchval("SELECT count(*) FROM producer_crosswalk") == 1


async def test_seed_records_the_export_provenance_on_every_row(db, tmp_path):
    """Which export a row came from is the first question a triage pass asks."""
    d = _write_export(
        tmp_path, f"kind,usa_wa_id,pm_id,span_key\nperson,{generate_id()},{await _person(db)},\n"
    )

    await seed(db, d, source="usa_wa", execute=True)

    row = await db.fetchrow("SELECT export_generated_at, export_sha256 FROM producer_crosswalk")
    assert row["export_generated_at"].year == 2026
    assert row["export_sha256"] == _sha256(d / "anchors.csv")


async def test_a_blocking_report_still_returns_in_dry_run(db, tmp_path):
    """Dry run's whole job is to show the blocking diff, so it must not raise."""
    d = _write_export(
        tmp_path, f"kind,usa_wa_id,pm_id,span_key\nperson,{generate_id()},{generate_id()},\n"
    )

    report = await seed(db, d, source="usa_wa", execute=False)

    assert report.is_blocking
    assert report.counts == {"missing": 1}


def test_read_export_names_a_manifest_key_it_cannot_find(tmp_path):
    """Every other malformed-input path here says what is wrong; this one said `KeyError`."""
    (tmp_path / "anchors.csv").write_text("kind,usa_wa_id,pm_id,span_key\n")
    (tmp_path / "manifest.json").write_text('{"exported_at": "2026-09-03T00:00:00Z"}')

    with pytest.raises(AnchorFormatError, match="sha256"):
        read_export(tmp_path)


def test_read_export_reads_a_pulled_snapshot_directory(tmp_path):
    """The puller (#496) writes `data.csv` + `snapshot.json`, not the VM-file shape.

    Teaching the seed to read what the store lands is what makes the puller
    actually replace the hand-staged fetch, rather than sitting beside it.
    """
    rows = f"kind,usa_wa_id,pm_id,span_key\nperson,{generate_id()},{generate_id()},\n"
    (tmp_path / "data.csv").write_text(rows)
    (tmp_path / "snapshot.json").write_text(
        json.dumps(
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
    (tmp_path / "data.csv").write_text("kind,usa_wa_id,pm_id,span_key\n")
    (tmp_path / "snapshot.json").write_text(
        json.dumps({"sha256": "0" * 64, "generated_at": "2026-09-09T04:34:02Z"})
    )

    with pytest.raises(AnchorFormatError, match="digest"):
        read_export(tmp_path)


async def test_a_keyed_pulled_snapshot_re_keys_its_assignments_end_to_end(db, tmp_path, caplog):
    """#525, through the script: a `pm_anchors` snapshot as the puller lands it
    (`data.csv` + `snapshot.json`, four columns, empty keys written quoted as
    usa-wa does) re-keys an anchor the keyless seed wrote under its ULID, leaves an
    unpublished assignment on its ULID, and says both in the report."""
    org, role = generate_id(), generate_id()
    await db.execute("INSERT INTO organizations (id) VALUES ($1)", org)
    await db.execute(
        "INSERT INTO roles (id, organization_id, title) VALUES ($1, $2, 'Member')", role, org
    )
    person = await _person(db)
    published, unpublished = generate_id(), generate_id()
    for ra, start in ((published, date(2025, 1, 13)), (unpublished, date(2013, 1, 14))):
        await db.execute(
            "INSERT INTO role_assignments (id, person_id, role_id, start_date)"
            " VALUES ($1, $2, $3, $4)",
            ra,
            person,
            role,
            start,
        )
    keyed_id, unkeyed_id = generate_id(), generate_id()
    # What the 2026-09-09 seed left: the anchor keyed on its ULID.
    await db.execute(
        "INSERT INTO producer_crosswalk (id, source, kind, producer_id, exported_producer_id,"
        " exported_pm_id, pm_id, resolution, export_sha256)"
        " VALUES ($1, 'usa_wa', 'assignment', $2, $2, $3, $3, 'live', 'sha256:old')",
        generate_id(),
        keyed_id,
        published,
    )
    span = f"{generate_id()}|committee-member-role:31640|committee|31640|2025-26"
    rows = (
        "kind,usa_wa_id,pm_id,span_key\n"
        f"assignment,{keyed_id},{published},{span}\n"
        f'assignment,{unkeyed_id},{unpublished},""\n'
    )
    (tmp_path / "data.csv").write_text(rows)
    (tmp_path / "snapshot.json").write_text(
        json.dumps(
            {
                "name": "pm_anchors",
                "version": "v20260913T033950Z-13c981",
                "sha256": hashlib.sha256(rows.encode()).hexdigest(),
                "generated_at": "2026-09-13T03:39:50Z",
            }
        )
    )

    with caplog.at_level(logging.INFO, logger="scripts.seed_producer_crosswalk"):
        report = await seed(db, tmp_path, source="usa_wa", execute=True)

    keys = dict(
        await db.fetch(
            "SELECT exported_producer_id, producer_id FROM producer_crosswalk"
            " WHERE source = 'usa_wa' AND kind = 'assignment'"
            "   AND exported_producer_id = ANY($1)",
            [keyed_id, unkeyed_id],
        )
    )
    assert keys == {keyed_id: span, unkeyed_id: unkeyed_id}
    assert (report.rekeyed, report.unkeyed) == (1, 1)
    logged = caplog.text
    assert "re-keyed" in logged and "unkeyed" in logged


async def test_a_key_another_row_holds_passes_the_dry_run_and_aborts_execute(db, tmp_path):
    """CR 1: only a write can find a key another row already holds. The dry run
    reports clean; `--execute` fails the unique index on `(source, kind, producer_id)`
    and its transaction rolls back whole. The holder here is a row the applier
    minted — no exported id, so the seed's match misses it."""
    person, usa_wa_id, minted = await _person(db), generate_id(), generate_id()
    await db.execute(
        "INSERT INTO producer_crosswalk"
        " (id, source, kind, producer_id, exported_pm_id, pm_id, resolution)"
        " VALUES ($1, 'usa_wa', 'person', $2, $3, $3, 'live')",
        minted,
        usa_wa_id,
        person,
    )
    d = _write_export(tmp_path, f"kind,usa_wa_id,pm_id,span_key\nperson,{usa_wa_id},{person},\n")

    assert not (await seed(db, d, source="usa_wa", execute=False)).is_blocking
    with pytest.raises(asyncpg.UniqueViolationError, match="uq_producer_crosswalk_producer"):
        await seed(db, d, source="usa_wa", execute=True)

    rows = await db.fetch(
        "SELECT id, exported_producer_id FROM producer_crosswalk"
        " WHERE source = 'usa_wa' AND kind = 'person' AND producer_id = $1",
        usa_wa_id,
    )
    assert [tuple(r) for r in rows] == [(minted, None)]
