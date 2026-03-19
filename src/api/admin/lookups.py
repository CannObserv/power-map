"""Admin views for lookup tables: platforms, url_types, entity_identifier_types."""

import asyncpg
from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from src.api.admin.deps import AdminUser, get_admin_user, get_db
from src.core.db import generate_id

templates = Jinja2Templates(directory="src/templates")
router = APIRouter(prefix="/lookups", tags=["admin-lookups"])


def _check_auth(user: AdminUser | RedirectResponse):
    """Return (redirect, user) tuple. If redirect is not None, return it immediately."""
    if isinstance(user, RedirectResponse):
        return user, None
    return None, user


# ---------------------------------------------------------------------------
# Platforms
# ---------------------------------------------------------------------------


@router.get("/platforms/")
async def platforms_list(
    request: Request,
    user: AdminUser | RedirectResponse = Depends(get_admin_user),
    db=Depends(get_db),
):
    """List all platforms."""
    redirect, user = _check_auth(user)
    if redirect:
        return redirect
    items = await db.fetch("SELECT * FROM platforms ORDER BY display_name")
    return templates.TemplateResponse(
        request,
        "admin/lookups/list.html",
        {
            "user": user,
            "active_section": "lookups",
            "kind": "platforms",
            "items": items,
        },
    )


@router.get("/platforms/new/")
async def platform_new_form(
    request: Request,
    user: AdminUser | RedirectResponse = Depends(get_admin_user),
):
    """New platform form."""
    redirect, user = _check_auth(user)
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
    """Create a new platform."""
    redirect, user = _check_auth(user)
    if redirect:
        return redirect
    pid = generate_id()
    await db.execute(
        "INSERT INTO platforms (id, display_name, slug) VALUES ($1, $2, $3)",
        pid, display_name, slug or None,
    )
    return RedirectResponse("/admin/lookups/platforms/", status_code=303)


@router.get("/platforms/{item_id}/edit/")
async def platform_edit_form(
    item_id: str,
    request: Request,
    user: AdminUser | RedirectResponse = Depends(get_admin_user),
    db=Depends(get_db),
):
    """Edit platform form."""
    redirect, user = _check_auth(user)
    if redirect:
        return redirect
    item = await db.fetchrow("SELECT * FROM platforms WHERE id = $1", item_id)
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
    """Update a platform."""
    redirect, user = _check_auth(user)
    if redirect:
        return redirect
    item = await db.fetchrow("SELECT id FROM platforms WHERE id = $1", item_id)
    if not item:
        raise HTTPException(status_code=404, detail="Platform not found")
    await db.execute(
        "UPDATE platforms SET display_name = $1, slug = $2 WHERE id = $3",
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
    """Hard delete a platform."""
    redirect, user = _check_auth(user)
    if redirect:
        return redirect
    try:
        await db.execute("DELETE FROM platforms WHERE id = $1", item_id)
    except asyncpg.ForeignKeyViolationError:
        raise HTTPException(status_code=409, detail="Cannot delete: record is in use")
    return HTMLResponse(content="", status_code=200)


# ---------------------------------------------------------------------------
# URL Types
# ---------------------------------------------------------------------------


@router.get("/url-types/")
async def url_types_list(
    request: Request,
    user: AdminUser | RedirectResponse = Depends(get_admin_user),
    db=Depends(get_db),
):
    """List all URL types."""
    redirect, user = _check_auth(user)
    if redirect:
        return redirect
    items = await db.fetch("SELECT * FROM url_types ORDER BY display_name")
    return templates.TemplateResponse(
        request,
        "admin/lookups/list.html",
        {
            "user": user,
            "active_section": "lookups",
            "kind": "url_types",
            "items": items,
        },
    )


@router.get("/url-types/new/")
async def url_type_new_form(
    request: Request,
    user: AdminUser | RedirectResponse = Depends(get_admin_user),
):
    """New URL type form."""
    redirect, user = _check_auth(user)
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
    """Create a new URL type."""
    redirect, user = _check_auth(user)
    if redirect:
        return redirect
    uid = generate_id()
    await db.execute(
        "INSERT INTO url_types (id, display_name, slug) VALUES ($1, $2, $3)",
        uid, display_name, slug or None,
    )
    return RedirectResponse("/admin/lookups/url-types/", status_code=303)


@router.get("/url-types/{item_id}/edit/")
async def url_type_edit_form(
    item_id: str,
    request: Request,
    user: AdminUser | RedirectResponse = Depends(get_admin_user),
    db=Depends(get_db),
):
    """Edit URL type form."""
    redirect, user = _check_auth(user)
    if redirect:
        return redirect
    item = await db.fetchrow("SELECT * FROM url_types WHERE id = $1", item_id)
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
    """Update a URL type."""
    redirect, user = _check_auth(user)
    if redirect:
        return redirect
    item = await db.fetchrow("SELECT id FROM url_types WHERE id = $1", item_id)
    if not item:
        raise HTTPException(status_code=404, detail="URL type not found")
    await db.execute(
        "UPDATE url_types SET display_name = $1, slug = $2 WHERE id = $3",
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
    """Hard delete a URL type."""
    redirect, user = _check_auth(user)
    if redirect:
        return redirect
    try:
        await db.execute("DELETE FROM url_types WHERE id = $1", item_id)
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
):
    """List all entity identifier types."""
    redirect, user = _check_auth(user)
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
        },
    )


@router.get("/identifier-types/new/")
async def identifier_type_new_form(
    request: Request,
    user: AdminUser | RedirectResponse = Depends(get_admin_user),
):
    """New identifier type form."""
    redirect, user = _check_auth(user)
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
    redirect, user = _check_auth(user)
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
):
    """Edit identifier type form."""
    redirect, user = _check_auth(user)
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
    redirect, user = _check_auth(user)
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
    redirect, user = _check_auth(user)
    if redirect:
        return redirect
    try:
        await db.execute(
            "DELETE FROM entity_identifier_types WHERE id = $1", item_id
        )
    except asyncpg.ForeignKeyViolationError:
        raise HTTPException(status_code=409, detail="Cannot delete: record is in use")
    return HTMLResponse(content="", status_code=200)
