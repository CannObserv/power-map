"""Org-duplicate detection: SQL, DB-backed TTL cache, and FastAPI dependency."""

from datetime import UTC, datetime, timedelta

import asyncpg
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


async def fetch_duplicate_pairs(db) -> list:
    """Return near-duplicate org pairs; empty list if pg_trgm not installed."""
    try:
        return await db.fetch(
            f"""WITH cands AS (
                SELECT DISTINCT ON (a.id, b.id)
                    a.id AS a_id,
                    dn_a.name AS a_match_name,
                    dn_a.is_canonical AS a_match_is_canonical,
                    a.created_at AS a_created,
                    b.id AS b_id,
                    dn_b.name AS b_match_name,
                    dn_b.is_canonical AS b_match_is_canonical,
                    b.created_at AS b_created,
                    similarity(dn_a.name, dn_b.name) AS score,
                    (SELECT count(*) FROM roles
                     WHERE organization_id = a.id AND archived_at IS NULL) AS a_roles,
                    (SELECT count(*) FROM roles
                     WHERE organization_id = b.id AND archived_at IS NULL) AS b_roles
                {CANDIDATE_WHERE}
                ORDER BY a.id, b.id, similarity(dn_a.name, dn_b.name) DESC
            )
            SELECT
                cands.a_id,
                COALESCE(vdn_a.display_name, cands.a_match_name) AS a_name,
                cands.a_match_name,
                cands.a_match_is_canonical,
                cands.a_created,
                cands.b_id,
                COALESCE(vdn_b.display_name, cands.b_match_name) AS b_name,
                cands.b_match_name,
                cands.b_match_is_canonical,
                cands.b_created,
                cands.score,
                cands.a_roles,
                cands.b_roles
            FROM cands
            LEFT JOIN v_org_display_names vdn_a ON vdn_a.organization_id = cands.a_id
            LEFT JOIN v_org_display_names vdn_b ON vdn_b.organization_id = cands.b_id
            ORDER BY cands.score DESC"""
        )
    except asyncpg.exceptions.UndefinedFunctionError:
        return []
