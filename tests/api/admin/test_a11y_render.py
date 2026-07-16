"""Rendered-DOM a11y sweep over every admin GET route (GH #246).

Authoritative complement to the static template lint (``test_aria_labels.py``):
fetches each admin GET route through the lifespan-less rollback client (#288),
so the checks in ``tests.api.admin.a11y`` run against **resolved output** —
includes expanded, ids materialized — closing the three #244 blind spots.

Coverage is programmatic: routes are enumerated from ``app.routes`` (no
hand-picked "representative views"), path params filled from one seeded entity
per type. A new admin GET route is swept automatically; if its params can't be
filled the test fails loudly rather than silently skipping.

Checks per route:

- response is 200 (the sweep must actually render every route);
- every input/select/textarea resolves an accessible name via real ancestry;
- on full-page documents only: every ``<label for>`` / ``aria-labelledby`` /
  ``aria-describedby`` reference resolves to an existing id. HTMX fragments are
  exempt from the id-resolution check — they may legitimately reference ids
  rendered by the parent page (that is #244 blind spot 2).
"""

import hashlib

import pytest
import pytest_asyncio
from fastapi.routing import APIRoute
from httpx import ASGITransport, AsyncClient

from src.api.admin.deps import get_db
from src.api.main import app
from src.core.db import generate_id
from tests.api.admin.a11y import (
    controls_missing_accessible_name,
    count_controls,
    dangling_id_refs,
    is_full_document,
)

pytestmark = [
    pytest.mark.integration,
]

AUTH_HEADERS = {
    "X-ExeDev-UserID": "usr_test",
    "X-ExeDev-Email": "admin@test.com",
}

_EMBED_MODEL_ID = "pyannote-community-1-embed"
_EMBED_TABLE = "person_embeddings_pyannote_community_1_embed"
_EMBED_DIM = 256

ADMIN_GET_PATHS = sorted(
    {
        route.path
        for route in app.routes
        if isinstance(route, APIRoute)
        and route.path.startswith("/admin")
        and "GET" in route.methods
    }
)

# Routes whose handlers require query params to render a 200. Everything not
# listed here must render with path params alone.
_QUERY = {
    "/admin/orgs/{org_id}/addresses/country-format/": "?country=US",
    "/admin/people/{person_id}/addresses/country-format/": "?country=US",
    "/admin/jurisdictions/{jurisdiction_id}/addresses/country-format/": "?country=US",
    "/admin/_dup-badge/orgs/": "?variant=card",
    "/admin/_dup-badge/people/": "?variant=card",
    "/admin/orgs/{entity_id}/contacts/new-row/": "?contact_type=phone",
    "/admin/people/{entity_id}/contacts/new-row/": "?contact_type=phone",
    "/admin/jurisdictions/{entity_id}/contacts/new-row/": "?contact_type=phone",
}

# Aggregate control-coverage guard. Each rendered route adds its control count
# to this accumulator; a module-teardown fixture asserts the total clears a
# floor, but only when the *full* sweep ran (a filtered `-k` subset skips the
# check). Catches a mass regression — a form that silently stops rendering
# controls would still pass every per-route check vacuously (#246 CR). Floor is
# well below the ~345 currently rendered, so it flags a collapse, not drift.
#
# xdist caveat: these are process-global counters. Under `pytest-xdist` (not
# used yet — see #288) each worker runs only a shard, so `_routes_executed`
# never reaches len(ADMIN_GET_PATHS) on any worker and the floor check silently
# no-ops. Before enabling xdist, move the aggregate to a cross-worker mechanism
# (e.g. a `pytest_sessionfinish` hook) or the backstop disappears without warning.
_MIN_TOTAL_CONTROLS = 250
_control_total = 0
_routes_executed = 0


# Routes that only render their partial for an HTMX request. Without HX-Request
# they either 400 (dup badges) or 303-redirect to a list page (api-key detail).
# The sweep must exercise the *partial*, not the redirect target, so send the
# header. Redirect-following is OFF (see the client fixture) precisely so a route
# that 3xx's here fails the 200 gate loudly instead of silently re-testing
# wherever it points.
_EXTRA_HEADERS = {
    "/admin/_dup-badge/orgs/": {"HX-Request": "true"},
    "/admin/_dup-badge/people/": {"HX-Request": "true"},
    "/admin/settings/api-keys/{key_id}/detail/": {"HX-Request": "true"},
}


