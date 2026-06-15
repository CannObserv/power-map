"""GET /api/v1/changes — outbox-based, subscription-filtered entity change feed."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query

from src.api.deps import get_db
from src.api.public.deps import AuthedKey, require_key
from src.api.public.schemas import ChangeFeedResponse, ChangeItem, ChangeMeta

router = APIRouter()

_QUERY = """
SELECT ec.id, ec.entity_type, ec.entity_id, ec.change_kind, ec.changed_at
FROM entity_changes ec
JOIN api_key_entity_subscriptions s
    ON s.entity_id = ec.entity_id
    AND s.api_key_id = $1
WHERE ec.id > $2
ORDER BY ec.id ASC
LIMIT $3
"""


@router.get(
    "/changes",
    response_model=ChangeFeedResponse,
    operation_id="getChangeFeed",
)
async def get_changes(
    after: Annotated[
        int,
        Query(ge=0, description="Outbox seq_id cursor (exclusive). Pass 0 for all events."),
    ],
    limit: Annotated[int, Query(ge=1, le=1000)] = 50,
    auth: AuthedKey = Depends(require_key),
    db=Depends(get_db),
) -> ChangeFeedResponse:
    """Return subscribed entity changes with id > after.

    Only events for entities this API key has explicitly subscribed to are returned.
    A key with no subscriptions receives an empty feed.

    Pass ``meta.next_after`` from the previous response as ``after`` on each
    subsequent poll. The cursor is exclusive (``>``), so no deduplication is needed.
    """
    rows = await db.fetch(_QUERY, auth.key_id, after, limit + 1)

    has_more = len(rows) > limit
    rows = rows[:limit]

    items = [
        ChangeItem(
            seq_id=row["id"],
            entity_type=row["entity_type"],
            entity_id=row["entity_id"],
            change_kind=row["change_kind"],
            changed_at=row["changed_at"],
        )
        for row in rows
    ]

    next_after = rows[-1]["id"] if rows else after

    return ChangeFeedResponse(
        data=items,
        meta=ChangeMeta(
            limit=limit,
            count=len(items),
            has_more=has_more,
            next_after=next_after,
        ),
    )
