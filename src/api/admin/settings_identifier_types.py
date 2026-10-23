"""Admin settings: entity identifier type CRUD views."""

import asyncpg
from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from markupsafe import escape

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
from src.core.types import VALID_ENTITY_TYPES

templates = Jinja2Templates(directory="src/templates")
router = APIRouter(prefix="/settings/identifier-types", tags=["admin-settings-identifier-types"])

_PAGE = "/admin/settings/identifier-types/"
_ROW = "identifier-type-row"


async def fetch_identifier_types(db) -> list:
    """Every identifier type with its usage count — the table's one query (#687)."""
    return await db.fetch(
        """
        SELECT eit.*, COUNT(i.id) AS usage_count
        FROM entity_identifier_types eit
        LEFT JOIN identifiers i ON i.entity_identifier_type_id = eit.id
        GROUP BY eit.id
        ORDER BY eit.display_name, eit.id
        """
    )


def _validate(display_name: str, slug: str, full_name: str, entity_type: str) -> str | None:
    """The form's error message, or None when the values are writable."""
    if not display_name:
        return "Display name is required."
    if not slug:
        return "Slug is required."
    if not full_name:
        return "Full name is required."
    if entity_type not in VALID_ENTITY_TYPES:
        return f"Entity type must be one of: {', '.join(VALID_ENTITY_TYPES)}."
    return None


def _form_row(request: Request, eit, form: dict, error: str | None, status_code: int = 200):
    """The new/edit form row; an error re-render lands on that row only (#683)."""
    row_id = f"{_ROW}-{eit['id']}" if eit else f"{_ROW}-new"
    return templates.TemplateResponse(
        request,
        "admin/settings/partials/_identifier_type_edit_row.html",
        {"eit": eit, "form": form, "error": error, "entity_types": VALID_ENTITY_TYPES},
        **form_row_status(request, {"form": error} if error else {}, status_code, row_id),
    )


async def _rows_response(request: Request, db, headers: dict):
    """The whole tbody: every write answers with it, so the empty row follows (#683)."""
    return templates.TemplateResponse(
        request,
        "admin/settings/partials/_identifier_type_rows.html",
        {"rows": await fetch_identifier_types(db)},
        headers=headers,
    )


async def _write(db, slug: str, sql: str, *args) -> str | None:
    """Run an INSERT/UPDATE under a savepoint; a slug clash becomes a form error.

    The savepoint aborts only this write, keeping the ambient transaction (and so
    the error response) usable (#288).
    """
    try:
        async with db.transaction():
            await db.execute(sql, *args)
    except asyncpg.UniqueViolationError:
        return f"Slug \u201c{slug}\u201d is already in use."
    return None


def _base_ctx(user, active_section: str = "settings"):
    return {
        "user": user,
        "active_section": active_section,
    }


@router.get("/")
async def identifier_types_page(
    request: Request,
    flash: str | None = Query(None),
    user: AdminUser = Depends(get_admin_user),
    db=Depends(get_db),
):
    rows = await fetch_identifier_types(db)
    flash_msg, resp_headers = resolve_query_flash(request, {}, flash)
    return templates.TemplateResponse(
        request,
        "admin/settings/identifier_types.html",
        {
            **_base_ctx(user, "settings_identifier_types"),
            "rows": rows,
            "flash_msg": flash_msg,
        },
        headers=resp_headers,
    )


@router.get("/new-row/")
async def identifier_type_new_row(
    request: Request,
    user: AdminUser = Depends(get_admin_user),
):
    return _form_row(request, None, {}, None)