@pytest_asyncio.fixture(scope="module", loop_scope="session")
async def db(db_pool):
    """Module-scoped connection in one rolled-back transaction.

    The sweep is read-only (GETs), so all ~150 parametrized cases share one
    seed + connection instead of re-seeding per test."""
    async with db_pool.acquire() as conn:
        tr = conn.transaction()
        await tr.start()
        try:
            yield conn
        finally:
            await tr.rollback()


@pytest_asyncio.fixture(scope="module", loop_scope="session")
async def client(db):
    async def _get_db_override():
        yield db

    app.dependency_overrides[get_db] = _get_db_override
    # follow_redirects=False on purpose: a route that 3xx's must fail the 200
    # gate, not silently pass by following the redirect to some other page
    # (which masks HTMX-only partials — a 303 to the list page still 200s).
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test", follow_redirects=False
    ) as c:
        yield c
    app.dependency_overrides.pop(get_db, None)


async def _seed_subresources(db, entity_type: str, entity_id: str, ident_type_id: str) -> dict:
    """Contact, link, identifier, address rows for one entity; returns their ids."""
    link_type_id = await db.fetchval("SELECT id FROM link_types WHERE is_social = FALSE LIMIT 1")
    out = {
        "contact_id": generate_id(),
        "link_id": generate_id(),
        "ident_id": generate_id(),
        "addr_id": generate_id(),
    }
    await db.execute(
        "INSERT INTO contact_methods (id, entity_type, entity_id, contact_type, value)"
        " VALUES ($1, $2, $3, 'phone', '+13605551234')",
        out["contact_id"],
        entity_type,
        entity_id,
    )
    await db.execute(
        "INSERT INTO links (id, entity_type, entity_id, url, link_type_id)"
        " VALUES ($1, $2, $3, 'https://example.com/', $4)",
        out["link_id"],
        entity_type,
        entity_id,
        link_type_id,
    )
    await db.execute(
        "INSERT INTO identifiers (id, entity_id, entity_identifier_type_id, value)"
        " VALUES ($1, $2, $3, 'A11Y-1')",
        out["ident_id"],
        entity_id,
        ident_type_id,
    )
    address_id = generate_id()
    await db.execute(
        "INSERT INTO addresses (id, address_line_1, city, postal_code, country)"
        " VALUES ($1, '1 Main St', 'Olympia', '98501', 'US')",
        address_id,
    )
    await db.execute(
        "INSERT INTO entity_addresses (id, entity_type, entity_id, address_id, address_type)"
        " VALUES ($1, $2, $3, $4, 'mailing')",
        out["addr_id"],
        entity_type,
        entity_id,
        address_id,
    )
    return out


