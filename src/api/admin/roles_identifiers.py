"""Admin CRUD for role identifiers (#808).

A role definition carries identifiers since #808 (`role_cannabis_observer`); the
picker excludes internal types, so `pm_role_id` is never offered.
"""

from src.api.admin._identifiers_shared import make_identifiers_router

router = make_identifiers_router(
    entity_type="role",
    entity_id_key="role_id",
    prefix="/roles/{entity_id}/identifiers",
    tags=["admin-role-identifiers"],
    entity_table="roles",
    entity_not_found_msg="Role not found",
    tmpl_form_row="admin/roles/partials/_identifier_form_row.html",
    tmpl_read_row="admin/roles/partials/_identifier_row.html",
    tmpl_rows="admin/roles/partials/_identifier_rows.html",
    detail_url=lambda eid: f"/admin/roles/{eid}/",
)
