"""Admin views for lookup tables: link_types, entity_identifier_types."""

import asyncpg
from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from src.api.admin.deps import AdminUser, check_auth, get_admin_user, get_db
from src.api.admin.org_dups import get_org_dup_count
from src.core.db import generate_id

templates = Jinja2Templates(directory="src/templates")
router = APIRouter(prefix="/lookups", tags=["admin-lookups"])


# ---------------------------------------------------------------------------
# Link Types (replaces platforms + url_types)
# ---------------------------------------------------------------------------


@router.get("/platforms/")
async def platforms_list(
    request: Request,
    user: AdminUser | RedirectResponse = Depends(get_admin_user),
    db=Depends(get_db),
    org_dup_count: int = Depends(get_org_dup_count),
):
    """List all social link types (is_social=TRUE)."""
    redirect, user = check_auth(user)
    if redirect:
        return redirect
    items = await db.fetch(
        "SELECT * FROM link_types WHERE is_social = TRUE ORDER BY display_name"
    )
    return templates.TemplateResponse(
        request,
        "admin/lookups/list.html",
        {
            "user": user,
            "active_section": "lookups",
            "kind": "platforms",
            "items": items,
            "org_dup_count": org_dup_count,
        },
    )


@router.get("/platforms/new/")
async def platform_new_form(
    request: Request,
    user: AdminUser | RedirectResponse = Depends(get_admin_user),
    org_dup_count: int = Depends(get_org_dup_count),
):
    """New platform form."""
    redirect, user = check_auth(user)
    if redirect:
        return redirect
    return templates.TemplateResponse(
        request,
        "admin/lookups/form.html",
        {
            "user": user,
            "active_section": "lookups",
            "kind": "platforms",
            "item": None,
            "org_dup_count": org_dup_count,
        },
    )


@router.post("/platforms/new/")
async def platform_create(
    request: Request,
    display_name: str = Form(...),
    slug: str = Form(...),
    user: AdminUser | RedirectResponse = Depends(get_admin_user),
    db=Depends(get_db),
):
    """Create a new social link type."""
    redirect, user = check_auth(user)
    if redirect:
        return redirect
    pid = generate_id()
    await db.execute(
        "INSERT INTO link_types (id, display_name, slug, is_social) VALUES ($1, $2, $3, TRUE)",
        pid, display_name, slug or None,
    )
    return RedirectResponse("/admin/lookups/platforms/", status_code=303)


@router.get("/platforms/{item_id}/edit/")
async def platform_edit_form(
    item_id: str,
    request: Request,
    user: AdminUser | RedirectResponse = Depends(get_admin_user),
    db=Depends(get_db),
    org_dup_count: int = Depends(get_org_dup_count),
):
    """Edit platform form."""
    redirect, user = check_auth(user)
    if redirect:
        return redirect
    item = await db.fetchrow(
        "SELECT * FROM link_types WHERE id = $1 AND is_social = TRUE", item_id
    )
    if not item:
        raise HTTPException(status_code=404, detail="Platform not found")
    return templates.TemplateResponse(
        request,
        "admin/lookups/form.html",
        {
            "user": user,
            "active_section": "lookups",
            "kind": "platforms",
            "item": item,
            "org_dup_count": org_dup_count,
        },
    )


@router.post("/platforms/{item_id}/edit/")
async def platform_update(
    item_id: str,
    request: Request,
    display_name: str = Form(...),
    slug: str = Form(...),
    user: AdminUser | RedirectResponse = Depends(get_admin_user),
    db=Depends(get_db),
):
    """Update a social link type."""
    redirect, user = check_auth(user)
    if redirect:
        return redirect
    item = await db.fetchrow(
        "SELECT id FROM link_types WHERE id = $1 AND is_social = TRUE", item_id
    )
    if not item:
        raise HTTPException(status_code=404, detail="Platform not found")
    await db.execute(
        "UPDATE link_types SET display_name = $1, slug = $2 WHERE id = $3",
        display_name, slug or None, item_id,
    )
    return RedirectResponse("/admin/lookups/platforms/", status_code=303)


@router.delete("/platforms/{item_id}/")
async def platform_delete(
    item_id: str,
    request: Request,
    user: AdminUser | RedirectResponse = Depends(get_admin_user),
    db=Depends(get_db),
):
    """Hard delete a social link type."""
    redirect, user = check_auth(user)
    if redirect:
        return redirect
    try:
        await db.execute("DELETE FROM link_types WHERE id = $1 AND is_social = TRUE", item_id)
    except asyncpg.ForeignKeyViolationError:
        raise HTTPException(status_code=409, detail="Cannot delete: record is in use")
    return HTMLResponse(content="", status_code=200)


