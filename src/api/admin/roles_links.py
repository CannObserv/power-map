"""Admin CRUD for role links (#326)."""

from src.api.admin._links_shared import make_links_router

router = make_links_router(
    entity_type="role",
    entity_id_key="role_id",
    prefix="/roles/{entity_id}/links",
    tags=["admin-role-links"],
    entity_table="roles",
    entity_not_found_msg="Role not found",
    tmpl_form_row="admin/roles/partials/_link_form_row.html",
    tmpl_read_row="admin/roles/partials/_link_row.html",
    detail_url=lambda eid: f"/admin/roles/{eid}/",
)
