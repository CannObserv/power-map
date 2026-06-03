"""Shared factory for entity events CRUD routers (people and organizations)."""

from collections.abc import Callable

import asyncpg
from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from markupsafe import escape

from src.api.admin.deps import AdminUser, flash_trigger, get_admin_user, get_db, is_htmx
from src.core.db import generate_id

templates = Jinja2Templates(directory="src/templates")

_EVENT_FETCH_QUERY = """
SELECT ee.id, ee.event_year, ee.event_month, ee.event_day,
       ee.event_hour, ee.event_minute, ee.event_second,
       ee.event_place_text, ee.linked_entity_type, ee.linked_entity_id,
       ee.notes, ee.visibility, ee.archived_at,
       eet.slug AS event_type_slug, eet.display_name AS event_type_name
FROM entity_events ee
JOIN entity_event_types eet ON eet.id = ee.event_type_id
WHERE ee.entity_id = $1 AND ee.entity_type = $2
ORDER BY ee.archived_at IS NOT NULL, ee.event_year DESC NULLS LAST, ee.created_at DESC
"""

_EVENT_SINGLE_QUERY = """
SELECT ee.*, eet.slug AS event_type_slug, eet.display_name AS event_type_name,
       eet.requires_year, eet.requires_linked_entity
FROM entity_events ee
JOIN entity_event_types eet ON eet.id = ee.event_type_id
WHERE ee.id = $1 AND ee.entity_id = $2 AND ee.entity_type = $3
"""

_EVENT_TYPES_QUERY = """
SELECT id, slug, display_name, requires_year, requires_linked_entity
FROM entity_event_types
WHERE applies_to = $1 OR applies_to = 'both'
ORDER BY display_name
"""


async def fetch_entity_events(entity_id: str, entity_type: str, db: asyncpg.Connection) -> list:
    """Fetch all events for an entity, sorted for display.

    Exposed at module level so detail routes can include events in context.
    """
    return await db.fetch(_EVENT_FETCH_QUERY, entity_id, entity_type)


def _parse_int(value: str) -> int | None:
    """Parse an optional integer form field. Returns None for empty/whitespace/invalid input."""
    stripped = value.strip()
    if not stripped:
        return None
    try:
        return int(stripped)
    except ValueError:
        return None


