"""Public API v1 router — requires X-API-Key on all routes."""

from fastapi import APIRouter, Depends

from src.api.public.deps import require_api_key

router = APIRouter(prefix="/api/v1", tags=["public-api"])


@router.get("/")
async def api_root(user_id: str = Depends(require_api_key)):
    """API health check — returns version info when key is valid."""
    return {"status": "ok", "version": "v1"}
