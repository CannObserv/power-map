"""Admin CRUD for entity_event citations (#319).

entity_event is a sub-entity (a row in the events table on a person/org detail
page), so its citation panel loads inline via ``inline_panel=True`` into a drawer
below the events table. The non-htmx redirect resolves to the event's owning
entity (person or organization).
"""

from src.api.admin._citations_shared import make_citations_router


async def _resolve_owner(event_id: str, db) -> str:
    row = await db.fetchrow(
        "SELECT entity_type, entity_id FROM entity_events WHERE id=$1", event_id
    )
    if not row:
        return "/admin/"
    base = "orgs" if row["entity_type"] == "organization" else "people"
    return f"/admin/{base}/{row['entity_id']}/"


async def _event_subject(event_id: str, db) -> str | None:
    # Heading label for the inline drawer: the event type name.
    name = await db.fetchval(
        "SELECT t.display_name FROM entity_events e"
        " JOIN entity_event_types t ON t.id = e.event_type_id"
        " WHERE e.id=$1",
        event_id,
    )
    return f"{name} event" if name else None


router = make_citations_router(
    entity_type="entity_event",
    prefix="/entity-events/{entity_id}/citations",
    tags=["admin-entity-event-citations"],
    entity_table="entity_events",
    entity_not_found_msg="Event not found",
    detail_url=lambda eid: "/admin/",
    redirect_resolver=_resolve_owner,
    subject_resolver=_event_subject,
    inline_panel=True,
    # Events keep the field picker — date / place / notes are all citable.
    # Events table columns: Type / Date / Place / Linked Entity / Status / actions.
    subrow_colspan=6,
)
