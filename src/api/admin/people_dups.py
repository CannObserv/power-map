"""People-duplicate detection: SQL, DB-backed TTL cache, and FastAPI dependency."""

from datetime import UTC, datetime, timedelta

from fastapi import Depends

from src.api.admin.deps import get_db
from src.core.logging import get_logger

logger = get_logger(__name__)

CANDIDATE_WHERE = """
    FROM people a
    JOIN people b ON b.id > a.id
    JOIN person_names dn_a
        ON dn_a.person_id = a.id
       AND dn_a.is_canonical = TRUE AND dn_a.visibility = 'public'
    JOIN person_names dn_b
        ON dn_b.person_id = b.id
       AND dn_b.is_canonical = TRUE AND dn_b.visibility = 'public'
    WHERE a.archived_at IS NULL AND b.archived_at IS NULL
      AND similarity(dn_a.name, dn_b.name) > 0.85
      AND NOT EXISTS (
          SELECT 1 FROM duplicate_dismissals
          WHERE entity_type = 'person'
            AND entity_a_id = a.id AND entity_b_id = b.id
      )
"""

_DUP_COUNT_TTL = timedelta(seconds=300)
_ENTITY_TYPE = "person"


async def invalidate_dup_count_cache(db) -> None:
    """Expire the cached duplicate count so the next request re-queries."""
    await db.execute(
        "UPDATE dup_count_cache SET expires_at = now() - interval '1 second'"
        " WHERE entity_type = $1",
        _ENTITY_TYPE,
    )


async def count_person_duplicates(db) -> int:
    """Return count of non-dismissed near-duplicate person pairs (DB-TTL-cached, 5 min).

    Cache is shared across all workers via the dup_count_cache table.
    On miss or expiry: computes the O(n²) similarity join and upserts the result.
    """
    row = await db.fetchrow(
        "SELECT count, expires_at FROM dup_count_cache WHERE entity_type = $1",
        _ENTITY_TYPE,
    )
    if row and row["expires_at"] > datetime.now(UTC):
        return row["count"]
    count = await db.fetchval(f"SELECT count(*) {CANDIDATE_WHERE}")
    await db.execute(
        """INSERT INTO dup_count_cache (entity_type, count, expires_at)
           VALUES ($1, $2, now() + $3)
           ON CONFLICT (entity_type) DO UPDATE
             SET count = excluded.count, expires_at = excluded.expires_at""",
        _ENTITY_TYPE,
        count,
        _DUP_COUNT_TTL,
    )
    return count


async def get_person_dup_count(db=Depends(get_db)) -> int:
    """FastAPI dependency: cached person duplicate count, defaults to 0 on error."""
    try:
        return await count_person_duplicates(db)
    except Exception:
        logger.warning("Failed to fetch person duplicate count", exc_info=True)
        return 0
