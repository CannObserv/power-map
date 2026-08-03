"""Shared admin GET-route enumeration + seed fixtures for the a11y tiers (GH #300).

Extracted from ``test_a11y_render.py`` (#246) so the in-process lxml sweep and
the out-of-process Playwright/axe browser sweep (#300) consume **one** route
list and **one** seed dataset — a new admin GET route is swept by both tiers
automatically, and neither can drift from the other.

Nothing here is a test module: it exposes the enumeration constants and pure
seed helpers. The lxml tier wraps ``seed_admin_fixtures`` in a rolled-back
module fixture; the browser tier calls it once against a disposable database
(the BEGIN/ROLLBACK isolation trick can't cross the uvicorn process boundary).
"""

import hashlib

from fastapi.routing import APIRoute

from src.api.main import app
from src.core.db import generate_id

# exe.dev admin-auth headers (same dict as tests/api/admin/conftest.py). The
# API-key routes scope keys to this user id, so the seeded app_users row must
# carry it — see seed_admin_fixtures.
AUTH_HEADERS = {
    "X-ExeDev-UserID": "usr_test",
    "X-ExeDev-Email": "admin@test.com",
}

_EMBED_MODEL_ID = "pyannote-community-1-embed"
_EMBED_TABLE = "person_embeddings_pyannote_community_1_embed"
_EMBED_DIM = 256

# Every admin GET route, enumerated programmatically (no hand-picked views). A
# new route is swept automatically; if its params can't be filled the sweep
# fails loudly rather than silently skipping.
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
QUERY_PARAMS = {
    "/admin/orgs/{org_id}/addresses/country-format/": "?country=US",
    "/admin/people/{person_id}/addresses/country-format/": "?country=US",
    "/admin/jurisdictions/{jurisdiction_id}/addresses/country-format/": "?country=US",
    "/admin/_dup-badge/orgs/": "?variant=card",
    "/admin/_dup-badge/people/": "?variant=card",
    "/admin/orgs/{entity_id}/contacts/new-row/": "?contact_type=phone",
    "/admin/people/{entity_id}/contacts/new-row/": "?contact_type=phone",
    "/admin/jurisdictions/{entity_id}/contacts/new-row/": "?contact_type=phone",
    "/admin/roles/{entity_id}/contacts/new-row/": "?contact_type=phone",
    "/admin/role-assignments/{entity_id}/contacts/new-row/": "?contact_type=phone",
}

# Routes that only render their partial for an HTMX request. Without HX-Request
# they either 400 (dup badges) or 303-redirect to a list page (api-key detail).
# The sweep must exercise the *partial*, not the redirect target, so send the
# header. Redirect-following must be OFF in the client so a route that 3xx's
# here fails the 200 gate loudly instead of silently re-testing wherever it
# points.
EXTRA_HEADERS = {
    "/admin/_dup-badge/orgs/": {"HX-Request": "true"},
    "/admin/_dup-badge/people/": {"HX-Request": "true"},
    "/admin/settings/api-keys/{key_id}/detail/": {"HX-Request": "true"},
}


