"""Public API v1 router — requires X-API-Key on all routes."""

from fastapi import APIRouter, Depends

from src.api.public.assignment_relationships import router as assignment_relationships_router
from src.api.public.assignments import router as assignments_router
from src.api.public.changes import router as changes_router
from src.api.public.citations import router as citations_router
from src.api.public.deps import require_api_key
from src.api.public.embeddings import router as embeddings_router
from src.api.public.entity_event_types import router as entity_event_types_router
from src.api.public.entity_identifier_types import (
    router as entity_identifier_types_router,
)
from src.api.public.jurisdictions import router as jurisdictions_router
from src.api.public.link_types import router as link_types_router
from src.api.public.orgs import router as orgs_router
from src.api.public.people import router as people_router
from src.api.public.role_types import router as role_types_router
from src.api.public.roles import router as roles_router
from src.api.public.subscriptions import router as subscriptions_router

# Every route depends on the auth choke point, so every route can 429 (#292);
# declared once here so the whole public surface documents the throttle contract.
router = APIRouter(
    prefix="/api/v1",
    tags=["public-api"],
    responses={
        429: {
            "description": "Rate limit exceeded — back off for Retry-After seconds. "
            "See PUBLIC_API.md § Rate Limits."
        }
    },
)
router.include_router(assignments_router)
router.include_router(assignment_relationships_router)
router.include_router(changes_router)
router.include_router(citations_router)
# Within subscriptions_router, GET /subscriptions/discover is defined before
# DELETE /subscriptions/{entity_id} so FastAPI does not match 'discover' as entity_id.
router.include_router(subscriptions_router)
# embeddings_router registered before people_router: /people/identify (static)
# must be resolved before /people/{id} (dynamic) for POST routes.
router.include_router(embeddings_router)
router.include_router(entity_event_types_router)
router.include_router(entity_identifier_types_router)
router.include_router(jurisdictions_router)
router.include_router(link_types_router)
router.include_router(orgs_router)
router.include_router(people_router)
router.include_router(role_types_router)
router.include_router(roles_router)


@router.get("/")
async def api_root(user_id: str = Depends(require_api_key)):
    """API health check — returns version info when key is valid."""
    return {"status": "ok", "version": "v1"}
