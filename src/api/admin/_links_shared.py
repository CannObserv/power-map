"""Shared factory for entity links CRUD routers (orgs and people)."""

from collections.abc import Callable

from asyncpg.exceptions import UniqueViolationError
from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from markupsafe import escape

from src.api.admin.deps import AdminUser, flash_trigger, get_admin_user, get_db, is_htmx
from src.core.db import generate_id

templates = Jinja2Templates(directory="src/templates")


def make_links_router(
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
    """Return a configured links APIRouter for the given entity type.

    Parameters
    ----------
    entity_type:
        DB value stored in links.entity_type (e.g. ``'organization'``).
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
        Template path for the link form row partial.
    tmpl_read_row:
        Template path for the link read row partial.
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

    async def _get_link_or_404(link_id: str, entity_id: str, db):
        row = await db.fetchrow(
            """SELECT l.*, lt.display_name AS link_type_name, lt.is_social
               FROM links l JOIN link_types lt ON lt.id = l.link_type_id
               WHERE l.id=$1 AND l.entity_type=$2 AND l.entity_id=$3""",
            link_id,
            entity_type,
            entity_id,
        )
        if not row:
            raise HTTPException(status_code=404)
        return row

    def _ctx(entity_id: str, **extra) -> dict:
        """Build template context with the correct entity-id key."""
        return {entity_id_key: entity_id, **extra}

    # ---- routes -----------------------------------------------------------------

    @router.get("/new-row/")
    async def link_new_row(
        entity_id: str,
        request: Request,
        user: AdminUser = Depends(get_admin_user),
        db=Depends(get_db),
    ):
        """Return empty link form row."""
        await _get_entity_or_404(entity_id, db)
        link_types = await db.fetch(
            "SELECT * FROM link_types ORDER BY is_social DESC, display_name"
        )
        return templates.TemplateResponse(
            request,
            tmpl_form_row,
            _ctx(entity_id, l=None, link_types=link_types),
        )

    @router.post("/")
    async def link_create(
        entity_id: str,
        request: Request,
        url: str = Form(...),
        link_type_id: str = Form(...),
        is_active: str = Form(""),
        user: AdminUser = Depends(get_admin_user),
        db=Depends(get_db),
    ):
        """Create a new link."""
        await _get_entity_or_404(entity_id, db)
        lid = generate_id()
        clean_url = url.strip()
        try:
            await db.execute(
                "INSERT INTO links"
                " (id, entity_type, entity_id, url, link_type_id, is_active)"
                " VALUES ($1, $2, $3, $4, $5, $6)",
                lid,
                entity_type,
                entity_id,
                clean_url,
                link_type_id,
                is_active == "true",
            )
        except UniqueViolationError:
            if not is_htmx(request):
                return RedirectResponse(detail_url(entity_id), status_code=303)
            return HTMLResponse(
                content="",
                status_code=409,
                headers=flash_trigger(
                    "warning",
                    f"Link <strong>{escape(clean_url)}</strong> already exists for this entity.",
                ),
            )
        row = await _get_link_or_404(lid, entity_id, db)
        if not is_htmx(request):
            return RedirectResponse(detail_url(entity_id), status_code=303)
        return templates.TemplateResponse(
            request,
            tmpl_read_row,
            _ctx(entity_id, l=row),
            headers=flash_trigger(
                "success", f"Link <strong>{escape(url.strip())}</strong> added."
            ),
        )

    @router.get("/{link_id}/read-row/")
    async def link_read_row(
        entity_id: str,
        link_id: str,
        request: Request,
        user: AdminUser = Depends(get_admin_user),
        db=Depends(get_db),
    ):
        """Return read-only link row (used by Cancel on edit form)."""
        row = await _get_link_or_404(link_id, entity_id, db)
        return templates.TemplateResponse(
            request, tmpl_read_row, _ctx(entity_id, l=row)
        )

    @router.get("/{link_id}/edit-row/")
    async def link_edit_row_get(
        entity_id: str,
        link_id: str,
        request: Request,
        user: AdminUser = Depends(get_admin_user),
        db=Depends(get_db),
    ):
        """Return link edit form row."""
        row = await _get_link_or_404(link_id, entity_id, db)
        link_types = await db.fetch(
            "SELECT * FROM link_types ORDER BY is_social DESC, display_name"
        )
        return templates.TemplateResponse(
            request,
            tmpl_form_row,
            _ctx(entity_id, l=row, link_types=link_types),
        )

    @router.post("/{link_id}/edit-row/")
    async def link_edit_row_post(
        entity_id: str,
        link_id: str,
        request: Request,
        url: str = Form(...),
        link_type_id: str = Form(...),
        is_active: str = Form(""),
        user: AdminUser = Depends(get_admin_user),
        db=Depends(get_db),
    ):
        """Update a link."""
        await _get_link_or_404(link_id, entity_id, db)
        clean_url = url.strip()
        try:
            await db.execute(
                "UPDATE links SET url=$1, link_type_id=$2, is_active=$3 WHERE id=$4",
                clean_url,
                link_type_id,
                is_active == "true",
                link_id,
            )
        except UniqueViolationError:
            if not is_htmx(request):
                return RedirectResponse(detail_url(entity_id), status_code=303)
            return HTMLResponse(
                content="",
                status_code=409,
                headers=flash_trigger(
                    "warning",
                    f"Link <strong>{escape(clean_url)}</strong> already exists for this entity.",
                ),
            )
        row = await _get_link_or_404(link_id, entity_id, db)
        if not is_htmx(request):
            return RedirectResponse(detail_url(entity_id), status_code=303)
        return templates.TemplateResponse(
            request,
            tmpl_read_row,
            _ctx(entity_id, l=row),
            headers=flash_trigger(
                "success", f"Link <strong>{escape(url.strip())}</strong> saved."
            ),
        )

    @router.delete("/{link_id}/")
    async def link_delete(
        entity_id: str,
        link_id: str,
        request: Request,
        user: AdminUser = Depends(get_admin_user),
        db=Depends(get_db),
    ):
        """Delete a link."""
        existing = await db.fetchrow(
            "SELECT id FROM links WHERE id=$1 AND entity_type=$2 AND entity_id=$3",
            link_id,
            entity_type,
            entity_id,
        )
        if not existing:
            raise HTTPException(status_code=404)
        await db.execute("DELETE FROM links WHERE id=$1", link_id)
        return HTMLResponse(
            content="", status_code=200, headers=flash_trigger("info", "Link removed.")
        )

    return router
