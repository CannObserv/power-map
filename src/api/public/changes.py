"""GET /api/v1/changes — outbox-based, subscription-filtered entity change feed."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query

from src.api.deps import get_db
from src.api.public.deps import AuthedKey, require_key
from src.api.public.schemas import ChangeFeedResponse, ChangeItem, ChangeMeta

router = APIRouter()

# Single round-trip: the page rows plus the global prune horizon (#388). The
# horizon CTE always yields exactly one row (MIN is NULL when the outbox is
# empty); LEFT JOIN page ON true carries min_seq onto every page row, and still
# returns one placeholder row (page columns NULL) when the page is empty — so
# min_seq is available even on an empty page without a second query. min_seq is
# global, not subscription-scoped: pruning is a global changed_at delete, so it
# is the single id below which any event may already be gone.
_QUERY = """
WITH horizon AS (SELECT MIN(id) AS min_seq FROM entity_changes),
page AS (
    SELECT ec.id, ec.entity_type, ec.entity_id, ec.change_kind, ec.changed_at, ec.merged_into,
           ec.source_key_id
    FROM entity_changes ec
    JOIN api_key_entity_subscriptions s
        ON s.entity_id = ec.entity_id
        AND s.api_key_id = $1
    WHERE ec.id > $2
    ORDER BY ec.id ASC
    LIMIT $3
)
SELECT h.min_seq,
       p.id, p.entity_type, p.entity_id, p.change_kind, p.changed_at, p.merged_into,
       p.source_key_id
FROM horizon h
LEFT JOIN page p ON true
ORDER BY p.id ASC NULLS LAST
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

    # The horizon CTE guarantees ≥1 row; min_seq is the same on every row.
    min_seq = rows[0]["min_seq"] if rows else None
    # Drop the empty-page placeholder (page columns NULL) before paginating.
    page = [row for row in rows if row["id"] is not None]

    has_more = len(page) > limit
    page = page[:limit]

    items = [
        ChangeItem(
            seq_id=row["id"],
            entity_type=row["entity_type"],
            entity_id=row["entity_id"],
            change_kind=row["change_kind"],
            changed_at=row["changed_at"],
            merged_into=row["merged_into"],
            source_key_id=row["source_key_id"],
        )
        for row in page
    ]

    next_after = page[-1]["id"] if page else after

    return ChangeFeedResponse(
        data=items,
        meta=ChangeMeta(
            limit=limit,
            count=len(items),
            has_more=has_more,
            next_after=next_after,
            min_seq=min_seq,
        ),
    )
