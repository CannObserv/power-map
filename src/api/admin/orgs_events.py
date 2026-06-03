"""Admin CRUD for organization events."""

from src.api.admin._events_shared import make_events_router

router = make_events_router(
    entity_type="organization",
    entity_id_key="org_id",
    prefix="/orgs/{entity_id}/events",
    tags=["admin-org-events"],
    entity_table="organizations",
    entity_not_found_msg="Organization not found",
    tmpl_form_row="admin/orgs/partials/_event_form_row.html",
    tmpl_read_row="admin/orgs/partials/_event_row.html",
    tmpl_rows="admin/orgs/partials/_event_rows.html",
    detail_url=lambda eid: f"/admin/orgs/{eid}/",
)
