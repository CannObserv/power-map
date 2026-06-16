"""GET/POST/DELETE /api/v1/subscriptions — per-key entity subscription management."""

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Response

from src.api.deps import get_db
from src.api.public.deps import AuthedKey, require_key, require_scope
from src.api.public.schemas import (
    DiscoveryItem,
    DiscoveryMeta,
    DiscoveryResponse,
    SubscriptionBulkDeleteRequest,
    SubscriptionItem,
    SubscriptionListMeta,
    SubscriptionListResponse,
    SubscriptionRegisterRequest,
    SubscriptionRegisterResponse,
)

router = APIRouter()

EntityType = Literal["person", "organization", "jurisdiction", "role", "role_assignment"]
RootType = Literal["jurisdiction", "organization"]

_VALID_FOLLOW = frozenset(
    {"lineage", "affiliated_orgs", "org_children", "roles", "assignments", "people"}
)


def _parse_follow(raw: str) -> list[str]:
    """Split and validate the comma-separated follow param."""
    if not raw or not raw.strip():
        return []
    steps = [s.strip() for s in raw.split(",") if s.strip()]
    unknown = [s for s in steps if s not in _VALID_FOLLOW]
    if unknown:
        raise HTTPException(422, f"Unknown follow values: {unknown!r}")
    return steps


def _validate_follow_chain(root_type: str, steps: list[str]) -> None:
    """Raise 422 if a follow step's prerequisites aren't satisfied."""
    available: set[str] = {root_type}
    for step in steps:
        if step == "lineage":
            if "jurisdiction" not in available:
                raise HTTPException(422, "lineage requires root_type=jurisdiction")
        elif step == "affiliated_orgs":
            if "jurisdiction" not in available:
                raise HTTPException(422, "affiliated_orgs requires a jurisdiction in scope")
            available.add("organization")
        elif step == "org_children":
            if "organization" not in available:
                raise HTTPException(422, "org_children requires an organization in scope")
        elif step == "roles":
            if "organization" not in available:
                raise HTTPException(422, "roles requires an organization in scope")
            available.add("role")
        elif step == "assignments":
            if "role" not in available:
                raise HTTPException(422, "assignments requires roles in the follow list")
            available.add("role_assignment")
        elif step == "people":
            if "role_assignment" not in available:
                raise HTTPException(422, "people requires assignments in the follow list")
            available.add("person")


async def _fetch_display_names(db, items: list[dict]) -> dict[str, str | None]:
    """Batch-fetch display names for a page of discovery items; returns entity_id → name."""
    by_type: dict[str, list[str]] = {}
    for item in items:
        by_type.setdefault(item["entity_type"], []).append(item["entity_id"])

    names: dict[str, str | None] = {}

    if jur_ids := by_type.get("jurisdiction"):
        rows = await db.fetch(
            "SELECT id, name FROM jurisdictions WHERE id = ANY($1::text[])", jur_ids
        )
        for r in rows:
            names[r["id"]] = r["name"]

    if org_ids := by_type.get("organization"):
        rows = await db.fetch(
            "SELECT organization_id AS id, display_name FROM v_org_display_names"
            " WHERE organization_id = ANY($1::text[])",
            org_ids,
        )
        for r in rows:
            names[r["id"]] = r["display_name"]

    if role_ids := by_type.get("role"):
        rows = await db.fetch(
            "SELECT id, title AS display_name FROM roles WHERE id = ANY($1::text[])", role_ids
        )
        for r in rows:
            names[r["id"]] = r["display_name"]

    if asgn_ids := by_type.get("role_assignment"):
        rows = await db.fetch(
            "SELECT ra.id, r.title AS display_name"
            " FROM role_assignments ra JOIN roles r ON r.id = ra.role_id"
            " WHERE ra.id = ANY($1::text[])",
            asgn_ids,
        )
        for r in rows:
            names[r["id"]] = r["display_name"]

    if person_ids := by_type.get("person"):
        rows = await db.fetch(
            "SELECT person_id AS id, display_name FROM v_person_display_names"
            " WHERE person_id = ANY($1::text[])",
            person_ids,
        )
        for r in rows:
            names[r["id"]] = r["display_name"]

    return names


_TRAVERSE_MAX_ITEMS = 5_000


