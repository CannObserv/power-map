"""Admin CRUD for jurisdiction links."""

from src.api.admin._links_shared import make_links_router

router = make_links_router(
    entity_type="jurisdiction",
    entity_id_key="jurisdiction_id",
    prefix="/jurisdictions/{entity_id}/links",
    tags=["admin-jurisdiction-links"],
    entity_table="jurisdictions",
    entity_not_found_msg="Jurisdiction not found",
    tmpl_form_row="admin/jurisdictions/partials/_link_form_row.html",
    tmpl_read_row="admin/jurisdictions/partials/_link_row.html",
    detail_url=lambda eid: f"/admin/jurisdictions/{eid}/",
)
