"""Admin settings: API key management CRUD views."""

import hashlib
import os

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from markupsafe import escape

from src.api.admin.deps import (
    AdminUser,
    flash_trigger,
    get_db,
    is_htmx,
    provision_app_user,
    resolve_query_flash,
    with_flash,
)
from src.core.db import generate_id

templates = Jinja2Templates(directory="src/templates")
router = APIRouter(prefix="/settings/api-keys", tags=["admin-settings-api-keys"])


def generate_api_key() -> tuple[str, str, str]:
    """Return (raw_key, key_hash, key_prefix).

    raw_key:    "pm_" + 32 hex chars (128-bit random via os.urandom)
    key_hash:   SHA-256 hex of raw_key — stored in DB; never returned after creation
    key_prefix: first 8 chars of raw_key — stored for display identification
    """
    raw_key = "pm_" + os.urandom(16).hex()
    key_hash = hashlib.sha256(raw_key.encode()).hexdigest()
    key_prefix = raw_key[:8]
    return raw_key, key_hash, key_prefix


def _base_ctx(user):
    return {
        "user": user,
        "active_section": "settings_api_keys",
    }


@router.get("/")
async def api_keys_list(
    request: Request,
    flash: str | None = Query(None),
    user: AdminUser = Depends(provision_app_user),
    db=Depends(get_db),
):
    keys = await db.fetch(
        "SELECT id, label, key_prefix, created_at, last_used_at"
        " FROM api_keys WHERE user_id=$1 ORDER BY created_at DESC",
        user.id,
    )
    flash_msg, resp_headers = resolve_query_flash(request, {}, flash)
    return templates.TemplateResponse(
        request,
        "admin/settings/api_keys.html",
        {**_base_ctx(user), "keys": keys, "flash_msg": flash_msg},
        headers=resp_headers,
    )


@router.get("/new-row/")
async def api_key_new_row(
    request: Request,
    user: AdminUser = Depends(provision_app_user),
):
    return templates.TemplateResponse(
        request,
        "admin/settings/partials/_api_key_edit_row.html",
        {"key": None},
    )


@router.post("/")
async def api_key_create(
    request: Request,
    label: str = Form(...),
    user: AdminUser = Depends(provision_app_user),
    db=Depends(get_db),
):
    label_val = label.strip()
    if not label_val:
        raise HTTPException(status_code=422, detail="label is required")
    raw_key, key_hash, key_prefix = generate_api_key()
    kid = generate_id()
    await db.execute(
        "INSERT INTO api_keys (id, user_id, label, key_prefix, key_hash) VALUES ($1,$2,$3,$4,$5)",
        kid,
        user.id,
        label_val,
        key_prefix,
        key_hash,
    )
    if not is_htmx(request):
        return RedirectResponse(with_flash("/admin/settings/api-keys/", "saved"), status_code=303)
    return templates.TemplateResponse(
        request,
        "admin/settings/partials/_api_key_new_key_modal.html",
        {"raw_key": raw_key, "label": label_val},
    )


@router.get("/{key_id}/edit-row/")
async def api_key_edit_row_get(
    key_id: str,
    request: Request,
    user: AdminUser = Depends(provision_app_user),
    db=Depends(get_db),
):
    key = await db.fetchrow(
        "SELECT id, label, key_prefix, created_at, last_used_at"
        " FROM api_keys WHERE id=$1 AND user_id=$2",
        key_id,
        user.id,
    )
    if not key:
        raise HTTPException(status_code=404)
    return templates.TemplateResponse(
        request,
        "admin/settings/partials/_api_key_edit_row.html",
        {"key": key},
    )


@router.post("/{key_id}/edit-row/")
async def api_key_edit_row_post(
    key_id: str,
    request: Request,
    label: str = Form(...),
    user: AdminUser = Depends(provision_app_user),
    db=Depends(get_db),
):
    label_val = label.strip()
    if not label_val:
        raise HTTPException(status_code=422, detail="label is required")
    key = await db.fetchrow("SELECT id FROM api_keys WHERE id=$1 AND user_id=$2", key_id, user.id)
    if not key:
        raise HTTPException(status_code=404)
    await db.execute("UPDATE api_keys SET label=$1 WHERE id=$2", label_val, key_id)
    row = await db.fetchrow(
        "SELECT id, label, key_prefix, created_at, last_used_at FROM api_keys WHERE id=$1",
        key_id,
    )
    if not is_htmx(request):
        return RedirectResponse(with_flash("/admin/settings/api-keys/", "saved"), status_code=303)
    return templates.TemplateResponse(
        request,
        "admin/settings/partials/_api_key_row.html",
        {"key": row},
        headers=flash_trigger("success", f"Key <strong>{escape(label_val)}</strong> renamed."),
    )


@router.get("/{key_id}/read-row/")
async def api_key_read_row(
    key_id: str,
    request: Request,
    user: AdminUser = Depends(provision_app_user),
    db=Depends(get_db),
):
    row = await db.fetchrow(
        "SELECT id, label, key_prefix, created_at, last_used_at"
        " FROM api_keys WHERE id=$1 AND user_id=$2",
        key_id,
        user.id,
    )
    if not row:
        raise HTTPException(status_code=404)
    return templates.TemplateResponse(
        request,
        "admin/settings/partials/_api_key_row.html",
        {"key": row},
    )


