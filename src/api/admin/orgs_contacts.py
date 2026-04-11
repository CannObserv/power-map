"""Admin CRUD for organization contact methods."""

from src.api.admin._contacts_shared import make_contacts_router

router = make_contacts_router(
    entity_type="organization",
    entity_id_key="org_id",
    prefix="/orgs/{entity_id}/contacts",
    tags=["admin-org-contacts"],
    entity_table="organizations",
    entity_not_found_msg="Organization not found",
    tmpl_form_row="admin/orgs/partials/_contact_form_row.html",
    tmpl_read_row="admin/orgs/partials/_contact_row.html",
    detail_url=lambda eid: f"/admin/orgs/{eid}/",
)
