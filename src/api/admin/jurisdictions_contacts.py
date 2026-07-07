"""Admin CRUD for jurisdiction contact methods."""

from src.api.admin._contacts_shared import make_contacts_router

router = make_contacts_router(
    entity_type="jurisdiction",
    entity_id_key="jurisdiction_id",
    prefix="/jurisdictions/{entity_id}/contacts",
    tags=["admin-jurisdiction-contacts"],
    entity_table="jurisdictions",
    entity_not_found_msg="Jurisdiction not found",
    tmpl_form_row="admin/jurisdictions/partials/_contact_form_row.html",
    tmpl_read_row="admin/jurisdictions/partials/_contact_row.html",
    detail_url=lambda eid: f"/admin/jurisdictions/{eid}/",
)
