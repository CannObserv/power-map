"""Shared helpers for entity event list endpoints."""

from datetime import datetime

import asyncpg

from src.api.public.schemas import fmt_ts

_EVENTS_VERSION_SQL = """
    SELECT count(*) AS n, max(updated_at) AS last
    FROM entity_events
    WHERE entity_id = $1
      AND entity_type = $2
      AND visibility = 'public'
      AND archived_at IS NULL
"""


async def events_collection_validator(
    db, entity_id: str, entity_type: str, limit: int, offset: int
) -> tuple[str, datetime | None]:
    """Compute the (ETag, Last-Modified) pair for an entity's events collection (#292).

    The collection version is ``count`` + ``max(updated_at)`` over the entity's
    *visible* events — count catches additions/removals (an archived or hidden
    event leaves the filtered set, so its own ``updated_at`` bump is invisible),
    max catches in-place edits. Pagination params are baked into the tag since
    they change the response body. Last-Modified is None for an empty
    collection (nothing was ever modified); the ETag still revalidates — the
    audit's dominant case is exactly this unchanged/empty poll.
    """
    row = await db.fetchrow(_EVENTS_VERSION_SQL, entity_id, entity_type)
    last: datetime | None = row["last"]
    last_ms = int(last.timestamp() * 1000) if last is not None else 0
    etag = f'"{entity_id}-events-{last_ms}-{row["n"]}-{limit}-{offset}"'
    return etag, last


def events_cache_headers(etag: str, last: datetime | None) -> dict[str, str]:
    """Header set for events list responses — mirrors the detail endpoints' contract."""
    headers = {
        "ETag": etag,
        "Cache-Control": "no-cache",
        "Vary": "X-API-Key",
    }
    if last is not None:
        headers["Last-Modified"] = last.strftime("%a, %d %b %Y %H:%M:%S GMT")
    return headers


def row_to_event(r: asyncpg.Record) -> dict:
    """Convert an entity_events DB row (with joined event_type fields) to a response dict."""
    return {
        "id": r["id"],
        "event_type": {
            "id": r["event_type_id"],
            "slug": r["event_type_slug"],
            "display_name": r["event_type_display_name"],
        },
        "date": {
            "year": r["event_year"],
            "month": r["event_month"],
            "day": r["event_day"],
            "hour": r["event_hour"],
            "minute": r["event_minute"],
            "second": r["event_second"],
            "at": fmt_ts(r["event_at"]) if r["event_at"] else None,
        },
        "event_place_text": r["event_place_text"],
        "event_place_address": (
            {
                "id": r["event_place_address_id"],
                "city": r["place_city"],
                "region": r["place_region"],
                "standardized": r["place_standardized"],
                "precision": r["place_precision"],
            }
            if r["event_place_address_id"]
            else None
        ),
        "linked_entity_type": r["linked_entity_type"],
        "linked_entity_id": r["linked_entity_id"],
        "notes": r["notes"],
        "visibility": r["visibility"],
        "verified_at": r["verified_at"],
        "created_at": r["created_at"],
    }