async def _fetch_scope_panel_ctx(key_id: str, db) -> dict:
    """Return template context dict for the scope panel partial.

    Queries current scopes and all available scope types for key_id.
    Caller must have already confirmed key ownership.
    """
    key = await db.fetchrow("SELECT id, label FROM api_keys WHERE id=$1", key_id)
    current_scopes = await db.fetch(
        "SELECT s.scope_id, t.display_name, t.description"
        " FROM api_key_scopes s"
        " JOIN api_key_scope_types t ON t.id = s.scope_id"
        " WHERE s.api_key_id = $1"
        " ORDER BY s.scope_id",
        key_id,
    )
    scope_types = await db.fetch(
        "SELECT id, display_name, description FROM api_key_scope_types ORDER BY id"
    )
    return {"key": key, "current_scopes": current_scopes, "scope_types": scope_types}


@router.get("/{key_id}/detail/")
async def api_key_detail(
    key_id: str,
    request: Request,
    user: AdminUser = Depends(provision_app_user),
    db=Depends(get_db),
):
    """Return scope detail panel partial for an API key."""
    key = await db.fetchrow("SELECT id FROM api_keys WHERE id=$1 AND user_id=$2", key_id, user.id)
    if not key:
        raise HTTPException(status_code=404)
    if not is_htmx(request):
        return RedirectResponse("/admin/settings/api-keys/", status_code=303)
    ctx = await _fetch_scope_panel_ctx(key_id, db)
    return templates.TemplateResponse(
        request,
        "admin/settings/partials/_api_key_scopes.html",
        ctx,
    )


@router.post("/{key_id}/scopes/{scope_id}/grant/")
async def api_key_scope_grant(
    key_id: str,
    scope_id: str,
    request: Request,
    user: AdminUser = Depends(provision_app_user),
    db=Depends(get_db),
):
    """Grant a scope to an API key (idempotent)."""
    key = await db.fetchrow("SELECT id FROM api_keys WHERE id=$1 AND user_id=$2", key_id, user.id)
    if not key:
        raise HTTPException(status_code=404)
    scope_type = await db.fetchrow("SELECT id FROM api_key_scope_types WHERE id = $1", scope_id)
    if not scope_type:
        raise HTTPException(status_code=422, detail="Unknown scope")
    if not is_htmx(request):
        return RedirectResponse(with_flash("/admin/settings/api-keys/", "saved"), status_code=303)
    await db.execute(
        "INSERT INTO api_key_scopes (api_key_id, scope_id, granted_by)"
        " VALUES ($1,$2,$3)"
        " ON CONFLICT DO NOTHING",
        key_id,
        scope_id,
        user.id,
    )
    ctx = await _fetch_scope_panel_ctx(key_id, db)
    return templates.TemplateResponse(
        request,
        "admin/settings/partials/_api_key_scopes.html",
        ctx,
        headers=flash_trigger("success", f"Scope <strong>{escape(scope_id)}</strong> granted."),
    )


@router.post("/{key_id}/scopes/{scope_id}/revoke/")
async def api_key_scope_revoke(
    key_id: str,
    scope_id: str,
    request: Request,
    user: AdminUser = Depends(provision_app_user),
    db=Depends(get_db),
):
    """Revoke a scope from an API key (no-op if not present)."""
    key = await db.fetchrow("SELECT id FROM api_keys WHERE id=$1 AND user_id=$2", key_id, user.id)
    if not key:
        raise HTTPException(status_code=404)
    if not is_htmx(request):
        return RedirectResponse(with_flash("/admin/settings/api-keys/", "removed"), status_code=303)
    await db.execute(
        "DELETE FROM api_key_scopes WHERE api_key_id=$1 AND scope_id=$2",
        key_id,
        scope_id,
    )
    ctx = await _fetch_scope_panel_ctx(key_id, db)
    return templates.TemplateResponse(
        request,
        "admin/settings/partials/_api_key_scopes.html",
        ctx,
        headers=flash_trigger("success", f"Scope <strong>{escape(scope_id)}</strong> revoked."),
    )


@router.delete("/{key_id}/")
async def api_key_delete(
    key_id: str,
    request: Request,
    user: AdminUser = Depends(provision_app_user),
    db=Depends(get_db),
):
    existing = await db.fetchrow(
        "SELECT id, label FROM api_keys WHERE id=$1 AND user_id=$2", key_id, user.id
    )
    if not existing:
        raise HTTPException(status_code=404)
    await db.execute("DELETE FROM api_keys WHERE id=$1", key_id)
    if not is_htmx(request):
        return RedirectResponse(with_flash("/admin/settings/api-keys/", "removed"), status_code=303)
    return HTMLResponse(
        content="",
        status_code=200,
        headers=flash_trigger(
            "success",
            f"Key <strong>{escape(existing['label'])}</strong> deleted.",
        ),
    )