@pytest_asyncio.fixture(scope="module", loop_scope="session")
async def seed(db):
    """One entity per type plus one row per sub-resource — enough to fill every
    path param in ADMIN_GET_PATHS."""
    s: dict = {}

    ident_types = {
        r["entity_type"]: r["id"]
        for r in await db.fetch(
            "SELECT DISTINCT ON (entity_type) entity_type, id"
            " FROM entity_identifier_types ORDER BY entity_type, id"
        )
    }

    # Orgs: two (merge preview), canonical names, acronym, two roles (role merge).
    s["org_id"], s["org2_id"] = generate_id(), generate_id()
    for oid, name in ((s["org_id"], "A11y Org One"), (s["org2_id"], "A11y Org Two")):
        await db.execute("INSERT INTO organizations (id) VALUES ($1)", oid)
        await db.execute(
            "INSERT INTO organization_names (id, organization_id, name, is_canonical)"
            " VALUES ($1, $2, $3, TRUE)",
            generate_id(),
            oid,
            name,
        )
    s["org_name_id"] = generate_id()
    await db.execute(
        "INSERT INTO organization_names (id, organization_id, name, is_canonical)"
        " VALUES ($1, $2, 'A11y Org Alias', FALSE)",
        s["org_name_id"],
        s["org_id"],
    )
    s["acronym_id"] = generate_id()
    await db.execute(
        "INSERT INTO organization_acronyms (id, organization_id, acronym, is_canonical)"
        " VALUES ($1, $2, 'AOO', TRUE)",
        s["acronym_id"],
        s["org_id"],
    )
    s["role_id"], s["role2_id"] = generate_id(), generate_id()
    for rid, title in ((s["role_id"], "Director"), (s["role2_id"], "Deputy Director")):
        await db.execute(
            "INSERT INTO roles (id, organization_id, title) VALUES ($1, $2, $3)",
            rid,
            s["org_id"],
            title,
        )
    org_sub = await _seed_subresources(db, "organization", s["org_id"], ident_types["organization"])
    s["org_sub"] = org_sub
    org_event_type = await db.fetchval("SELECT id FROM entity_event_types WHERE slug = 'founded'")
    s["org_event_id"] = generate_id()
    await db.execute(
        "INSERT INTO entity_events (id, entity_type, entity_id, event_type_id, event_year)"
        " VALUES ($1, 'organization', $2, $3, 1999)",
        s["org_event_id"],
        s["org_id"],
        org_event_type,
    )

    # People: two (merge preview), canonical + alias names.
    s["person_id"], s["person2_id"] = generate_id(), generate_id()
    for pid, name in ((s["person_id"], "A11y Person One"), (s["person2_id"], "A11y Person Two")):
        await db.execute("INSERT INTO people (id) VALUES ($1)", pid)
        await db.execute(
            "INSERT INTO person_names (id, person_id, name, is_canonical)"
            " VALUES ($1, $2, $3, TRUE)",
            generate_id(),
            pid,
            name,
        )
    s["person_name_id"] = generate_id()
    await db.execute(
        "INSERT INTO person_names (id, person_id, name, is_canonical)"
        " VALUES ($1, $2, 'A11y P. Alias', FALSE)",
        s["person_name_id"],
        s["person_id"],
    )
    person_sub = await _seed_subresources(db, "person", s["person_id"], ident_types["person"])
    s["person_sub"] = person_sub
    person_event_type = await db.fetchval("SELECT id FROM entity_event_types WHERE slug = 'birth'")
    s["person_event_id"] = generate_id()
    await db.execute(
        "INSERT INTO entity_events (id, entity_type, entity_id, event_type_id, event_year)"
        " VALUES ($1, 'person', $2, $3, 1970)",
        s["person_event_id"],
        s["person_id"],
        person_event_type,
    )

    # Assignment shared by /roles/{}/assignments, /people/{}/assignments, /role-assignments/{}.
    s["assignment_id"] = generate_id()
    await db.execute(
        "INSERT INTO role_assignments (id, person_id, role_id) VALUES ($1, $2, $3)",
        s["assignment_id"],
        s["person_id"],
        s["role_id"],
    )

    # Jurisdictions: two + one relationship.
    jur_type = await db.fetchval("SELECT id FROM jurisdiction_types WHERE slug = 'state'")
    rel_type = await db.fetchval(
        "SELECT id FROM jurisdiction_relationship_types WHERE slug = 'governs'"
    )
    s["jurisdiction_id"], s["jurisdiction2_id"] = generate_id(), generate_id()
    for jid, slug, name in (
        (s["jurisdiction_id"], "a11y-state", "A11y State"),
        (s["jurisdiction2_id"], "a11y-county", "A11y County"),
    ):
        await db.execute(
            "INSERT INTO jurisdictions (id, slug, name, type_id) VALUES ($1, $2, $3, $4)",
            jid,
            slug,
            name,
            jur_type,
        )
    s["rel_id"] = generate_id()
    await db.execute(
        "INSERT INTO jurisdiction_relationships (id, from_id, to_id, rel_type_id)"
        " VALUES ($1, $2, $3, $4)",
        s["rel_id"],
        s["jurisdiction_id"],
        s["jurisdiction2_id"],
        rel_type,
    )
    jur_sub = await _seed_subresources(
        db, "jurisdiction", s["jurisdiction_id"], ident_types["jurisdiction"]
    )
    s["jur_sub"] = jur_sub

    # Settings: app user + API key; type-catalog rows come from reference seeds.
    # The API-key routes scope keys to the authenticated admin (provision_app_user),
    # so the app_users row must carry the exe.dev user id from AUTH_HEADERS.
    user_id = AUTH_HEADERS["X-ExeDev-UserID"]
    s["key_id"] = generate_id()
    raw = "pm_a11ysweepkey0000000000000000"
    await db.execute("INSERT INTO app_users (id, email) VALUES ($1, 'a11y@test.com')", user_id)
    await db.execute(
        "INSERT INTO api_keys (id, user_id, label, key_prefix, key_hash)"
        " VALUES ($1, $2, 'A11y Sweep Key', $3, $4)",
        s["key_id"],
        user_id,
        raw[:8],
        hashlib.sha256(raw.encode()).hexdigest(),
    )
    s["link_type_item_id"] = await db.fetchval(
        "SELECT id FROM link_types WHERE is_social = FALSE LIMIT 1"
    )
    s["ident_type_item_id"] = ident_types["organization"]

    # Imports + activity log.
    s["batch_id"] = generate_id()
    await db.execute(
        "INSERT INTO import_batches"
        " (id, source_file, file_hash, row_count, loaded_count, error_count)"
        " VALUES ($1, 'a11y.csv', 'a11yhash', 1, 1, 0)",
        s["batch_id"],
    )
    s["log_id"] = await db.fetchval(
        "INSERT INTO api_request_log"
        " (api_key_id, method, path, route_group, status_code, latency_ms)"
        " VALUES ($1, 'GET', '/api/v1/people', 'other', 200, 12) RETURNING id",
        s["key_id"],
    )

    # Person embedding (registry row is a reference-table seed).
    s["model_id"] = _EMBED_MODEL_ID
    s["embedding_id"] = generate_id()
    vec = "[" + ",".join("0.125" for _ in range(_EMBED_DIM)) + "]"
    await db.execute(
        f"INSERT INTO {_EMBED_TABLE}"
        "  (id, person_id, embedding, embedding_dim, activity_ms, audio_sample_rate_hz,"
        "   source_service, source_job_id, source_segment, recorded_at, created_by_key_id)"
        " VALUES ($1, $2, $3::vector, $4, 1000, 16000, 'observo', 'job_a11y', 1, now(), $5)",
        s["embedding_id"],
        s["person_id"],
        vec,
        _EMBED_DIM,
        s["key_id"],
    )
    return s


