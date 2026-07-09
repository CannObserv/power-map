"""Admin settings: link type CRUD views."""

import asyncpg
from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from markupsafe import escape

from src.api.admin.deps import (
    AdminUser,
    flash_trigger,
    get_admin_user,
    get_db,
    is_htmx,
)
from src.core.db import generate_id

templates = Jinja2Templates(directory="src/templates")
router = APIRouter(prefix="/settings/link-types", tags=["admin-settings-link-types"])

_SCOPE_MAP = {"general": False, "social": True}


def _scope_to_is_social(scope: str) -> bool:
    if scope not in _SCOPE_MAP:
        raise HTTPException(status_code=404, detail="Invalid scope")
    return _SCOPE_MAP[scope]


async def _fetch_link_types(db, is_social: bool) -> list:
    return await db.fetch(
        """
        SELECT lt.*, COUNT(l.id) AS usage_count
        FROM link_types lt
        LEFT JOIN links l ON l.link_type_id = lt.id
        WHERE lt.is_social = $1
        GROUP BY lt.id
        ORDER BY lt.display_name
        """,
        is_social,
    )


def _base_ctx(user, active_section: str = "settings"):
    return {
        "user": user,
        "active_section": active_section,
    }


@router.get("/")
async def link_types_page(
    request: Request,
    user: AdminUser = Depends(get_admin_user),
    db=Depends(get_db),
):
    general = await _fetch_link_types(db, False)
    social = await _fetch_link_types(db, True)
    return templates.TemplateResponse(
        request,
        "admin/settings/link_types.html",
        {
            **_base_ctx(user, "settings_link_types"),
            "general": general,
            "social": social,
        },  # noqa: E501
    )


@router.get("/{scope}/new-row/")
async def link_type_new_row(
    scope: str,
    request: Request,
    user: AdminUser = Depends(get_admin_user),
):
    is_social = _scope_to_is_social(scope)
    return templates.TemplateResponse(
        request,
        "admin/settings/partials/_link_type_edit_row.html",
        {"lt": None, "scope": scope, "is_social": is_social},
    )


@router.post("/{scope}/")
async def link_type_create(
    scope: str,
    request: Request,
    display_name: str = Form(...),
    slug: str = Form(...),
    user: AdminUser = Depends(get_admin_user),
    db=Depends(get_db),
):
    is_social = _scope_to_is_social(scope)
    slug_val = slug.strip()
    if not slug_val:
        raise HTTPException(status_code=422, detail="slug is required")
    lid = generate_id()
    await db.execute(
        "INSERT INTO link_types (id, display_name, slug, is_social) VALUES ($1, $2, $3, $4)",
        lid,
        display_name.strip(),
        slug_val,
        is_social,
    )
    rows = await _fetch_link_types(db, is_social)
    if not is_htmx(request):
        return RedirectResponse("/admin/settings/link-types/", status_code=303)
    return templates.TemplateResponse(
        request,
        "admin/settings/partials/_link_type_rows.html",
        {"rows": rows, "scope": scope},
        headers=flash_trigger(
            "success", f"Link type <strong>{escape(display_name.strip())}</strong> added."
        ),
    )


@router.get("/{scope}/{item_id}/edit-row/")
async def link_type_edit_row_get(
    scope: str,
    item_id: str,
    request: Request,
    user: AdminUser = Depends(get_admin_user),
    db=Depends(get_db),
):
    is_social = _scope_to_is_social(scope)
    lt = await db.fetchrow(
        "SELECT * FROM link_types WHERE id=$1 AND is_social=$2", item_id, is_social
    )
    if not lt:
        raise HTTPException(status_code=404)
    return templates.TemplateResponse(
        request,
        "admin/settings/partials/_link_type_edit_row.html",
        {"lt": lt, "scope": scope, "is_social": is_social},
    )


@router.post("/{scope}/{item_id}/edit-row/")
async def link_type_edit_row_post(
    scope: str,
    item_id: str,
    request: Request,
    display_name: str = Form(...),
    slug: str = Form(...),
    user: AdminUser = Depends(get_admin_user),
    db=Depends(get_db),
):
    is_social = _scope_to_is_social(scope)
    slug_val = slug.strip()
    if not slug_val:
        raise HTTPException(status_code=422, detail="slug is required")
    lt = await db.fetchrow(
        "SELECT id FROM link_types WHERE id=$1 AND is_social=$2", item_id, is_social
    )
    if not lt:
        raise HTTPException(status_code=404)
    await db.execute(
        "UPDATE link_types SET display_name=$1, slug=$2 WHERE id=$3",
        display_name.strip(),
        slug_val,
        item_id,
    )
    row = await db.fetchrow(
        """
        SELECT lt.*, COUNT(l.id) AS usage_count
        FROM link_types lt
        LEFT JOIN links l ON l.link_type_id = lt.id
        WHERE lt.id = $1
        GROUP BY lt.id
        """,
        item_id,
    )
    if not is_htmx(request):
        return RedirectResponse("/admin/settings/link-types/", status_code=303)
    return templates.TemplateResponse(
        request,
        "admin/settings/partials/_link_type_row.html",
        {"lt": row, "scope": scope},
        headers=flash_trigger(
            "success", f"Link type <strong>{escape(display_name.strip())}</strong> saved."
        ),
    )


@router.get("/{scope}/{item_id}/read-row/")
async def link_type_read_row(
    scope: str,
    item_id: str,
    request: Request,
    user: AdminUser = Depends(get_admin_user),
    db=Depends(get_db),
):
    is_social = _scope_to_is_social(scope)
    row = await db.fetchrow(
        """
        SELECT lt.*, COUNT(l.id) AS usage_count
        FROM link_types lt
        LEFT JOIN links l ON l.link_type_id = lt.id
        WHERE lt.id = $1 AND lt.is_social = $2
        GROUP BY lt.id
        """,
        item_id,
        is_social,
    )
    if not row:
        raise HTTPException(status_code=404)
    return templates.TemplateResponse(
        request,
        "admin/settings/partials/_link_type_row.html",
        {"lt": row, "scope": scope},
    )


@router.delete("/{scope}/{item_id}/")
async def link_type_delete(
    scope: str,
    item_id: str,
    request: Request,
    user: AdminUser = Depends(get_admin_user),
    db=Depends(get_db),
):
    is_social = _scope_to_is_social(scope)
    existing = await db.fetchrow(
        "SELECT id FROM link_types WHERE id=$1 AND is_social=$2", item_id, is_social
    )
    if not existing:
        raise HTTPException(status_code=404)
    try:
        # Savepoint so an FK violation (type in use) aborts only this delete, not
        # the ambient transaction — keeps the except-block response usable (#288).
        async with db.transaction():
            await db.execute("DELETE FROM link_types WHERE id=$1", item_id)
    except asyncpg.ForeignKeyViolationError:
        if not is_htmx(request):
            raise HTTPException(status_code=409, detail="Cannot delete: record is in use")
        return HTMLResponse(
            content="",
            status_code=200,
            headers=flash_trigger("error", "Cannot delete: this link type is in use."),
        )
    return HTMLResponse(
        content="",
        status_code=200,
        headers=flash_trigger("info", "Link type deleted."),
    )
