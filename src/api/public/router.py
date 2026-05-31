"""Public API v1 router — requires X-API-Key on all routes."""

from fastapi import APIRouter, Depends

from src.api.public.changes import router as changes_router
from src.api.public.deps import require_api_key
from src.api.public.link_types import router as link_types_router
from src.api.public.observations import router as observations_router
from src.api.public.orgs import router as orgs_router
from src.api.public.people import router as people_router

router = APIRouter(prefix="/api/v1", tags=["public-api"])
router.include_router(changes_router)
router.include_router(link_types_router)
router.include_router(observations_router)
router.include_router(orgs_router)
router.include_router(people_router)


@router.get("/")
async def api_root(user_id: str = Depends(require_api_key)):
    """API health check — returns version info when key is valid."""
    return {"status": "ok", "version": "v1"}
