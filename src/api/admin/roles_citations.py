"""Admin CRUD for role citations (#319)."""

from src.api.admin._citations_shared import make_citations_router

router = make_citations_router(
    entity_type="role",
    prefix="/roles/{entity_id}/citations",
    tags=["admin-role-citations"],
    entity_table="roles",
    entity_not_found_msg="Role not found",
    detail_url=lambda eid: f"/admin/roles/{eid}/",
)
