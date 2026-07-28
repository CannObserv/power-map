"""Admin CRUD for role contact methods (#326)."""

from src.api.admin._contacts_shared import make_contacts_router

router = make_contacts_router(
    entity_type="role",
    entity_id_key="role_id",
    prefix="/roles/{entity_id}/contacts",
    tags=["admin-role-contacts"],
    entity_table="roles",
    entity_not_found_msg="Role not found",
    tmpl_form_row="admin/roles/partials/_contact_form_row.html",
    tmpl_read_row="admin/roles/partials/_contact_row.html",
    detail_url=lambda eid: f"/admin/roles/{eid}/",
)
