"""People-duplicate detection: SQL, TTL cache, and FastAPI dependency."""

import time

from fastapi import Depends

from src.api.admin.deps import get_db
from src.core.logging import get_logger

logger = get_logger(__name__)

CANDIDATE_WHERE = """
    FROM people a
    JOIN people b ON b.id > a.id
    JOIN v_person_display_names dn_a ON dn_a.person_id = a.id
    JOIN v_person_display_names dn_b ON dn_b.person_id = b.id
    WHERE a.archived_at IS NULL AND b.archived_at IS NULL
      AND similarity(dn_a.display_name, dn_b.display_name) > 0.85
      AND NOT EXISTS (
          SELECT 1 FROM duplicate_dismissals
          WHERE entity_type = 'person'
            AND entity_a_id = a.id AND entity_b_id = b.id
      )
"""

_DUP_COUNT_TTL = 300.0  # seconds
# Process-local cache — not shared across gunicorn workers; counts may lag by
# up to 5 min per worker under multi-process deployments.
_dup_count_cache: dict[str, int | float] = {"value": 0, "expires": 0.0}


def invalidate_dup_count_cache() -> None:
    """Expire the cached duplicate count so the next request re-queries."""
    _dup_count_cache["expires"] = 0.0


async def count_person_duplicates(db) -> int:
    """Return count of non-dismissed near-duplicate person pairs (TTL-cached, 5 min)."""
    now = time.monotonic()
    if now < _dup_count_cache["expires"]:
        return _dup_count_cache["value"]
    count = await db.fetchval(f"SELECT count(*) {CANDIDATE_WHERE}")
    _dup_count_cache["value"] = count
    _dup_count_cache["expires"] = now + _DUP_COUNT_TTL
    return count


async def get_person_dup_count(db=Depends(get_db)) -> int:
    """FastAPI dependency: cached person duplicate count, defaults to 0 on error."""
    try:
        return await count_person_duplicates(db)
    except Exception:
        logger.warning("Failed to fetch person duplicate count", exc_info=True)
        return 0
