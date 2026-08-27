"""Org-duplicate detection: SQL, DB-backed TTL cache, and FastAPI dependency."""

from datetime import UTC, datetime, timedelta

from fastapi import Depends

from src.api.admin.deps import get_db
from src.core.logging import get_logger

logger = get_logger(__name__)

CANDIDATE_WHERE = """
    FROM organizations a
    JOIN organizations b ON b.id > a.id
    JOIN organization_names dn_a
        ON dn_a.organization_id = a.id
    JOIN organization_names dn_b
        ON dn_b.organization_id = b.id
    WHERE a.archived_at IS NULL AND b.archived_at IS NULL
      AND similarity(dn_a.name, dn_b.name) > 0.85
      AND NOT EXISTS (
          SELECT 1 FROM duplicate_dismissals
          WHERE entity_type = 'organization'
            AND entity_a_id = a.id AND entity_b_id = b.id
      )
      AND NOT EXISTS (
          -- #469: orgs in one succession chain are the same institution across
          -- source re-keys — never merge candidates. The view holds both
          -- orderings, so one probe covers the pair.
          SELECT 1 FROM v_org_succession_pairs sp
          WHERE sp.org_a = a.id AND sp.org_b = b.id
      )
"""

_DUP_COUNT_TTL = timedelta(seconds=300)
_ENTITY_TYPE = "organization"


async def invalidate_dup_count_cache(db) -> None:
    """Expire the cached duplicate count so the next request re-queries."""
    await db.execute(
        "UPDATE dup_count_cache SET expires_at = now() - interval '1 second'"
        " WHERE entity_type = $1",
        _ENTITY_TYPE,
    )


async def count_org_duplicates(db) -> int:
    """Return count of non-dismissed near-duplicate org pairs (DB-TTL-cached, 5 min).

    Cache is shared across all workers via the dup_count_cache table.
    On miss or expiry: computes the O(n²) similarity join and upserts the result.
    """
    row = await db.fetchrow(
        "SELECT count, expires_at FROM dup_count_cache WHERE entity_type = $1",
        _ENTITY_TYPE,
    )
    if row and row["expires_at"] > datetime.now(UTC):
        return row["count"]
    count = await db.fetchval(
        f"SELECT count(*) FROM (SELECT DISTINCT a.id, b.id {CANDIDATE_WHERE}) sub"
    )
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


async def get_org_dup_count(db=Depends(get_db)) -> int:
    """FastAPI dependency: cached org duplicate count, defaults to 0 on error."""
    try:
        return await count_org_duplicates(db)
    except Exception:
        logger.warning("Failed to fetch org duplicate count", exc_info=True)
        return 0