# ---------------------------------------------------------------------------
# URL Types (now redirects to link_types with is_social=FALSE)
# ---------------------------------------------------------------------------


@router.get("/url-types/")
async def url_types_list(
    request: Request,
    user: AdminUser | RedirectResponse = Depends(get_admin_user),
    db=Depends(get_db),
    org_dup_count: int = Depends(get_org_dup_count),
):
    """List all non-social link types (is_social=FALSE)."""
    redirect, user = check_auth(user)
    if redirect:
        return redirect
    items = await db.fetch(
        "SELECT * FROM link_types WHERE is_social = FALSE ORDER BY display_name"
    )
    return templates.TemplateResponse(
        request,
        "admin/lookups/list.html",
        {
            "user": user,
            "active_section": "lookups",
            "kind": "url_types",
            "items": items,
            "org_dup_count": org_dup_count,
        },
    )


@router.get("/url-types/new/")
async def url_type_new_form(
    request: Request,
    user: AdminUser | RedirectResponse = Depends(get_admin_user),
    org_dup_count: int = Depends(get_org_dup_count),
):
    """New URL type form."""
    redirect, user = check_auth(user)
    if redirect:
        return redirect
    return templates.TemplateResponse(
        request,
        "admin/lookups/form.html",
        {
            "user": user,
            "active_section": "lookups",
            "kind": "url_types",
            "item": None,
            "org_dup_count": org_dup_count,
        },
    )


@router.post("/url-types/new/")
async def url_type_create(
    request: Request,
    display_name: str = Form(...),
    slug: str = Form(...),
    user: AdminUser | RedirectResponse = Depends(get_admin_user),
    db=Depends(get_db),
):
    """Create a new non-social link type."""
    redirect, user = check_auth(user)
    if redirect:
        return redirect
    uid = generate_id()
    await db.execute(
        "INSERT INTO link_types (id, display_name, slug, is_social) VALUES ($1, $2, $3, FALSE)",
        uid, display_name, slug or None,
    )
    return RedirectResponse("/admin/lookups/url-types/", status_code=303)


@router.get("/url-types/{item_id}/edit/")
async def url_type_edit_form(
    item_id: str,
    request: Request,
    user: AdminUser | RedirectResponse = Depends(get_admin_user),
    db=Depends(get_db),
    org_dup_count: int = Depends(get_org_dup_count),
):
    """Edit URL type form."""
    redirect, user = check_auth(user)
    if redirect:
        return redirect
    item = await db.fetchrow(
        "SELECT * FROM link_types WHERE id = $1 AND is_social = FALSE", item_id
    )
    if not item:
        raise HTTPException(status_code=404, detail="URL type not found")
    return templates.TemplateResponse(
        request,
        "admin/lookups/form.html",
        {
            "user": user,
            "active_section": "lookups",
            "kind": "url_types",
            "item": item,
            "org_dup_count": org_dup_count,
        },
    )


@router.post("/url-types/{item_id}/edit/")
async def url_type_update(
    item_id: str,
    request: Request,
    display_name: str = Form(...),
    slug: str = Form(...),
    user: AdminUser | RedirectResponse = Depends(get_admin_user),
    db=Depends(get_db),
):
    """Update a non-social link type."""
    redirect, user = check_auth(user)
    if redirect:
        return redirect
    item = await db.fetchrow(
        "SELECT id FROM link_types WHERE id = $1 AND is_social = FALSE", item_id
    )
    if not item:
        raise HTTPException(status_code=404, detail="URL type not found")
    await db.execute(
        "UPDATE link_types SET display_name = $1, slug = $2 WHERE id = $3",
        display_name, slug or None, item_id,
    )
    return RedirectResponse("/admin/lookups/url-types/", status_code=303)


@router.delete("/url-types/{item_id}/")
async def url_type_delete(
    item_id: str,
    request: Request,
    user: AdminUser | RedirectResponse = Depends(get_admin_user),
    db=Depends(get_db),
):
    """Hard delete a non-social link type."""
    redirect, user = check_auth(user)
    if redirect:
        return redirect
    try:
        await db.execute(
            "DELETE FROM link_types WHERE id = $1 AND is_social = FALSE", item_id
        )
    except asyncpg.ForeignKeyViolationError:
        raise HTTPException(status_code=409, detail="Cannot delete: record is in use")
    return HTMLResponse(content="", status_code=200)


# ---------------------------------------------------------------------------
# Entity Identifier Types
# ---------------------------------------------------------------------------