async def seed_subresources(
    db,
    entity_type: str,
    entity_id: str,
    ident_type_id: str | None,
    *,
    include_identifier: bool = True,
    include_address: bool = True,
) -> dict:
    """Contact + link rows for one entity (always), plus optional identifier and
    address rows; returns the seeded child ids.

    ``include_identifier``/``include_address`` are opt-out for entity types that
    have no such ancillary GET routes — roles carry only contacts+links (#326),
    and no ``role`` ``entity_identifier_types`` row exists to key an identifier."""
    link_type_id = await db.fetchval("SELECT id FROM link_types WHERE is_social = FALSE LIMIT 1")
    out = {
        "contact_id": generate_id(),
        "link_id": generate_id(),
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
    if include_identifier:
        out["ident_id"] = generate_id()
        await db.execute(
            "INSERT INTO identifiers (id, entity_id, entity_identifier_type_id, value)"
            " VALUES ($1, $2, $3, 'A11Y-1')",
            out["ident_id"],
            entity_id,
            ident_type_id,
        )
    if include_address:
        out["addr_id"] = generate_id()
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


async def seed_admin_fixtures(db) -> dict:
    """One entity per type plus one row per sub-resource — enough to fill every
    path param in ADMIN_GET_PATHS.

    Callable against any connection: the lxml tier passes a rolled-back
    connection, the browser tier a disposable-database one."""
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
    org_sub = await seed_subresources(db, "organization", s["org_id"], ident_types["organization"])
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
    person_sub = await seed_subresources(db, "person", s["person_id"], ident_types["person"])
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

    # Role + role_assignment ancillary (#326/#329): roles carry contacts+links
    # only (no `role` identifier type); assignments add identifiers. Neither has
    # address routes.
    s["role_sub"] = await seed_subresources(
        db, "role", s["role_id"], None, include_identifier=False, include_address=False
    )
    s["ra_sub"] = await seed_subresources(
        db,
        "role_assignment",
        s["assignment_id"],
        ident_types["role_assignment"],
        include_address=False,
    )

    # Second assignment + a role_assignment_relationship edge (#301): fills the
    # /role-assignments/{ra_id}/relationships/{rel_id}/... routes. The edge is
    # staffer(assignment2) --staff_of--> principal(assignment).
    s["assignment2_id"] = generate_id()
    await db.execute(
        "INSERT INTO role_assignments (id, person_id, role_id) VALUES ($1, $2, $3)",
        s["assignment2_id"],
        s["person2_id"],
        s["role2_id"],
    )
    ra_rel_type = await db.fetchval(
        "SELECT id FROM role_assignment_relationship_types WHERE slug = 'staff_of'"
    )
    s["ra_rel_id"] = generate_id()
    await db.execute(
        "INSERT INTO role_assignment_relationships (id, from_assignment_id, to_assignment_id,"
        " rel_type_id) VALUES ($1, $2, $3, $4)",
        s["ra_rel_id"],
        s["assignment2_id"],
        s["assignment_id"],
        ra_rel_type,
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
    jur_sub = await seed_subresources(
        db, "jurisdiction", s["jurisdiction_id"], ident_types["jurisdiction"]
    )
    s["jur_sub"] = jur_sub

    # Citations (#319): one active citation per citable entity type, so the
    # /{entity}/{entity_id}/citations/{citation_id}/{read,edit}-row routes fill.
    # person_name + entity_event are the two indirect citable types.
    s["citation_ids"] = {}
    _citation_targets = (
        ("organization", s["org_id"]),
        ("person", s["person_id"]),
        ("role", s["role_id"]),
        ("role_assignment", s["assignment_id"]),
        ("jurisdiction", s["jurisdiction_id"]),
        ("person_name", s["person_name_id"]),
        ("entity_event", s["org_event_id"]),
    )
    for entity_type, entity_id in _citation_targets:
        cid = generate_id()
        s["citation_ids"][entity_type] = cid
        await db.execute(
            "INSERT INTO citations (id, entity_type, entity_id, url, title)"
            " VALUES ($1, $2, $3, 'https://example.com/cite', 'A11y Citation')",
            cid,
            entity_type,
            entity_id,
        )

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


def param_values(path: str, s: dict) -> dict:
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
            "citation_id": s["citation_ids"]["organization"],
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
            "citation_id": s["citation_ids"]["person"],
            "winner_id": s["person_id"],
            "loser_id": s["person2_id"],
        }
    elif path.startswith("/admin/role-assignments/"):
        values |= s["ra_sub"]
        values |= {
            "entity_id": s["assignment_id"],
            "ra_id": s["assignment_id"],
            "rel_id": s["ra_rel_id"],
            "citation_id": s["citation_ids"]["role_assignment"],
        }
    elif path.startswith("/admin/roles/"):
        values |= s["role_sub"]
        values |= {
            "entity_id": s["role_id"],
            "citation_id": s["citation_ids"]["role"],
        }
    elif path.startswith("/admin/jurisdictions/"):
        values |= s["jur_sub"]
        values |= {
            "entity_id": s["jurisdiction_id"],
            "citation_id": s["citation_ids"]["jurisdiction"],
        }
    elif path.startswith("/admin/person-names/"):
        values |= {
            "entity_id": s["person_name_id"],
            "citation_id": s["citation_ids"]["person_name"],
        }
    elif path.startswith("/admin/entity-events/"):
        values |= {
            "entity_id": s["org_event_id"],
            "citation_id": s["citation_ids"]["entity_event"],
        }
    elif path.startswith("/admin/settings/link-types/"):
        values |= {"item_id": s["link_type_item_id"]}
    elif path.startswith("/admin/settings/identifier-types/"):
        values |= {"item_id": s["ident_type_item_id"]}
    return values
