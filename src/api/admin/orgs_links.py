"""Admin CRUD for organization links."""

from src.api.admin._links_shared import make_links_router

router = make_links_router(
    entity_type="organization",
    entity_id_key="org_id",
    prefix="/orgs/{entity_id}/links",
    tags=["admin-org-links"],
    entity_table="organizations",
    entity_not_found_msg="Organization not found",
    tmpl_form_row="admin/orgs/partials/_link_form_row.html",
    tmpl_read_row="admin/orgs/partials/_link_row.html",
    detail_url=lambda eid: f"/admin/orgs/{eid}/",
)
