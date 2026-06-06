"""Shared helpers for entity event list endpoints."""

import asyncpg

from src.api.public.schemas import fmt_ts


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
