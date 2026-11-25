"""Integration tests for scripts/classify_suggestions.py --source rules (#877).

The rules source writes a ``classification_suggestions`` row only when an
org's identifier-implied kind changed. Dry run by default; ``--execute``
writes. Requires TEST_DATABASE_URL + a schema-applied DB.
"""

import pytest
import pytest_asyncio

from scripts.classify_suggestions import classify
from src.core.db import generate_id

pytestmark = [pytest.mark.integration]


@pytest_asyncio.fixture(loop_scope="session")
async def db(db_pool):
    async with db_pool.acquire() as conn:
        tr = conn.transaction()
        await tr.start()
        try:
            yield conn
        finally:
            await tr.rollback()


async def _org(db, *slugs: str) -> str:
    oid = generate_id()
    await db.execute("INSERT INTO organizations (id) VALUES ($1)", oid)
    for slug in slugs:
        type_id = await db.fetchval("SELECT id FROM entity_identifier_types WHERE slug=$1", slug)
        await db.execute(
            "INSERT INTO identifiers (id, entity_id, entity_identifier_type_id, value)"
            " VALUES ($1,$2,$3,$4)",
            generate_id(),
            oid,
            type_id,
            f"v-{generate_id()[-8:]}",
        )
    return oid


async def _rules_rows(db, org_id) -> list[str]:
    rows = await db.fetch(
        "SELECT value FROM classification_suggestions"
        " WHERE entity_id=$1 AND source='rules' ORDER BY created_at, id",
        org_id,
    )
    return [r["value"] for r in rows]


async def test_dry_run_reports_without_writing(db):
    party = await _org(db, "org_wa_party")
    report = await classify(db, source="rules", execute=False)
    assert party in {c.org_id for c in report.changes}
    assert report.counts["political_party"] >= 1
    assert report.written == 0
    assert await _rules_rows(db, party) == []


async def test_execute_writes_once_and_reruns_clean(db):
    leg = await _org(db, "org_wa_legislature_committee_id")
    biz = await _org(db, "org_wslcb", "org_ubi")
    plain = await _org(db, "org_ubi")

    report = await classify(db, source="rules", execute=True)
    assert report.written == len(report.changes) >= 2
    assert await _rules_rows(db, leg) == ["legislature"]
    assert await _rules_rows(db, biz) == ["business"]
    assert await _rules_rows(db, plain) == []

    again = await classify(db, source="rules", execute=True)
    assert again.changes == []
    assert again.written == 0


async def test_unknown_source_is_refused(db):
    with pytest.raises(ValueError, match="source"):
        await classify(db, source="jev", execute=False)
