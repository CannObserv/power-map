"""Admin CRUD for role_assignment identifiers (#326).

Only role_assignment carries identifiers (not the role definition); the picker
excludes internal types, so `role_wa_pdc` is the offered public type.
"""

from src.api.admin._identifiers_shared import make_identifiers_router

router = make_identifiers_router(
    entity_type="role_assignment",
    entity_id_key="ra_id",
    prefix="/role-assignments/{entity_id}/identifiers",
    tags=["admin-role-assignment-identifiers"],
    entity_table="role_assignments",
    entity_not_found_msg="Role assignment not found",
    tmpl_form_row="admin/role_assignments/partials/_identifier_form_row.html",
    tmpl_read_row="admin/role_assignments/partials/_identifier_row.html",
    detail_url=lambda eid: f"/admin/role-assignments/{eid}/",
)