@router.get("/identifier-types/")
async def identifier_types_list(
    request: Request,
    user: AdminUser | RedirectResponse = Depends(get_admin_user),
    db=Depends(get_db),
    org_dup_count: int = Depends(get_org_dup_count),
):
    """List all entity identifier types."""
    redirect, user = check_auth(user)
    if redirect:
        return redirect
    items = await db.fetch(
        "SELECT * FROM entity_identifier_types ORDER BY display_name"
    )
    return templates.TemplateResponse(
        request,
        "admin/lookups/list.html",
        {
            "user": user,
            "active_section": "lookups",
            "kind": "identifier_types",
            "items": items,
            "org_dup_count": org_dup_count,
        },
    )


@router.get("/identifier-types/new/")
async def identifier_type_new_form(
    request: Request,
    user: AdminUser | RedirectResponse = Depends(get_admin_user),
    org_dup_count: int = Depends(get_org_dup_count),
):
    """New identifier type form."""
    redirect, user = check_auth(user)
    if redirect:
        return redirect
    return templates.TemplateResponse(
        request,
        "admin/lookups/form.html",
        {
            "user": user,
            "active_section": "lookups",
            "kind": "identifier_types",
            "item": None,
            "org_dup_count": org_dup_count,
        },
    )


@router.post("/identifier-types/new/")
async def identifier_type_create(
    request: Request,
    display_name: str = Form(...),
    slug: str = Form(...),
    full_name: str = Form(...),
    entity_type: str = Form(...),
    user: AdminUser | RedirectResponse = Depends(get_admin_user),
    db=Depends(get_db),
):
    """Create a new entity identifier type."""
    redirect, user = check_auth(user)
    if redirect:
        return redirect
    iid = generate_id()
    await db.execute(
        "INSERT INTO entity_identifier_types"
        " (id, display_name, slug, full_name, entity_type)"
        " VALUES ($1, $2, $3, $4, $5)",
        iid, display_name, slug or None, full_name or None, entity_type,
    )
    return RedirectResponse("/admin/lookups/identifier-types/", status_code=303)


@router.get("/identifier-types/{item_id}/edit/")
async def identifier_type_edit_form(
    item_id: str,
    request: Request,
    user: AdminUser | RedirectResponse = Depends(get_admin_user),
    db=Depends(get_db),
    org_dup_count: int = Depends(get_org_dup_count),
):
    """Edit identifier type form."""
    redirect, user = check_auth(user)
    if redirect:
        return redirect
    item = await db.fetchrow(
        "SELECT * FROM entity_identifier_types WHERE id = $1", item_id
    )
    if not item:
        raise HTTPException(status_code=404, detail="Identifier type not found")
    return templates.TemplateResponse(
        request,
        "admin/lookups/form.html",
        {
            "user": user,
            "active_section": "lookups",
            "kind": "identifier_types",
            "item": item,
            "org_dup_count": org_dup_count,
        },
    )


@router.post("/identifier-types/{item_id}/edit/")
async def identifier_type_update(
    item_id: str,
    request: Request,
    display_name: str = Form(...),
    slug: str = Form(...),
    full_name: str = Form(...),
    entity_type: str = Form(...),
    user: AdminUser | RedirectResponse = Depends(get_admin_user),
    db=Depends(get_db),
):
    """Update an entity identifier type."""
    redirect, user = check_auth(user)
    if redirect:
        return redirect
    item = await db.fetchrow(
        "SELECT id FROM entity_identifier_types WHERE id = $1", item_id
    )
    if not item:
        raise HTTPException(status_code=404, detail="Identifier type not found")
    await db.execute(
        "UPDATE entity_identifier_types"
        " SET display_name = $1, slug = $2, full_name = $3, entity_type = $4"
        " WHERE id = $5",
        display_name, slug or None, full_name or None, entity_type, item_id,
    )
    return RedirectResponse("/admin/lookups/identifier-types/", status_code=303)


@router.delete("/identifier-types/{item_id}/")
async def identifier_type_delete(
    item_id: str,
    request: Request,
    user: AdminUser | RedirectResponse = Depends(get_admin_user),
    db=Depends(get_db),
):
    """Hard delete an entity identifier type."""
    redirect, user = check_auth(user)
    if redirect:
        return redirect
    try:
        await db.execute(
            "DELETE FROM entity_identifier_types WHERE id = $1", item_id
        )
    except asyncpg.ForeignKeyViolationError:
        raise HTTPException(status_code=409, detail="Cannot delete: record is in use")
    return HTMLResponse(content="", status_code=200)