async def _traverse(db, root_type: str, root_id: str, steps: list[str]) -> tuple[list[dict], bool]:
    """Run graph traversal; return (results, truncated).

    results: ordered list of {entity_type, entity_id, hops_from_root}.
    Stops accumulating once _TRAVERSE_MAX_ITEMS is reached; truncated=True signals the caller.
    """
    results: list[dict] = [{"entity_type": root_type, "entity_id": root_id, "hops_from_root": 0}]
    seen: set[str] = {root_id}
    truncated = False

    for hop, step in enumerate(steps, start=1):
        new_ids: list[str] = []

        if step == "lineage":
            jur_ids = [r["entity_id"] for r in results if r["entity_type"] == "jurisdiction"]
            if jur_ids:
                rows = await db.fetch(
                    """
                    WITH RECURSIVE lin AS (
                        SELECT j.id, ARRAY[j.id] AS visited
                        FROM jurisdictions j WHERE j.id = ANY($1::text[])
                        UNION ALL
                        SELECT j2.id, lin.visited || j2.id
                        FROM lin
                        JOIN jurisdiction_relationships jr
                            ON jr.from_id = lin.id OR jr.to_id = lin.id
                        JOIN jurisdiction_relationship_types jrt
                            ON jrt.id = jr.rel_type_id AND jrt.category IN ('lineage', 'spatial')
                        JOIN jurisdictions j2
                            ON j2.id = CASE WHEN jr.from_id = lin.id
                                            THEN jr.to_id ELSE jr.from_id END
                        WHERE NOT (j2.id = ANY(lin.visited))
                    )
                    SELECT DISTINCT id FROM lin WHERE id != ALL($1::text[])
                    """,
                    jur_ids,
                )
                new_ids = [r["id"] for r in rows]
            entity_type = "jurisdiction"

        elif step == "affiliated_orgs":
            jur_ids = [r["entity_id"] for r in results if r["entity_type"] == "jurisdiction"]
            if jur_ids:
                rows = await db.fetch(
                    """
                    SELECT DISTINCT oja.organization_id AS id
                    FROM organization_jurisdiction_affiliations oja
                    JOIN organization_jurisdiction_affiliation_types ojat
                        ON ojat.id = oja.affiliation_type_id AND ojat.slug = 'governing'
                    WHERE oja.jurisdiction_id = ANY($1::text[])
                    """,
                    jur_ids,
                )
                new_ids = [r["id"] for r in rows]
            entity_type = "organization"

        elif step == "org_children":
            org_ids = [r["entity_id"] for r in results if r["entity_type"] == "organization"]
            if org_ids:
                rows = await db.fetch(
                    """
                    WITH RECURSIVE tree AS (
                        SELECT id, ARRAY[id] AS visited
                        FROM organizations WHERE id = ANY($1::text[])
                        UNION ALL
                        SELECT o.id, tree.visited || o.id
                        FROM organizations o
                        JOIN tree ON tree.id = o.parent_id
                        WHERE NOT (o.id = ANY(tree.visited))
                    )
                    SELECT DISTINCT id FROM tree WHERE id != ALL($1::text[])
                    """,
                    org_ids,
                )
                new_ids = [r["id"] for r in rows]
            entity_type = "organization"

        elif step == "roles":
            org_ids = [r["entity_id"] for r in results if r["entity_type"] == "organization"]
            if org_ids:
                rows = await db.fetch(
                    "SELECT DISTINCT id FROM roles WHERE organization_id = ANY($1::text[])",
                    org_ids,
                )
                new_ids = [r["id"] for r in rows]
            entity_type = "role"

        elif step == "assignments":
            role_ids = [r["entity_id"] for r in results if r["entity_type"] == "role"]
            if role_ids:
                rows = await db.fetch(
                    "SELECT DISTINCT id FROM role_assignments WHERE role_id = ANY($1::text[])",
                    role_ids,
                )
                new_ids = [r["id"] for r in rows]
            entity_type = "role_assignment"

        elif step == "people":
            asgn_ids = [r["entity_id"] for r in results if r["entity_type"] == "role_assignment"]
            if asgn_ids:
                rows = await db.fetch(
                    "SELECT DISTINCT person_id AS id FROM role_assignments"
                    " WHERE id = ANY($1::text[])",
                    asgn_ids,
                )
                new_ids = [r["id"] for r in rows]
            entity_type = "person"

        else:
            continue

        for eid in new_ids:
            if eid not in seen:
                if len(results) >= _TRAVERSE_MAX_ITEMS:
                    truncated = True
                    break
                results.append(
                    {"entity_type": entity_type, "entity_id": eid, "hops_from_root": hop}
                )
                seen.add(eid)
        if truncated:
            break

    return results, truncated


