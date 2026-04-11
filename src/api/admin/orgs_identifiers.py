"""Admin CRUD for organization identifiers."""

from src.api.admin._identifiers_shared import make_identifiers_router

router = make_identifiers_router(
    entity_type="organization",
    entity_id_key="org_id",
    prefix="/orgs/{entity_id}/identifiers",
    tags=["admin-org-identifiers"],
    entity_table="organizations",
    entity_not_found_msg="Organization not found",
    tmpl_form_row="admin/orgs/partials/_identifier_form_row.html",
    tmpl_read_row="admin/orgs/partials/_identifier_row.html",
    detail_url=lambda eid: f"/admin/orgs/{eid}/",
)
