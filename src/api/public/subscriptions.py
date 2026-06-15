"""GET/POST/DELETE /api/v1/subscriptions — per-key entity subscription management."""

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Response

from src.api.deps import get_db
from src.api.public.deps import AuthedKey, require_key, require_scope
from src.api.public.schemas import (
    SubscriptionBulkDeleteRequest,
    SubscriptionItem,
    SubscriptionListMeta,
    SubscriptionListResponse,
    SubscriptionRegisterRequest,
    SubscriptionRegisterResponse,
)

router = APIRouter()

EntityType = Literal["person", "organization", "jurisdiction", "role", "role_assignment"]

# Resolves entity_type for a given entity_id across all entity tables + deleted tombstones.
_RESOLVE_ENTITY_TYPE = """
SELECT entity_type FROM (
    SELECT 'person'          AS entity_type FROM people           WHERE id = $1
    UNION ALL
    SELECT 'organization'    AS entity_type FROM organizations    WHERE id = $1
    UNION ALL
    SELECT 'jurisdiction'    AS entity_type FROM jurisdictions    WHERE id = $1
    UNION ALL
    SELECT 'role'            AS entity_type FROM roles            WHERE id = $1
    UNION ALL
    SELECT 'role_assignment' AS entity_type FROM role_assignments WHERE id = $1
    UNION ALL
    SELECT entity_type                      FROM deleted_entities WHERE entity_id = $1
) t
LIMIT 1
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
    """
    registered = 0
    already_subscribed = 0
    not_found: list[str] = []

    for entity_id in body.entity_ids:
        type_row = await db.fetchrow(_RESOLVE_ENTITY_TYPE, entity_id)
        if type_row is None:
            not_found.append(entity_id)
            continue

        entity_type = type_row["entity_type"]
        existing = await db.fetchrow(
            "SELECT 1 FROM api_key_entity_subscriptions WHERE api_key_id = $1 AND entity_id = $2",
            auth.key_id,
            entity_id,
        )
        if existing:
            already_subscribed += 1
        else:
            await db.execute(
                "INSERT INTO api_key_entity_subscriptions"
                " (api_key_id, entity_id, entity_type) VALUES ($1,$2,$3)",
                auth.key_id,
                entity_id,
                entity_type,
            )
            registered += 1

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
