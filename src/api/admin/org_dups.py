"""Org-duplicate detection: SQL, TTL cache, and FastAPI dependency."""

import time

from fastapi import Depends

from src.api.admin.deps import get_db

CANDIDATE_WHERE = """
    FROM organizations a
    JOIN organizations b ON b.id > a.id
    JOIN v_org_display_names dn_a ON dn_a.organization_id = a.id
    JOIN v_org_display_names dn_b ON dn_b.organization_id = b.id
    WHERE a.archived_at IS NULL AND b.archived_at IS NULL
      AND similarity(dn_a.display_name, dn_b.display_name) > 0.85
      AND NOT EXISTS (
          SELECT 1 FROM duplicate_dismissals
          WHERE entity_type = 'organization'
            AND entity_a_id = a.id AND entity_b_id = b.id
      )
"""

_DUP_COUNT_TTL = 300.0  # seconds
_dup_count_cache: dict[str, int | float] = {"value": 0, "expires": 0.0}


def invalidate_dup_count_cache() -> None:
    """Expire the cached duplicate count so the next request re-queries."""
    _dup_count_cache["expires"] = 0.0


async def count_org_duplicates(db) -> int:
    """Return count of non-dismissed near-duplicate org pairs (TTL-cached, 5 min)."""
    now = time.monotonic()
    if now < _dup_count_cache["expires"]:
        return _dup_count_cache["value"]
    count = await db.fetchval(f"SELECT count(*) {CANDIDATE_WHERE}")
    _dup_count_cache["value"] = count
    _dup_count_cache["expires"] = now + _DUP_COUNT_TTL
    return count


async def get_org_dup_count(db=Depends(get_db)) -> int:
    """FastAPI dependency: cached org duplicate count, defaults to 0 on error."""
    try:
        return await count_org_duplicates(db)
    except Exception:
        return 0