@router.get(
    "/subscriptions/discover",
    response_model=DiscoveryResponse,
    operation_id="discoverSubscriptions",
)
async def discover_subscriptions(
    root_type: Annotated[RootType, Query(description="Entity type of the traversal root")],
    root_id: Annotated[str, Query(description="ULID or slug of the root entity")],
    follow: Annotated[
        str,
        Query(
            description=(
                "Comma-separated traversal steps (applied in order): "
                "lineage — jurisdiction lineage edges (recursive); "
                "affiliated_orgs — orgs with 'governing' affiliation for in-scope jurisdictions; "
                "org_children — child orgs via parent_id (recursive); "
                "roles — roles owned by in-scope orgs; "
                "assignments — role_assignments for in-scope roles; "
                "people — persons via in-scope assignments. "
                "Each step has prerequisites; a violation returns 422."
            )
        ),
    ] = "",
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
    auth: AuthedKey = Depends(require_key),
    db=Depends(get_db),
) -> DiscoveryResponse:
    """Graph-traversal discovery of entities to subscribe to.

    Returns candidate entities reachable from a root jurisdiction or organization
    via the specified traversal steps. The client selects from results and POSTs
    to ``/subscriptions`` to register entities for the change feed.
    """
    steps = _parse_follow(follow)
    _validate_follow_chain(root_type, steps)

    # Resolve root entity
    if root_type == "jurisdiction":
        root_row = await db.fetchrow(
            "SELECT id FROM jurisdictions WHERE id = $1 OR slug = $1", root_id
        )
    else:
        root_row = await db.fetchrow("SELECT id FROM organizations WHERE id = $1", root_id)
    if root_row is None:
        raise HTTPException(status_code=404, detail=f"{root_type} not found")

    resolved_id = root_row["id"]
    all_items, truncated = await _traverse(db, root_type, resolved_id, steps)

    total = len(all_items)
    page = all_items[offset : offset + limit]
    has_more = (offset + limit) < total

    names = await _fetch_display_names(db, page)

    return DiscoveryResponse(
        data=[
            DiscoveryItem(
                entity_type=item["entity_type"],
                entity_id=item["entity_id"],
                display_name=names.get(item["entity_id"]),
                hops_from_root=item["hops_from_root"],
            )
            for item in page
        ],
        meta=DiscoveryMeta(
            limit=limit,
            offset=offset,
            count=len(page),
            has_more=has_more,
            truncated=truncated,
        ),
    )


# Batch-resolves entity_type for a set of entity_ids across all tables + deleted tombstones.
# DISTINCT ON (entity_id) guards against data-integrity anomalies where an entity_id could
# theoretically appear in more than one source (e.g., both a live table and deleted_entities).
_BATCH_RESOLVE_ENTITY_TYPE = """
SELECT DISTINCT ON (entity_id) entity_type, entity_id
FROM (
    SELECT 'person'          AS entity_type, id AS entity_id
    FROM people WHERE id = ANY($1::text[])
    UNION ALL
    SELECT 'organization'    AS entity_type, id AS entity_id
    FROM organizations WHERE id = ANY($1::text[])
    UNION ALL
    SELECT 'jurisdiction'    AS entity_type, id AS entity_id
    FROM jurisdictions WHERE id = ANY($1::text[])
    UNION ALL
    SELECT 'role'            AS entity_type, id AS entity_id
    FROM roles WHERE id = ANY($1::text[])
    UNION ALL
    SELECT 'role_assignment' AS entity_type, id AS entity_id
    FROM role_assignments WHERE id = ANY($1::text[])
    UNION ALL
    SELECT entity_type, entity_id
    FROM deleted_entities WHERE entity_id = ANY($1::text[])
) t
ORDER BY entity_id
"""


