"""Admin CRUD for person identifiers."""

from src.api.admin._identifiers_shared import make_identifiers_router

router = make_identifiers_router(
    entity_type="person",
    entity_id_key="person_id",
    prefix="/people/{entity_id}/identifiers",
    tags=["admin-person-identifiers"],
    entity_table="people",
    entity_not_found_msg="Person not found",
    tmpl_form_row="admin/people/partials/_identifier_form_row.html",
    tmpl_read_row="admin/people/partials/_identifier_row.html",
    detail_url=lambda eid: f"/admin/people/{eid}/",
)
