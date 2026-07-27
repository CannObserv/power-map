"""Admin CRUD for role_assignment links (#326)."""

from src.api.admin._links_shared import make_links_router

router = make_links_router(
    entity_type="role_assignment",
    entity_id_key="ra_id",
    prefix="/role-assignments/{entity_id}/links",
    tags=["admin-role-assignment-links"],
    entity_table="role_assignments",
    entity_not_found_msg="Role assignment not found",
    tmpl_form_row="admin/role_assignments/partials/_link_form_row.html",
    tmpl_read_row="admin/role_assignments/partials/_link_row.html",
    detail_url=lambda eid: f"/admin/role-assignments/{eid}/",
)
