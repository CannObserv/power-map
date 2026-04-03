"""Admin views for import history (read-only)."""

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates

from src.api.admin.deps import AdminUser, check_auth, get_admin_user, get_db
from src.api.admin.org_dups import get_org_dup_count
from src.api.admin.people_dups import get_person_dup_count

templates = Jinja2Templates(directory="src/templates")
router = APIRouter(prefix="/imports", tags=["admin-imports"])

PAGE_SIZE = 50


@router.get("/")
async def imports_list(
    request: Request,
    page: int = 1,
    user: AdminUser | RedirectResponse = Depends(get_admin_user),
    db=Depends(get_db),
    org_dup_count: int = Depends(get_org_dup_count),
    person_dup_count: int = Depends(get_person_dup_count),
):
    """List all import batches, most recent first."""
    redirect, user = check_auth(user)
    if redirect:
        return redirect
    offset = (page - 1) * PAGE_SIZE
    batches = await db.fetch(
        "SELECT * FROM import_batches ORDER BY imported_at DESC LIMIT $1 OFFSET $2",
        PAGE_SIZE,
        offset,
    )
    total = await db.fetchval("SELECT COUNT(*) FROM import_batches")
    return templates.TemplateResponse(
        request,
        "admin/imports/batches.html",
        {
            "user": user,
            "active_section": "imports",
            "batches": batches,
            "total": total,
            "page": page,
            "page_size": PAGE_SIZE,
            "org_dup_count": org_dup_count,
            "person_dup_count": person_dup_count,
        },
    )


@router.get("/{batch_id}/")
async def import_detail(
    batch_id: str,
    request: Request,
    page: int = 1,
    user: AdminUser | RedirectResponse = Depends(get_admin_user),
    db=Depends(get_db),
    org_dup_count: int = Depends(get_org_dup_count),
    person_dup_count: int = Depends(get_person_dup_count),
):
    """Detail view for one import batch, paginated provenance rows."""
    redirect, user = check_auth(user)
    if redirect:
        return redirect
    batch = await db.fetchrow("SELECT * FROM import_batches WHERE id = $1", batch_id)
    if not batch:
        raise HTTPException(status_code=404, detail="Import batch not found")
    offset = (page - 1) * PAGE_SIZE
    provenance = await db.fetch(
        "SELECT * FROM import_provenance"
        " WHERE batch_id = $1 ORDER BY source_row LIMIT $2 OFFSET $3",
        batch_id,
        PAGE_SIZE,
        offset,
    )
    total = await db.fetchval(
        "SELECT COUNT(*) FROM import_provenance WHERE batch_id = $1", batch_id
    )
    return templates.TemplateResponse(
        request,
        "admin/imports/batch_detail.html",
        {
            "user": user,
            "active_section": "imports",
            "batch": batch,
            "provenance": provenance,
            "total": total,
            "page": page,
            "page_size": PAGE_SIZE,
            "org_dup_count": org_dup_count,
            "person_dup_count": person_dup_count,
        },
    )
