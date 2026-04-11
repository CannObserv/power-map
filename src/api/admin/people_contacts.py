"""Admin CRUD for person contact methods."""

from src.api.admin._contacts_shared import make_contacts_router

router = make_contacts_router(
    entity_type="person",
    entity_id_key="person_id",
    prefix="/people/{entity_id}/contacts",
    tags=["admin-person-contacts"],
    entity_table="people",
    entity_not_found_msg="Person not found",
    tmpl_form_row="admin/people/partials/_contact_form_row.html",
    tmpl_read_row="admin/people/partials/_contact_row.html",
    detail_url=lambda eid: f"/admin/people/{eid}/",
)
