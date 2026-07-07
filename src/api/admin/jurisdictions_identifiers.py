"""Admin CRUD for jurisdiction identifiers."""

from src.api.admin._identifiers_shared import make_identifiers_router

router = make_identifiers_router(
    entity_type="jurisdiction",
    entity_id_key="jurisdiction_id",
    prefix="/jurisdictions/{entity_id}/identifiers",
    tags=["admin-jurisdiction-identifiers"],
    entity_table="jurisdictions",
    entity_not_found_msg="Jurisdiction not found",
    tmpl_form_row="admin/jurisdictions/partials/_identifier_form_row.html",
    tmpl_read_row="admin/jurisdictions/partials/_identifier_row.html",
    detail_url=lambda eid: f"/admin/jurisdictions/{eid}/",
)