@router.post("/")
async def identifier_type_create(
    request: Request,
    display_name: str = Form(""),
    slug: str = Form(""),
    full_name: str = Form(""),
    entity_type: str = Form(""),
    user: AdminUser = Depends(get_admin_user),
    db=Depends(get_db),
):
    form = {
        "display_name": display_name.strip(),
        "slug": slug.strip(),
        "full_name": full_name.strip(),
        "entity_type": entity_type,
    }
    error = _validate(**form) or await _write(
        db,
        form["slug"],
        "INSERT INTO entity_identifier_types"
        " (id, display_name, slug, full_name, entity_type)"
        " VALUES ($1, $2, $3, $4, $5)",
        generate_id(),
        *form.values(),
    )
    if error:
        return _form_row(request, None, form, error, 422)
    if not is_htmx(request):
        return RedirectResponse(with_flash(_PAGE, "saved"), status_code=303)
    return await _rows_response(
        request,
        db,
        flash_trigger(
            "success", f"Identifier type <strong>{escape(form['display_name'])}</strong> added."
        ),
    )


async def _get_or_404(db, item_id: str):
    eit = await db.fetchrow("SELECT * FROM entity_identifier_types WHERE id=$1", item_id)
    if not eit:
        raise HTTPException(status_code=404)
    return eit


@router.get("/{item_id}/edit-row/")
async def identifier_type_edit_row_get(
    item_id: str,
    request: Request,
    user: AdminUser = Depends(get_admin_user),
    db=Depends(get_db),
):
    return _form_row(request, await _get_or_404(db, item_id), {}, None)


@router.post("/{item_id}/edit-row/")
async def identifier_type_edit_row_post(
    item_id: str,
    request: Request,
    display_name: str = Form(""),
    slug: str = Form(""),
    full_name: str = Form(""),
    entity_type: str = Form(""),
    user: AdminUser = Depends(get_admin_user),
    db=Depends(get_db),
):
    eit = await _get_or_404(db, item_id)
    form = {
        "display_name": display_name.strip(),
        "slug": slug.strip(),
        "full_name": full_name.strip(),
        "entity_type": entity_type,
    }
    error = _validate(**form) or await _write(
        db,
        form["slug"],
        "UPDATE entity_identifier_types"
        " SET display_name=$1, slug=$2, full_name=$3, entity_type=$4"
        " WHERE id=$5",
        *form.values(),
        item_id,
    )
    if error:
        return _form_row(request, eit, form, error, 422)
    if not is_htmx(request):
        return RedirectResponse(with_flash(_PAGE, "saved"), status_code=303)
    return await _rows_response(
        request,
        db,
        flash_trigger(
            "success", f"Identifier type <strong>{escape(form['display_name'])}</strong> saved."
        ),
    )


@router.get("/{item_id}/read-row/")
async def identifier_type_read_row(
    item_id: str,
    request: Request,
    user: AdminUser = Depends(get_admin_user),
    db=Depends(get_db),
):
    row = await db.fetchrow(
        """
        SELECT eit.*, COUNT(i.id) AS usage_count
        FROM entity_identifier_types eit
        LEFT JOIN identifiers i ON i.entity_identifier_type_id = eit.id
        WHERE eit.id = $1
        GROUP BY eit.id
        """,
        item_id,
    )
    if not row:
        raise HTTPException(status_code=404)
    return templates.TemplateResponse(
        request,
        "admin/settings/partials/_identifier_type_row.html",
        {"eit": row},
    )


@router.delete("/{item_id}/")
async def identifier_type_delete(
    item_id: str,
    request: Request,
    user: AdminUser = Depends(get_admin_user),
    db=Depends(get_db),
):
    await _get_or_404(db, item_id)
    try:
        # Savepoint so an FK violation (type in use) aborts only this delete, not
        # the ambient transaction — keeps the except-block response usable (#288).
        async with db.transaction():
            await db.execute("DELETE FROM entity_identifier_types WHERE id=$1", item_id)
    except asyncpg.ForeignKeyViolationError:
        if not is_htmx(request):
            raise HTTPException(status_code=409, detail="Cannot delete: record is in use")
        return htmx_refusal("Cannot delete: this identifier type is in use.")
    if not is_htmx(request):
        return RedirectResponse(with_flash(_PAGE, "removed"), status_code=303)
    return await _rows_response(request, db, flash_trigger("success", "Identifier type deleted."))
