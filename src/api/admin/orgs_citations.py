"""Admin CRUD for organization citations (#319)."""

from src.api.admin._citations_shared import make_citations_router

router = make_citations_router(
    entity_type="organization",
    prefix="/orgs/{entity_id}/citations",
    tags=["admin-org-citations"],
    entity_table="organizations",
    entity_not_found_msg="Organization not found",
    detail_url=lambda eid: f"/admin/orgs/{eid}/",
)
