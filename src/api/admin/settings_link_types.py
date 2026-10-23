"""Admin settings: link type CRUD views."""

import asyncpg
from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from markupsafe import escape

from src.api.admin._settings_shared import write_unique_slug
from src.api.admin.deps import (
    AdminUser,
    flash_trigger,
    form_row_status,
    get_admin_user,
    get_db,
    htmx_refusal,
    is_htmx,
    resolve_query_flash,
    with_flash,
)
from src.core.db import generate_id

templates = Jinja2Templates(directory="src/templates")
router = APIRouter(prefix="/settings/link-types", tags=["admin-settings-link-types"])

_PAGE = "/admin/settings/link-types/"
_SCOPE_MAP = {"general": False, "social": True}


def _scope_to_is_social(scope: str) -> bool:
    if scope not in _SCOPE_MAP:
        raise HTTPException(status_code=404, detail="Invalid scope")
    return _SCOPE_MAP[scope]


async def fetch_link_types(db, is_social: bool) -> list:
    """One scope's link types with usage counts — the table's one query (#687)."""
    return await db.fetch(
        """
        SELECT lt.*, COUNT(l.id) AS usage_count
        FROM link_types lt
        LEFT JOIN links l ON l.link_type_id = lt.id
        WHERE lt.is_social = $1
        GROUP BY lt.id
        ORDER BY lt.display_name, lt.id
        """,
        is_social,
    )


def _validate(display_name: str, slug: str) -> str | None:
    """The form's error message, or None when the values are writable."""
    if not display_name:
        return "Display name is required."
    if not slug:
        return "Slug is required."
    return None


def _form_row(
    request: Request, scope: str, lt, form: dict, error: str | None, status_code: int = 200
):
    """The new/edit form row; an error re-render lands on that row only (#683)."""
    row_id = f"link-type-row-{lt['id']}" if lt else f"{scope}-link-type-row-new"
    return templates.TemplateResponse(
        request,
        "admin/settings/partials/_link_type_edit_row.html",
        {"lt": lt, "scope": scope, "form": form, "error": error},
        **form_row_status(request, {"form": error} if error else {}, status_code, row_id),
    )


async def _rows_response(request: Request, db, scope: str, headers: dict):
    """One scope's whole tbody: every write answers with it, so the empty row follows."""
    return templates.TemplateResponse(
        request,
        "admin/settings/partials/_link_type_rows.html",
        {"rows": await fetch_link_types(db, _SCOPE_MAP[scope]), "scope": scope},
        headers=headers,
    )


def _base_ctx(user, active_section: str = "settings"):
    return {
        "user": user,
        "active_section": active_section,
    }


@router.get("/")
async def link_types_page(
    request: Request,
    flash: str | None = Query(None),
    user: AdminUser = Depends(get_admin_user),
    db=Depends(get_db),
):
    general = await fetch_link_types(db, False)
    social = await fetch_link_types(db, True)
    flash_msg, resp_headers = resolve_query_flash(request, {}, flash)
    return templates.TemplateResponse(
        request,
        "admin/settings/link_types.html",
        {
            **_base_ctx(user, "settings_link_types"),
            "general": general,
            "social": social,
            "flash_msg": flash_msg,
        },
        headers=resp_headers,
    )


@router.get("/{scope}/new-row/")
async def link_type_new_row(
    scope: str,
    request: Request,
    user: AdminUser = Depends(get_admin_user),
):
    _scope_to_is_social(scope)
    return _form_row(request, scope, None, {}, None)


@router.post("/{scope}/")
async def link_type_create(
    scope: str,
    request: Request,
    display_name: str = Form(""),
    slug: str = Form(""),
    user: AdminUser = Depends(get_admin_user),
    db=Depends(get_db),
):
    is_social = _scope_to_is_social(scope)
    form = {"display_name": display_name.strip(), "slug": slug.strip()}
    error = _validate(**form) or await write_unique_slug(
        db,
        form["slug"],
        "INSERT INTO link_types (id, display_name, slug, is_social) VALUES ($1, $2, $3, $4)",
        generate_id(),
        form["display_name"],
        form["slug"],
        is_social,
    )
    if error:
        return _form_row(request, scope, None, form, error, 422)
    if not is_htmx(request):
        return RedirectResponse(with_flash(_PAGE, "saved"), status_code=303)
    return await _rows_response(
        request,
        db,
        scope,
        flash_trigger(
            "success", f"Link type <strong>{escape(form['display_name'])}</strong> added."
        ),
    )


async def _get_or_404(db, item_id: str, is_social: bool):
    lt = await db.fetchrow(
        "SELECT * FROM link_types WHERE id=$1 AND is_social=$2", item_id, is_social
    )
    if not lt:
        raise HTTPException(status_code=404)
    return lt


@router.get("/{scope}/{item_id}/edit-row/")
async def link_type_edit_row_get(
    scope: str,
    item_id: str,
    request: Request,
    user: AdminUser = Depends(get_admin_user),
    db=Depends(get_db),
):
    lt = await _get_or_404(db, item_id, _scope_to_is_social(scope))
    return _form_row(request, scope, lt, {}, None)


@router.post("/{scope}/{item_id}/edit-row/")
async def link_type_edit_row_post(
    scope: str,
    item_id: str,
    request: Request,
    display_name: str = Form(""),
    slug: str = Form(""),
    user: AdminUser = Depends(get_admin_user),
    db=Depends(get_db),
):
    lt = await _get_or_404(db, item_id, _scope_to_is_social(scope))
    form = {"display_name": display_name.strip(), "slug": slug.strip()}
    error = _validate(**form) or await write_unique_slug(
        db,
        form["slug"],
        "UPDATE link_types SET display_name=$1, slug=$2 WHERE id=$3",
        form["display_name"],
        form["slug"],
        item_id,
    )
    if error:
        return _form_row(request, scope, lt, form, error, 422)
    if not is_htmx(request):
        return RedirectResponse(with_flash(_PAGE, "saved"), status_code=303)
    return await _rows_response(
        request,
        db,
        scope,
        flash_trigger(
            "success", f"Link type <strong>{escape(form['display_name'])}</strong> saved."
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
    await _get_or_404(db, item_id, _scope_to_is_social(scope))
    try:
        # Savepoint so an FK violation (type in use) aborts only this delete, not
        # the ambient transaction — keeps the except-block response usable (#288).
        async with db.transaction():
            await db.execute("DELETE FROM link_types WHERE id=$1", item_id)
    except asyncpg.ForeignKeyViolationError:
        if not is_htmx(request):
            raise HTTPException(status_code=409, detail="Cannot delete: record is in use")
        return htmx_refusal("Cannot delete: this link type is in use.")
    if not is_htmx(request):
        return RedirectResponse(with_flash(_PAGE, "removed"), status_code=303)
    return await _rows_response(request, db, scope, flash_trigger("success", "Link type deleted."))
