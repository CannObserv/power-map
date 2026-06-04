"""GET /api/v1/changes — entity change feed for sibling-service cache invalidation."""

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query

from src.api.deps import get_db
from src.api.public.deps import require_api_key
from src.api.public.schemas import ChangeFeedResponse, ChangeItem, ChangeMeta, fmt_ts

router = APIRouter()

_QUERY = """
SELECT entity_type, entity_id, changed_at, change_kind, archived_at
FROM (
    SELECT
        'person'    AS entity_type,
        id          AS entity_id,
        updated_at  AS changed_at,
        'updated'   AS change_kind,
        archived_at
    FROM people
    WHERE updated_at >= $1

    UNION ALL

    SELECT
        'organization' AS entity_type,
        id             AS entity_id,
        updated_at     AS changed_at,
        'updated'      AS change_kind,
        archived_at
    FROM organizations
    WHERE updated_at >= $1

    UNION ALL

    SELECT
        'role'     AS entity_type,
        id         AS entity_id,
        updated_at AS changed_at,
        'updated'  AS change_kind,
        archived_at
    FROM roles
    WHERE updated_at >= $1

    UNION ALL

    SELECT
        entity_type,
        entity_id,
        deleted_at  AS changed_at,
        'deleted'   AS change_kind,
        NULL        AS archived_at
    FROM deleted_entities
    WHERE deleted_at >= $1
) combined
ORDER BY changed_at ASC, entity_id ASC
LIMIT $2
"""


@router.get(
    "/changes",
    response_model=ChangeFeedResponse,
    operation_id="getChangeFeed",
)
async def get_changes(
    since: Annotated[datetime, Query(description="ISO 8601 timestamp; changes at or after this.")],
    limit: Annotated[int, Query(ge=1, le=1000)] = 50,
    _user_id: str = Depends(require_api_key),
    db=Depends(get_db),
) -> ChangeFeedResponse:
    """Return entities updated, archived, or deleted since the given timestamp.

    Clients should pass ``meta.next_since`` from the previous response as
    ``since`` on each subsequent poll.  The ``since`` comparison is inclusive
    (>=) to avoid dropping events at exact timestamp boundaries; de-duplicate
    the overlap row using ``entity_id`` if needed.
    """
    if since.tzinfo is None:
        since = since.replace(tzinfo=UTC)

    # Fetch limit+1 to detect has_more without a separate COUNT query.
    rows = await db.fetch(_QUERY, since, limit + 1)

    has_more = len(rows) > limit
    rows = rows[:limit]

    items = [
        ChangeItem(
            entity_type=row["entity_type"],
            entity_id=row["entity_id"],
            changed_at=row["changed_at"],
            change_kind=row["change_kind"],
            archived_at=row["archived_at"],
        )
        for row in rows
    ]

    next_since = fmt_ts(rows[-1]["changed_at"]) if rows else fmt_ts(since)

    return ChangeFeedResponse(
        data=items,
        meta=ChangeMeta(
            limit=limit,
            count=len(items),
            has_more=has_more,
            next_since=next_since,
        ),
    )
