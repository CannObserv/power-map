"""Admin CRUD for person citations (#319)."""

from src.api.admin._citations_shared import make_citations_router

router = make_citations_router(
    entity_type="person",
    prefix="/people/{entity_id}/citations",
    tags=["admin-person-citations"],
    entity_table="people",
    entity_not_found_msg="Person not found",
    detail_url=lambda eid: f"/admin/people/{eid}/",
)
