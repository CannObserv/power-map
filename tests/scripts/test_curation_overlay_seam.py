"""#498's acceptance, end to end across every seam the overlay crosses.

1. Editing a producer-owned field in the admin, then applying a snapshot that
   reasserts the producer value, leaves the curator's value standing.
2. Removing the overlay row lets the producer value flow back on the next apply.

Each half is tested elsewhere alone — the edit hook, the export spec, the
staging filter, the mart join, the diff. What only a crossing test can catch is
a disagreement between them: an export column the staging model does not read,
a pin keyed on an id the mart joins on something else, an archived pin still
reaching the diff. So this drives the real admin route, exports through the
real export (reading the same rollback connection), builds with dbt over the
fixture snapshot — which keeps asserting the producer's name — and diffs the
result against the database the edit landed in.
"""

import shutil
from functools import partial

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

pytest.importorskip("dbt.adapters.duckdb")

from scripts.build_desired_state import build  # noqa: E402
from scripts.export_pm_tables import fetch_rows  # noqa: E402
from scripts.export_pm_tables import run as export  # noqa: E402
from src.api.admin.deps import get_db  # noqa: E402
from src.api.main import app  # noqa: E402
from src.core.curation_overlay import active_pin  # noqa: E402
from src.core.db import generate_id  # noqa: E402
from src.core.ingestion.applier import DesiredState, diff_desired  # noqa: E402
from src.core.ingestion.applier_pg import PostgresLiveStore  # noqa: E402
from src.core.ingestion.crosswalk import PRODUCER_SOURCE  # noqa: E402
from src.core.ingestion.mapping import load_manifest  # noqa: E402
from tests.core.ingestion.mapping.conftest import FIXTURE_STORE, P1  # noqa: E402

pytestmark = pytest.mark.integration

PRODUCED = "Peter Abbarno"  # the fixture snapshot's name for P1, reasserted every build
CURATED = "Peter J. Abbarno"
AUTH = {"X-ExeDev-UserID": "seam-curator", "X-ExeDev-Email": "seam@example.org"}
HX = {**AUTH, "HX-Request": "true"}
ENTRY = f"desired_person_names:{P1}|legal"


@pytest_asyncio.fixture(loop_scope="session")
async def db(db_pool):
    async with db_pool.acquire() as conn:
        tr = conn.transaction()
        await tr.start()
        try:
            yield conn
        finally:
            await tr.rollback()


@pytest_asyncio.fixture(loop_scope="session")
async def client(db):
    async def _get_db_override():
        yield db

    app.dependency_overrides[get_db] = _get_db_override
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c
    app.dependency_overrides.pop(get_db, None)


@pytest_asyncio.fixture(loop_scope="session")
async def anchored(db) -> tuple[str, str]:
    """A PM person holding the producer's name, anchored to the fixture's P1."""
    pid, nid = generate_id(), generate_id()
    await db.execute("INSERT INTO people (id) VALUES ($1)", pid)
    await db.execute(
        "INSERT INTO person_names (id, person_id, name, name_type, is_canonical)"
        " VALUES ($1, $2, $3, 'legal', TRUE)",
        nid,
        pid,
        PRODUCED,
    )
    await db.execute(
        "INSERT INTO producer_crosswalk"
        " (id, source, kind, producer_id, exported_pm_id, pm_id, resolution)"
        " VALUES ($1, $2, 'person', $3, $4, $4, 'live')",
        generate_id(),
        PRODUCER_SOURCE,
        P1,
        pid,
    )
    return pid, nid


async def _apply_dry(db, tmp_path, run: str):
    """Export this connection's PM tables, build over the fixture snapshot, diff."""
    root, out = tmp_path / f"store-{run}", tmp_path / f"desired-{run}"
    shutil.copytree(FIXTURE_STORE, root)
    await export(partial(fetch_rows, db), root=root)
    assert build(root, out, tmp_path / f"{run}.duckdb") == 0
    manifest = load_manifest()
    state = DesiredState.load(out, manifest)
    diff = await diff_desired(state, manifest, PostgresLiveStore(db))
    desired = {r["producer_id"]: r["name"] for r in state.tables["desired_person_names"]}
    return desired[P1], {e.entry_id: e for e in diff.entries}[ENTRY]


async def test_a_curators_edit_survives_the_snapshot_and_an_unpin_lets_the_producer_back(
    client, db, anchored, tmp_path
):
    pid, nid = anchored

    # The curator corrects the producer-owned legal name in the admin.
    r = await client.post(
        f"/admin/people/{pid}/names/{nid}/edit-row/",
        data={"name": CURATED, "name_type": "legal", "is_canonical": "true"},
        headers=HX,
    )
    assert r.status_code == 200
    assert (await active_pin(db, "person", pid, "name")).value == CURATED

    # 1. The snapshot still says PRODUCED; the curator's value stands — no write.
    desired, entry = await _apply_dry(db, tmp_path, "pinned")
    assert desired == CURATED
    assert entry.kind == "noop"

    # The curator lets the producer's value back.
    r = await client.post(f"/admin/_overlay/person/{pid}/name/unpin/", headers=HX)
    assert r.status_code == 200

    # 2. The next apply carries the producer's value back onto the row.
    desired, entry = await _apply_dry(db, tmp_path, "unpinned")
    assert desired == PRODUCED
    assert entry.kind == "update"
    assert entry.changes == {"name": (CURATED, PRODUCED)}
