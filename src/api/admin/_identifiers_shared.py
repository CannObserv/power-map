"""Shared factory for entity identifiers CRUD routers (orgs and people)."""

from collections.abc import Callable

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from markupsafe import escape

from src.api.admin.deps import AdminUser, flash_trigger, get_admin_user, get_db, is_htmx
from src.core.db import generate_id

templates = Jinja2Templates(directory="src/templates")


def make_identifiers_router(
    *,
    entity_type: str,
    entity_id_key: str,
    prefix: str,
    tags: list[str],
    entity_table: str,
    entity_not_found_msg: str,
    tmpl_form_row: str,
    tmpl_read_row: str,
    detail_url: Callable[[str], str],
) -> APIRouter:
    """Return a configured identifiers APIRouter for the given entity type.

    Parameters
    ----------
    entity_type:
        DB value in entity_identifier_types.entity_type (e.g. ``'organization'``).
    entity_id_key:
        Template context key for the entity id (e.g. ``'org_id'`` or ``'person_id'``).
    prefix:
        Router URL prefix — must contain ``{entity_id}`` as the path variable.
    tags:
        FastAPI tags list.
    entity_table:
        Table name used to verify the parent entity exists.
    entity_not_found_msg:
        Detail string for 404 when parent entity is missing.
    tmpl_form_row:
        Template path for the identifier form row partial.
    tmpl_read_row:
        Template path for the identifier read row partial.
    detail_url:
        Callable accepting the entity id and returning the detail redirect URL.
    """
    router = APIRouter(prefix=prefix, tags=tags)

    # ---- helpers ----------------------------------------------------------------

    async def _get_entity_or_404(entity_id: str, db):
        row = await db.fetchrow(f"SELECT id FROM {entity_table} WHERE id=$1", entity_id)
        if not row:
            raise HTTPException(status_code=404, detail=entity_not_found_msg)
        return row

    async def _get_identifier_or_404(ident_id: str, entity_id: str, db):
        row = await db.fetchrow(
            """SELECT i.*, eit.display_name AS type_name, eit.full_name AS type_full_name
               FROM identifiers i
               JOIN entity_identifier_types eit ON eit.id = i.entity_identifier_type_id
               WHERE i.id=$1 AND i.entity_id=$2 AND eit.entity_type=$3""",
            ident_id,
            entity_id,
            entity_type,
        )
        if not row:
            raise HTTPException(status_code=404)
        return row

    def _ctx(entity_id: str, **extra) -> dict:
        """Build template context with the correct entity-id key."""
        return {entity_id_key: entity_id, **extra}

    # ---- routes -----------------------------------------------------------------

    @router.get("/new-row/")
    async def identifier_new_row(
        entity_id: str,
        request: Request,
        user: AdminUser = Depends(get_admin_user),
        db=Depends(get_db),
    ):
        """Return empty identifier form row."""
        await _get_entity_or_404(entity_id, db)
        ident_types = await db.fetch(
            "SELECT * FROM entity_identifier_types"
            " WHERE entity_type=$1 AND NOT is_internal ORDER BY display_name",
            entity_type,
        )
        return templates.TemplateResponse(
            request,
            tmpl_form_row,
            _ctx(entity_id, ident=None, ident_types=ident_types),
        )

    @router.post("/")
    async def identifier_create(
        entity_id: str,
        request: Request,
        entity_identifier_type_id: str = Form(...),
        value: str = Form(...),
        user: AdminUser = Depends(get_admin_user),
        db=Depends(get_db),
    ):
        """Create a new identifier."""
        await _get_entity_or_404(entity_id, db)
        iid = generate_id()
        await db.execute(
            "INSERT INTO identifiers (id, entity_id, entity_identifier_type_id, value)"
            " VALUES ($1, $2, $3, $4)",
            iid,
            entity_id,
            entity_identifier_type_id,
            value.strip(),
        )
        row = await _get_identifier_or_404(iid, entity_id, db)
        if not is_htmx(request):
            return RedirectResponse(detail_url(entity_id), status_code=303)
        return templates.TemplateResponse(
            request,
            tmpl_read_row,
            _ctx(entity_id, ident=row),
            headers=flash_trigger("success", f"<strong>{escape(value.strip())}</strong> added."),
        )

    @router.get("/{ident_id}/read-row/")
    async def identifier_read_row(
        entity_id: str,
        ident_id: str,
        request: Request,
        user: AdminUser = Depends(get_admin_user),
        db=Depends(get_db),
    ):
        """Return read-only identifier row (used by Cancel on edit form)."""
        row = await _get_identifier_or_404(ident_id, entity_id, db)
        return templates.TemplateResponse(request, tmpl_read_row, _ctx(entity_id, ident=row))

    @router.get("/{ident_id}/edit-row/")
    async def identifier_edit_row_get(
        entity_id: str,
        ident_id: str,
        request: Request,
        user: AdminUser = Depends(get_admin_user),
        db=Depends(get_db),
    ):
        """Return identifier edit form row."""
        row = await _get_identifier_or_404(ident_id, entity_id, db)
        ident_types = await db.fetch(
            "SELECT * FROM entity_identifier_types"
            " WHERE entity_type=$1 AND NOT is_internal ORDER BY display_name",
            entity_type,
        )
        return templates.TemplateResponse(
            request,
            tmpl_form_row,
            _ctx(entity_id, ident=row, ident_types=ident_types),
        )

    @router.post("/{ident_id}/edit-row/")
    async def identifier_edit_row_post(
        entity_id: str,
        ident_id: str,
        request: Request,
        entity_identifier_type_id: str = Form(...),
        value: str = Form(...),
        user: AdminUser = Depends(get_admin_user),
        db=Depends(get_db),
    ):
        """Update an identifier."""
        await _get_identifier_or_404(ident_id, entity_id, db)
        await db.execute(
            "UPDATE identifiers SET entity_identifier_type_id=$1, value=$2 WHERE id=$3",
            entity_identifier_type_id,
            value.strip(),
            ident_id,
        )
        row = await _get_identifier_or_404(ident_id, entity_id, db)
        if not is_htmx(request):
            return RedirectResponse(detail_url(entity_id), status_code=303)
        return templates.TemplateResponse(
            request,
            tmpl_read_row,
            _ctx(entity_id, ident=row),
            headers=flash_trigger("success", f"<strong>{escape(value.strip())}</strong> saved."),
        )

    @router.delete("/{ident_id}/")
    async def identifier_delete(
        entity_id: str,
        ident_id: str,
        request: Request,
        user: AdminUser = Depends(get_admin_user),
        db=Depends(get_db),
    ):
        """Delete an identifier."""
        existing = await db.fetchrow(
            """SELECT i.id FROM identifiers i
               JOIN entity_identifier_types eit ON eit.id = i.entity_identifier_type_id
               WHERE i.id=$1 AND i.entity_id=$2 AND eit.entity_type=$3""",
            ident_id,
            entity_id,
            entity_type,
        )
        if not existing:
            raise HTTPException(status_code=404)
        await db.execute("DELETE FROM identifiers WHERE id=$1", ident_id)
        return HTMLResponse(
            content="",
            status_code=200,
            headers=flash_trigger("info", "Identifier removed."),
        )

    return router
