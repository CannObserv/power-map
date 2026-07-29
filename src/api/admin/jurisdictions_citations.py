"""Admin CRUD for jurisdiction citations (#319)."""

from src.api.admin._citations_shared import make_citations_router

router = make_citations_router(
    entity_type="jurisdiction",
    prefix="/jurisdictions/{entity_id}/citations",
    tags=["admin-jurisdiction-citations"],
    entity_table="jurisdictions",
    entity_not_found_msg="Jurisdiction not found",
    detail_url=lambda eid: f"/admin/jurisdictions/{eid}/",
)
