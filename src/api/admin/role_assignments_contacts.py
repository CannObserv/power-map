"""Admin CRUD for role_assignment contact methods (#326)."""

from src.api.admin._contacts_shared import make_contacts_router

router = make_contacts_router(
    entity_type="role_assignment",
    entity_id_key="ra_id",
    prefix="/role-assignments/{entity_id}/contacts",
    tags=["admin-role-assignment-contacts"],
    entity_table="role_assignments",
    entity_not_found_msg="Role assignment not found",
    tmpl_form_row="admin/role_assignments/partials/_contact_form_row.html",
    tmpl_read_row="admin/role_assignments/partials/_contact_row.html",
    detail_url=lambda eid: f"/admin/role-assignments/{eid}/",
)
