"""Admin CRUD for role-assignment citations (#319)."""

from src.api.admin._citations_shared import make_citations_router

router = make_citations_router(
    entity_type="role_assignment",
    prefix="/role-assignments/{entity_id}/citations",
    tags=["admin-role-assignment-citations"],
    entity_table="role_assignments",
    entity_not_found_msg="Role assignment not found",
    detail_url=lambda eid: f"/admin/role-assignments/{eid}/",
)
