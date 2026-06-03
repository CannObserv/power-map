"""Admin CRUD for person events."""

from src.api.admin._events_shared import make_events_router

router = make_events_router(
    entity_type="person",
    entity_id_key="person_id",
    prefix="/people/{entity_id}/events",
    tags=["admin-person-events"],
    entity_table="people",
    entity_not_found_msg="Person not found",
    tmpl_form_row="admin/people/partials/_event_form_row.html",
    tmpl_read_row="admin/people/partials/_event_row.html",
    tmpl_rows="admin/people/partials/_event_rows.html",
    detail_url=lambda eid: f"/admin/people/{eid}/",
)
