"""Admin CRUD for person links."""

from src.api.admin._links_shared import make_links_router

router = make_links_router(
    entity_type="person",
    entity_id_key="person_id",
    prefix="/people/{entity_id}/links",
    tags=["admin-person-links"],
    entity_table="people",
    entity_not_found_msg="Person not found",
    tmpl_form_row="admin/people/partials/_link_form_row.html",
    tmpl_read_row="admin/people/partials/_link_row.html",
    detail_url=lambda eid: f"/admin/people/{eid}/",
)
