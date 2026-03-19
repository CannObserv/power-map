"""Admin router — mounts all entity sub-routers."""

from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse

admin_router = APIRouter(prefix="/admin")


@admin_router.get("/")
async def dashboard(request: Request):
    user_id = request.headers.get("X-ExeDev-UserID")
    email = request.headers.get("X-ExeDev-Email")
    if not user_id or not email:
        return RedirectResponse(
            f"/__exe.dev/login?redirect=/admin/", status_code=307
        )
    return {"user": email}  # placeholder — replaced in Task 6