def make_events_router(
    *,
    entity_type: str,
    entity_id_key: str,
    prefix: str,
    tags: list[str],
    entity_table: str,
    entity_not_found_msg: str,
    tmpl_form_row: str,
    tmpl_read_row: str,
    tmpl_rows: str,
    detail_url: Callable[[str], str],
) -> APIRouter:
    """Return a configured events APIRouter for the given entity type.

    Parameters
    ----------
    entity_type:
        DB value stored in entity_events.entity_type (e.g. ``'person'``).
    entity_id_key:
        Template context key for the entity id (e.g. ``'org_id'`` or ``'person_id'``).
    prefix:
        Router URL prefix — must contain ``{entity_id}`` as the path variable.
    tags:
        FastAPI tags list.
    entity_table:
        Table name used to verify the parent entity exists.
    entity_not_found_msg:
        Detail string for 404 when parent entity is missing.
    tmpl_form_row:
        Template path for the event form row partial.
    tmpl_read_row:
        Template path for the event read row partial.
    tmpl_rows:
        Template path for the full tbody partial (used after archive/unarchive).
    detail_url:
        Callable accepting the entity id and returning the detail redirect URL.
    """
    router = APIRouter(prefix=prefix, tags=tags)

    # ---- helpers ----------------------------------------------------------------

    async def _get_entity_or_404(entity_id: str, db):
        row = await db.fetchrow(f"SELECT id FROM {entity_table} WHERE id=$1", entity_id)
        if not row:
            raise HTTPException(status_code=404, detail=entity_not_found_msg)
        return row

    async def _get_event_or_404(event_id: str, entity_id: str, db):
        row = await db.fetchrow(
            _EVENT_SINGLE_QUERY,
            event_id,
            entity_id,
            entity_type,
        )
        if not row:
            raise HTTPException(status_code=404)
        return row

    def _ctx(entity_id: str, **extra) -> dict:
        """Build template context with the correct entity-id key."""
        return {entity_id_key: entity_id, **extra}

    def _form_response(request, entity_id: str, ev, event_types, error: str | None = None):
        """Return form template response, used for new-row and validation errors."""
        return templates.TemplateResponse(
            request,
            tmpl_form_row,
            _ctx(entity_id, ev=ev, event_types=event_types, form_error=error),
        )

    # ---- routes -----------------------------------------------------------------

    @router.get("/new-row/")
    async def event_new_row(
        entity_id: str,
        request: Request,
        user: AdminUser = Depends(get_admin_user),
        db=Depends(get_db),
    ):
        """Return empty event form row."""
        await _get_entity_or_404(entity_id, db)
        event_types = await db.fetch(_EVENT_TYPES_QUERY, entity_type)
        return _form_response(request, entity_id, None, event_types)

    @router.post("/")
    async def event_create(
        entity_id: str,
        request: Request,
        event_type_id: str = Form(...),
        event_year: str = Form(""),
        event_month: str = Form(""),
        event_day: str = Form(""),
        event_hour: str = Form(""),
        event_minute: str = Form(""),
        event_second: str = Form(""),
        event_place_text: str = Form(""),
        linked_entity_type: str = Form(""),
        linked_entity_id: str = Form(""),
        notes: str = Form(""),
        visibility: str = Form("public"),
        user: AdminUser = Depends(get_admin_user),
        db=Depends(get_db),
    ):
        """Create a new event."""
        await _get_entity_or_404(entity_id, db)

        year_val = _parse_int(event_year)
        month_val = _parse_int(event_month)
        day_val = _parse_int(event_day)
        hour_val = _parse_int(event_hour)
        minute_val = _parse_int(event_minute)
        second_val = _parse_int(event_second)

        # Validate event type constraints
        etype_row = await db.fetchrow(
            "SELECT requires_year, requires_linked_entity FROM entity_event_types WHERE id=$1",
            event_type_id,
        )
        if not etype_row:
            raise HTTPException(status_code=400, detail="Unknown event type")
        if etype_row["requires_year"] and year_val is None:
            if not is_htmx(request):
                return RedirectResponse(detail_url(entity_id), status_code=303)
            event_types = await db.fetch(_EVENT_TYPES_QUERY, entity_type)
            return _form_response(
                request,
                entity_id,
                None,
                event_types,
                error="Year is required for this event type.",
            )
        if etype_row["requires_linked_entity"] and not linked_entity_id.strip():
            if not is_htmx(request):
                return RedirectResponse(detail_url(entity_id), status_code=303)
            event_types = await db.fetch(_EVENT_TYPES_QUERY, entity_type)
            return _form_response(
                request,
                entity_id,
                None,
                event_types,
                error="Linked entity is required for this event type.",
            )

        eid = generate_id()
        linked_id = linked_entity_id.strip() or None
        linked_type = linked_entity_type.strip() or None if linked_id else None
        await db.execute(
            """INSERT INTO entity_events
               (id, entity_type, entity_id, event_type_id,
                event_year, event_month, event_day,
                event_hour, event_minute, event_second,
                event_place_text, linked_entity_type, linked_entity_id,
                notes, visibility)
               VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15)""",
            eid,
            entity_type,
            entity_id,
            event_type_id,
            year_val,
            month_val,
            day_val,
            hour_val,
            minute_val,
            second_val,
            event_place_text.strip() or None,
            linked_type,
            linked_id,
            notes.strip() or None,
            visibility,
        )
        row = await _get_event_or_404(eid, entity_id, db)
        if not is_htmx(request):
            return RedirectResponse(detail_url(entity_id), status_code=303)
        return templates.TemplateResponse(
            request,
            tmpl_read_row,
            _ctx(entity_id, ev=row),
            headers=flash_trigger(
                "success",
                f"Event <strong>{escape(row['event_type_name'])}</strong> added.",
            ),
        )

    @router.get("/{event_id}/read-row/")
    async def event_read_row(
        entity_id: str,
        event_id: str,
        request: Request,
        user: AdminUser = Depends(get_admin_user),
        db=Depends(get_db),
    ):
        """Return read-only event row (Cancel on edit form)."""
        row = await _get_event_or_404(event_id, entity_id, db)
        return templates.TemplateResponse(request, tmpl_read_row, _ctx(entity_id, ev=row))

    @router.get("/{event_id}/edit-row/")
    async def event_edit_row_get(
        entity_id: str,
        event_id: str,
        request: Request,
        user: AdminUser = Depends(get_admin_user),
        db=Depends(get_db),
    ):
        """Return event edit form row."""
        row = await _get_event_or_404(event_id, entity_id, db)
        event_types = await db.fetch(_EVENT_TYPES_QUERY, entity_type)
        return _form_response(request, entity_id, row, event_types)

    @router.post("/{event_id}/edit-row/")
    async def event_edit_row_post(
        entity_id: str,
        event_id: str,
        request: Request,
        event_type_id: str = Form(...),
        event_year: str = Form(""),
        event_month: str = Form(""),
        event_day: str = Form(""),
        event_hour: str = Form(""),
        event_minute: str = Form(""),
        event_second: str = Form(""),
        event_place_text: str = Form(""),
        linked_entity_type: str = Form(""),
        linked_entity_id: str = Form(""),
        notes: str = Form(""),
        visibility: str = Form("public"),
        user: AdminUser = Depends(get_admin_user),
        db=Depends(get_db),
    ):
        """Update an event."""
        existing = await _get_event_or_404(event_id, entity_id, db)

        year_val = _parse_int(event_year)
        month_val = _parse_int(event_month)
        day_val = _parse_int(event_day)
        hour_val = _parse_int(event_hour)
        minute_val = _parse_int(event_minute)
        second_val = _parse_int(event_second)

        # Validate event type constraints
        etype_row = await db.fetchrow(
            "SELECT requires_year, requires_linked_entity FROM entity_event_types WHERE id=$1",
            event_type_id,
        )
        if not etype_row:
            raise HTTPException(status_code=400, detail="Unknown event type")
        if etype_row["requires_year"] and year_val is None:
            if not is_htmx(request):
                return RedirectResponse(detail_url(entity_id), status_code=303)
            event_types = await db.fetch(_EVENT_TYPES_QUERY, entity_type)
            return _form_response(
                request,
                entity_id,
                existing,
                event_types,
                error="Year is required for this event type.",
            )
        if etype_row["requires_linked_entity"] and not linked_entity_id.strip():
            if not is_htmx(request):
                return RedirectResponse(detail_url(entity_id), status_code=303)
            event_types = await db.fetch(_EVENT_TYPES_QUERY, entity_type)
            return _form_response(
                request,
                entity_id,
                existing,
                event_types,
                error="Linked entity is required for this event type.",
            )

        linked_id = linked_entity_id.strip() or None
        linked_type = linked_entity_type.strip() or None if linked_id else None
        await db.execute(
            """UPDATE entity_events SET
               event_type_id=$1,
               event_year=$2, event_month=$3, event_day=$4,
               event_hour=$5, event_minute=$6, event_second=$7,
               event_place_text=$8, linked_entity_type=$9, linked_entity_id=$10,
               notes=$11, visibility=$12
               WHERE id=$13""",
            event_type_id,
            year_val,
            month_val,
            day_val,
            hour_val,
            minute_val,
            second_val,
            event_place_text.strip() or None,
            linked_type,
            linked_id,
            notes.strip() or None,
            visibility,
            event_id,
        )
        row = await _get_event_or_404(event_id, entity_id, db)
        if not is_htmx(request):
            return RedirectResponse(detail_url(entity_id), status_code=303)
        return templates.TemplateResponse(
            request,
            tmpl_read_row,
            _ctx(entity_id, ev=row),
            headers=flash_trigger(
                "success",
                f"Event <strong>{escape(row['event_type_name'])}</strong> saved.",
            ),
        )

    @router.post("/{event_id}/archive/")
    async def event_archive(
        entity_id: str,
        event_id: str,
        request: Request,
        user: AdminUser = Depends(get_admin_user),
        db=Depends(get_db),
    ):
        """Archive an event. Returns 409 if already archived."""
        ev = await _get_event_or_404(event_id, entity_id, db)
        if ev["archived_at"]:
            raise HTTPException(status_code=409, detail="Event is already archived")
        await db.execute("UPDATE entity_events SET archived_at = NOW() WHERE id=$1", event_id)
        if not is_htmx(request):
            return RedirectResponse(detail_url(entity_id), status_code=303)
        events = await fetch_entity_events(entity_id, entity_type, db)
        return templates.TemplateResponse(
            request,
            tmpl_rows,
            _ctx(entity_id, events=events),
            headers=flash_trigger("success", "Event archived."),
        )

    @router.post("/{event_id}/unarchive/")
    async def event_unarchive(
        entity_id: str,
        event_id: str,
        request: Request,
        user: AdminUser = Depends(get_admin_user),
        db=Depends(get_db),
    ):
        """Unarchive an event. Returns 409 if not archived."""
        ev = await _get_event_or_404(event_id, entity_id, db)
        if not ev["archived_at"]:
            raise HTTPException(status_code=409, detail="Event is not archived")
        await db.execute("UPDATE entity_events SET archived_at = NULL WHERE id=$1", event_id)
        if not is_htmx(request):
            return RedirectResponse(detail_url(entity_id), status_code=303)
        events = await fetch_entity_events(entity_id, entity_type, db)
        return templates.TemplateResponse(
            request,
            tmpl_rows,
            _ctx(entity_id, events=events),
            headers=flash_trigger("success", "Event unarchived."),
        )

    @router.delete("/{event_id}/")
    async def event_delete(
        entity_id: str,
        event_id: str,
        request: Request,
        user: AdminUser = Depends(get_admin_user),
        db=Depends(get_db),
    ):
        """Hard delete an event. Requires archived_at IS NOT NULL (409 otherwise)."""
        ev = await _get_event_or_404(event_id, entity_id, db)
        if not ev["archived_at"]:
            raise HTTPException(status_code=409, detail="Event must be archived before deletion")
        await db.execute("DELETE FROM entity_events WHERE id=$1", event_id)
        if not is_htmx(request):
            return RedirectResponse(detail_url(entity_id), status_code=303)
        return HTMLResponse(
            content="",
            status_code=200,
            headers=flash_trigger("info", "Event deleted."),
        )

    return router