def _param_values(path: str, s: dict) -> dict:
    """Path-prefix-aware param fill: the same param name (entity_id, addr_id,
    name_id, …) resolves to a different seeded entity per route family."""
    values = {
        "batch_id": s["batch_id"],
        "key_id": s["key_id"],
        "log_id": s["log_id"],
        "scope": "general",
        "role_id": s["role_id"],
        "ra_id": s["assignment_id"],
        "assignment_id": s["assignment_id"],
        "jurisdiction_id": s["jurisdiction_id"],
        "rel_id": s["rel_id"],
        "model_id": s["model_id"],
        "embedding_id": s["embedding_id"],
        "org_id": s["org_id"],
        "person_id": s["person_id"],
    }
    if path.startswith("/admin/orgs/"):
        values |= s["org_sub"]
        values |= {
            "entity_id": s["org_id"],
            "name_id": s["org_name_id"],
            "acronym_id": s["acronym_id"],
            "event_id": s["org_event_id"],
            "id_a": s["org_id"],
            "id_b": s["org2_id"],
            "winner_id": s["role_id"],  # org-scoped role merge
            "loser_id": s["role2_id"],
        }
    elif path.startswith("/admin/people/"):
        values |= s["person_sub"]
        values |= {
            "entity_id": s["person_id"],
            "name_id": s["person_name_id"],
            "event_id": s["person_event_id"],
            "winner_id": s["person_id"],
            "loser_id": s["person2_id"],
        }
    elif path.startswith("/admin/jurisdictions/"):
        values |= s["jur_sub"]
        values |= {"entity_id": s["jurisdiction_id"]}
    elif path.startswith("/admin/settings/link-types/"):
        values |= {"item_id": s["link_type_item_id"]}
    elif path.startswith("/admin/settings/identifier-types/"):
        values |= {"item_id": s["ident_type_item_id"]}
    return values


@pytest.fixture(scope="module", autouse=True)
def _assert_control_floor():
    """After the module's tests run, assert aggregate control coverage cleared
    the floor — but only when the whole sweep ran (a filtered subset can't reach
    it and would false-fail)."""
    yield
    if _routes_executed == len(ADMIN_GET_PATHS):
        assert _control_total >= _MIN_TOTAL_CONTROLS, (
            f"rendered only {_control_total} controls across {_routes_executed} routes"
            f" (floor {_MIN_TOTAL_CONTROLS}) — a form may have silently stopped rendering"
        )


@pytest.mark.parametrize("path", ADMIN_GET_PATHS)
async def test_admin_route_renders_accessible_dom(path, client, seed):
    global _control_total, _routes_executed
    _routes_executed += 1
    url = path.format_map(_param_values(path, seed)) + _QUERY.get(path, "")
    resp = await client.get(url, headers=AUTH_HEADERS | _EXTRA_HEADERS.get(path, {}))
    assert resp.status_code == 200, f"{url} -> {resp.status_code}: {resp.text[:300]}"

    if "text/html" not in resp.headers.get("content-type", ""):
        return  # nothing to check on non-HTML responses (e.g. JSON vectors)

    html = resp.text
    _control_total += count_controls(html)

    missing = controls_missing_accessible_name(html)
    assert not missing, f"{url}: controls missing accessible name:\n  " + "\n  ".join(missing)

    if is_full_document(html):
        dangling = dangling_id_refs(html)
        assert not dangling, f"{url}: unresolved id references:\n  " + "\n  ".join(dangling)