@router.get(
    "/subscriptions",
    response_model=SubscriptionListResponse,
    operation_id="listSubscriptions",
)
async def list_subscriptions(
    entity_type: Annotated[EntityType | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    auth: AuthedKey = Depends(require_key),
    db=Depends(get_db),
) -> SubscriptionListResponse:
    """List entity subscriptions for the calling API key."""
    if entity_type:
        rows = await db.fetch(
            "SELECT entity_id, entity_type, created_at"
            " FROM api_key_entity_subscriptions"
            " WHERE api_key_id = $1 AND entity_type = $2"
            " ORDER BY created_at ASC"
            " LIMIT $3 OFFSET $4",
            auth.key_id,
            entity_type,
            limit + 1,
            offset,
        )
    else:
        rows = await db.fetch(
            "SELECT entity_id, entity_type, created_at"
            " FROM api_key_entity_subscriptions"
            " WHERE api_key_id = $1"
            " ORDER BY created_at ASC"
            " LIMIT $2 OFFSET $3",
            auth.key_id,
            limit + 1,
            offset,
        )

    has_more = len(rows) > limit
    rows = rows[:limit]

    return SubscriptionListResponse(
        data=[
            SubscriptionItem(
                entity_id=r["entity_id"],
                entity_type=r["entity_type"],
                created_at=r["created_at"],
            )
            for r in rows
        ],
        meta=SubscriptionListMeta(
            limit=limit,
            offset=offset,
            count=len(rows),
            has_more=has_more,
        ),
    )


@router.post(
    "/subscriptions",
    response_model=SubscriptionRegisterResponse,
    operation_id="registerSubscriptions",
)
async def register_subscriptions(
    body: SubscriptionRegisterRequest,
    auth: AuthedKey = Depends(require_scope("subscriptions:write")),
    db=Depends(get_db),
) -> SubscriptionRegisterResponse:
    """Bulk-register entity IDs for the calling key.

    Idempotent — already-subscribed IDs are counted separately, not errored.
    Unknown entity IDs are listed in ``not_found``; the rest of the batch still applies.
    The entire batch is applied atomically.
    """
    async with db.transaction():
        # Single round-trip to resolve all entity types.
        rows = await db.fetch(_BATCH_RESOLVE_ENTITY_TYPE, body.entity_ids)
        found: dict[str, str] = {r["entity_id"]: r["entity_type"] for r in rows}
        not_found = [eid for eid in body.entity_ids if eid not in found]

        if not found:
            return SubscriptionRegisterResponse(
                registered=0, already_subscribed=0, not_found=not_found
            )

        found_ids = list(found.keys())
        found_types = [found[eid] for eid in found_ids]
        result = await db.execute(
            """
            INSERT INTO api_key_entity_subscriptions (api_key_id, entity_id, entity_type)
            SELECT $1, r.entity_id, r.entity_type
            FROM unnest($2::text[], $3::text[]) AS r(entity_id, entity_type)
            ON CONFLICT (api_key_id, entity_id) DO NOTHING
            """,
            auth.key_id,
            found_ids,
            found_types,
        )
        registered = int(result.split()[-1])
        already_subscribed = len(found) - registered

    return SubscriptionRegisterResponse(
        registered=registered,
        already_subscribed=already_subscribed,
        not_found=not_found,
    )


@router.delete(
    "/subscriptions/{entity_id}",
    status_code=204,
    operation_id="deleteSubscription",
)
async def delete_subscription(
    entity_id: str,
    auth: AuthedKey = Depends(require_scope("subscriptions:write")),
    db=Depends(get_db),
) -> Response:
    """Remove a single entity subscription for the calling key. 404 if not subscribed."""
    result = await db.execute(
        "DELETE FROM api_key_entity_subscriptions WHERE api_key_id = $1 AND entity_id = $2",
        auth.key_id,
        entity_id,
    )
    # asyncpg returns "DELETE N" — extract N.
    count = int(result.split()[-1])
    if count == 0:
        raise HTTPException(status_code=404, detail="Subscription not found")
    return Response(status_code=204)


@router.delete(
    "/subscriptions",
    status_code=204,
    operation_id="deleteSubscriptionsBulk",
)
async def delete_subscriptions_bulk(
    body: SubscriptionBulkDeleteRequest,
    auth: AuthedKey = Depends(require_scope("subscriptions:write")),
    db=Depends(get_db),
) -> Response:
    """Bulk-remove entity subscriptions for the calling key. Silently ignores unknown IDs."""
    await db.execute(
        "DELETE FROM api_key_entity_subscriptions"
        " WHERE api_key_id = $1 AND entity_id = ANY($2::text[])",
        auth.key_id,
        body.entity_ids,
    )
    return Response(status_code=204)
