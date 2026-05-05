"""Shared factory for entity names CRUD routers (orgs and people)."""

from collections.abc import Awaitable, Callable

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from markupsafe import escape

from src.api.admin.deps import AdminUser, flash_trigger, get_admin_user, get_db, is_htmx
from src.core.db import generate_id

templates = Jinja2Templates(directory="src/templates")


def make_names_router(
    *,
    entity_id_key: str,
    prefix: str,
    tags: list[str],
    entity_table: str,
    entity_not_found_msg: str,
    names_table: str,
    entity_fk: str,
    tmpl_form_row: str,
    tmpl_read_row: str,
    tmpl_rows: str,
    detail_url: Callable[[str], str],
    maybe_promote_sole_name: Callable[[str, object], Awaitable[None]],
    last_identity_blocked: Callable[[str, object], Awaitable[bool]],
    last_identity_error_msg: str,
    last_identity_409_msg: str,
    header_extra: Callable[[str, object], Awaitable[dict]],
    supports_metadata: bool = False,
) -> APIRouter:
    """Return a configured names APIRouter for the given entity type.

    Parameters
    ----------
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
    names_table:
        Table storing names (``'organization_names'`` or ``'person_names'``).
    entity_fk:
        FK column name in the names table (``'organization_id'`` or ``'person_id'``).
    tmpl_form_row:
        Template path for the name form row partial.
    tmpl_read_row:
        Template path for the name read row partial.
    tmpl_rows:
        Template path for the full rows partial (tbody replacement).
    detail_url:
        Callable accepting the entity id and returning the detail redirect URL.
    maybe_promote_sole_name:
        Async callable ``(entity_id, db) -> None`` that promotes the sole remaining
        non-canonical name. Must be called inside a transaction on delete routes.
    last_identity_blocked:
        Async callable ``(entity_id, db) -> bool`` returning True when deleting would
        violate the last-identity invariant.
    last_identity_error_msg:
        Flash error body shown when ``last_identity_blocked`` returns True (HTMX).
    last_identity_409_msg:
        HTTPException detail string for non-HTMX last-identity block.
    header_extra:
        Async callable ``(entity_id, db) -> dict`` returning the extra HX-Trigger
        payload for header-sync events (e.g. updateOrgHeader / updatePersonHeader).
    supports_metadata:
        When True, accept and persist a ``visibility`` Form field on create/edit
        and pass it to row templates. Used by person_names (Phase 2a, #123);
        org_names ignores it (no visibility column on organization_names).
    """
    _VALID_VISIBILITIES = ("public", "legal_only", "hidden")

    def _normalise_visibility(value: str | None) -> str | None:
        """Validate the form-supplied visibility; return None when unsupported.

        Returns None when ``supports_metadata`` is False, the field is absent,
        or the value isn't in the allowed set — callers then fall back to the
        DB default ('public'). The DB ``person_names_visibility_check`` CHECK
        and the deadname trigger remain the source of truth.
        """
        if not supports_metadata or value is None:
            return None
        return value if value in _VALID_VISIBILITIES else None
    router = APIRouter(prefix=prefix, tags=tags)

    # ---- helpers ----------------------------------------------------------------

    async def _get_entity_or_404(entity_id: str, db):
        row = await db.fetchrow(f"SELECT id FROM {entity_table} WHERE id=$1", entity_id)
        if not row:
            raise HTTPException(status_code=404, detail=entity_not_found_msg)
        return row

    def _ctx(entity_id: str, **extra) -> dict:
        """Build template context with the correct entity-id key."""
        return {entity_id_key: entity_id, **extra}

    # ---- routes -----------------------------------------------------------------

    @router.get("/new-row/")
    async def name_new_row(
        entity_id: str,
        request: Request,
        user: AdminUser = Depends(get_admin_user),
        db=Depends(get_db),
    ):
        """Return empty name form row."""
        await _get_entity_or_404(entity_id, db)
        return templates.TemplateResponse(
            request,
            tmpl_form_row,
            _ctx(entity_id, n=None),
        )

    @router.post("/")
    async def name_create(
        entity_id: str,
        request: Request,
        name: str = Form(...),
        name_type: str = Form("legal"),
        is_canonical: str = Form(""),
        visibility: str | None = Form(None),
        user: AdminUser = Depends(get_admin_user),
        db=Depends(get_db),
    ):
        """Create a new name."""
        await _get_entity_or_404(entity_id, db)
        nid = generate_id()
        vis = _normalise_visibility(visibility)
        async with db.transaction():
            if is_canonical == "true":
                await db.execute(
                    f"UPDATE {names_table} SET is_canonical=FALSE"
                    f" WHERE {entity_fk}=$1 AND is_canonical=TRUE",
                    entity_id,
                )
            if vis is None:
                await db.execute(
                    f"INSERT INTO {names_table} (id, {entity_fk}, name, name_type, is_canonical)"
                    " VALUES ($1, $2, $3, $4, $5)",
                    nid,
                    entity_id,
                    name.strip(),
                    name_type,
                    is_canonical == "true",
                )
            else:
                await db.execute(
                    f"INSERT INTO {names_table}"
                    f" (id, {entity_fk}, name, name_type, is_canonical, visibility)"
                    " VALUES ($1, $2, $3, $4, $5, $6)",
                    nid,
                    entity_id,
                    name.strip(),
                    name_type,
                    is_canonical == "true",
                    vis,
                )
        if not is_htmx(request):
            return RedirectResponse(detail_url(entity_id), status_code=303)
        names = await db.fetch(
            f"SELECT * FROM {names_table} WHERE {entity_fk}=$1"
            " ORDER BY is_canonical DESC, name_type, name",
            entity_id,
        )
        return templates.TemplateResponse(
            request,
            tmpl_rows,
            _ctx(entity_id, names=names),
            headers=flash_trigger(
                "success",
                f"Name <strong>{escape(name.strip())}</strong> added.",
                extra=await header_extra(entity_id, db),
            ),
        )

    @router.get("/{name_id}/read-row/")
    async def name_read_row(
        entity_id: str,
        name_id: str,
        request: Request,
        user: AdminUser = Depends(get_admin_user),
        db=Depends(get_db),
    ):
        """Return read-only name row (used by Cancel on edit form)."""
        name_row = await db.fetchrow(
            f"SELECT * FROM {names_table} WHERE id=$1 AND {entity_fk}=$2",
            name_id,
            entity_id,
        )
        if not name_row:
            raise HTTPException(status_code=404)
        return templates.TemplateResponse(
            request, tmpl_read_row, _ctx(entity_id, n=name_row)
        )

    @router.get("/{name_id}/edit-row/")
    async def name_edit_row_get(
        entity_id: str,
        name_id: str,
        request: Request,
        user: AdminUser = Depends(get_admin_user),
        db=Depends(get_db),
    ):
        """Return name edit form row."""
        name_row = await db.fetchrow(
            f"SELECT * FROM {names_table} WHERE id=$1 AND {entity_fk}=$2",
            name_id,
            entity_id,
        )
        if not name_row:
            raise HTTPException(status_code=404)
        return templates.TemplateResponse(
            request,
            tmpl_form_row,
            _ctx(entity_id, n=name_row),
        )

    @router.post("/{name_id}/edit-row/")
    async def name_edit_row_post(
        entity_id: str,
        name_id: str,
        request: Request,
        name: str = Form(...),
        name_type: str = Form("legal"),
        is_canonical: str = Form(""),
        visibility: str | None = Form(None),
        user: AdminUser = Depends(get_admin_user),
        db=Depends(get_db),
    ):
        """Update a name."""
        existing = await db.fetchrow(
            f"SELECT * FROM {names_table} WHERE id=$1 AND {entity_fk}=$2",
            name_id,
            entity_id,
        )
        if not existing:
            raise HTTPException(status_code=404)
        if is_canonical != "true" and existing["is_canonical"]:
            # Guard runs outside the transaction intentionally: a concurrent promotion
            # (another request canonicalizing a different name) would make this check
            # false — i.e., the save would be allowed — which is the safe direction.
            other_canonical = await db.fetchval(
                f"SELECT id FROM {names_table}"
                f" WHERE {entity_fk}=$1 AND is_canonical=TRUE AND id != $2",
                entity_id,
                name_id,
            )
            if not other_canonical:
                if not is_htmx(request):
                    return RedirectResponse(detail_url(entity_id), status_code=303)
                return HTMLResponse(
                    content="",
                    status_code=200,
                    headers=flash_trigger(
                        "error",
                        "Cannot remove canonical. Promote another name first.",
                    ),
                )
        vis = _normalise_visibility(visibility)
        async with db.transaction():
            if is_canonical == "true":
                await db.execute(
                    f"UPDATE {names_table} SET is_canonical=FALSE"
                    f" WHERE {entity_fk}=$1 AND is_canonical=TRUE AND id != $2",
                    entity_id,
                    name_id,
                )
            if vis is None:
                await db.execute(
                    f"UPDATE {names_table} SET name=$1, name_type=$2, is_canonical=$3"
                    " WHERE id=$4",
                    name.strip(),
                    name_type,
                    is_canonical == "true",
                    name_id,
                )
            else:
                await db.execute(
                    f"UPDATE {names_table}"
                    " SET name=$1, name_type=$2, is_canonical=$3, visibility=$4"
                    " WHERE id=$5",
                    name.strip(),
                    name_type,
                    is_canonical == "true",
                    vis,
                    name_id,
                )
        if not is_htmx(request):
            return RedirectResponse(detail_url(entity_id), status_code=303)
        names = await db.fetch(
            f"SELECT * FROM {names_table} WHERE {entity_fk}=$1"
            " ORDER BY is_canonical DESC, name_type, name",
            entity_id,
        )
        return templates.TemplateResponse(
            request,
            tmpl_rows,
            _ctx(entity_id, names=names),
            headers=flash_trigger(
                "success",
                f"Name <strong>{escape(name.strip())}</strong> saved.",
                extra=await header_extra(entity_id, db),
            ),
        )

    @router.delete("/{name_id}/")
    async def name_delete(
        entity_id: str,
        name_id: str,
        request: Request,
        user: AdminUser = Depends(get_admin_user),
        db=Depends(get_db),
    ):
        """Delete a name."""
        existing = await db.fetchrow(
            f"SELECT id FROM {names_table} WHERE id=$1 AND {entity_fk}=$2",
            name_id,
            entity_id,
        )
        if not existing:
            raise HTTPException(status_code=404)
        async with db.transaction():
            if await last_identity_blocked(entity_id, db):
                if not is_htmx(request):
                    raise HTTPException(
                        status_code=409,
                        detail=last_identity_409_msg,
                    )
                return HTMLResponse(
                    content="",
                    status_code=200,
                    headers=flash_trigger("error", last_identity_error_msg),
                )
            await db.execute(f"DELETE FROM {names_table} WHERE id=$1", name_id)
            await maybe_promote_sole_name(entity_id, db)
        if not is_htmx(request):
            return RedirectResponse(detail_url(entity_id), status_code=303)
        names = await db.fetch(
            f"SELECT * FROM {names_table} WHERE {entity_fk}=$1"
            " ORDER BY is_canonical DESC, name_type, name",
            entity_id,
        )
        return templates.TemplateResponse(
            request,
            tmpl_rows,
            _ctx(entity_id, names=names),
            headers=flash_trigger(
                "info", "Name removed.", extra=await header_extra(entity_id, db)
            ),
        )

    return router
