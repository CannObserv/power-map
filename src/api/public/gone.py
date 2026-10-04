"""``404`` or ``410 Gone`` for a detail GET that found no live row (#607).

A merged or hard-deleted id used to ``404`` exactly like an id that never
existed, so a consumer reconciling by id could not follow a merge — the change
feed carried ``merged_into`` but the read path did not. A detail route now calls
:func:`not_found_or_gone` on a miss: if ``deleted_entities`` holds a tombstone
for the id it answers ``410`` with an :class:`EntityGone` body naming the live
end of the merge chain (``null`` for a genuine delete); otherwise ``404``.

The route reads the live row **first**, so a restore that un-merges an id (#467)
answers ``200`` even if a tombstone was left behind. A tombstoned id has no row,
so no ETag: the ``410`` never goes through :func:`conditional_response` and is
the same with or without ``If-None-Match``. ``Cache-Control: no-cache`` keeps an
intermediary from heuristically caching the ``410`` (RFC 9110 lets it), since a
restore can bring the id back; ``Vary: X-API-Key`` keys it per key, as
:func:`cache_headers` does for every other detail response.

``tests/api/public/test_gone.py`` sweeps that every route calling the helper
declares ``responses=DETAIL_RESPONSES``.
"""

from types import MappingProxyType
from typing import Final, NoReturn

import asyncpg
from fastapi import HTTPException
from fastapi.responses import JSONResponse

from src.api.public.etag import NOT_MODIFIED
from src.api.public.schemas import EntityGone
from src.core.merge_history import fetch_tombstone, walk_merge_history

# OpenAPI declaration for a tombstone-aware detail GET: its 304 plus the typed
# 410, so a generated client models a merged id as a response, not an error.
# Immutable for the same reason as NOT_MODIFIED — every detail route shares it.
DETAIL_RESPONSES: Final = MappingProxyType(
    {
        **NOT_MODIFIED,
        410: {
            "model": EntityGone,
            "description": (
                "Gone — the id was merged away or deleted. `merged_into` names "
                "where it went (null for a genuine delete)."
            ),
        },
    }
)


async def not_found_or_gone(
    db: asyncpg.Connection, entity_type: str, entity_id: str, detail: str
) -> JSONResponse | NoReturn:
    """Return the ``410`` for a tombstoned id, or raise the route's ``404``."""
    tombstone = await fetch_tombstone(db, entity_type, entity_id)
    if tombstone is None:
        raise HTTPException(status_code=404, detail=detail)

    merged_into = None
    if tombstone["merged_into"] is not None:
        # The walk returns pm_id=None for a chain ending in a delete, a missing
        # row or a cycle — all of which leave nothing to re-anchor to.
        merged_into = (await walk_merge_history(db, entity_type, tombstone["merged_into"])).pm_id

    body = EntityGone(
        id=entity_id,
        entity_type=entity_type,
        deleted_at=tombstone["deleted_at"],
        merged_into=merged_into,
    )
    return JSONResponse(
        status_code=410,
        content=body.model_dump(mode="json"),
        headers={"Cache-Control": "no-cache", "Vary": "X-API-Key"},
    )
