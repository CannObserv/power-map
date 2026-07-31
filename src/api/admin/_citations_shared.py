"""Shared factory for entity citation CRUD routers (#319).

Citations are uniform across all citable entity types, so — unlike the per-entity
contact/link templates — one shared template set (``admin/citations/partials/*``)
serves every entity, parameterized by a ``cit_base`` URL and ``detail_url``.

Admin writes go through **direct SQL** (ungated by the ``source_key_id`` provenance
gate — curators may edit producer-sourced citations) and self-emit the parent
``entity_changes`` 'updated' via ``trg_touch_entity_on_citation_change`` (#327
model — no app-layer emit). ``field_name`` is validated against ``CITABLE_FIELDS``;
admin delete is a hard delete (curator removing a mistaken row).
"""

from collections.abc import Awaitable, Callable
from typing import Any

import asyncpg
from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from markupsafe import escape

from src.api.admin.deps import (
    AdminUser,
    flash_trigger,
    get_admin_user,
    get_db,
    is_htmx,
    with_flash,
)
from src.core.citations import CITABLE_ENTITY_TYPES, CITABLE_FIELDS
from src.core.db import generate_id

templates = Jinja2Templates(directory="src/templates")

_FORM_ROW = "admin/citations/partials/_citation_form_row.html"
_READ_ROW = "admin/citations/partials/_citation_row.html"
_PANEL = "admin/citations/partials/_citations_panel.html"


def _clean(v: str | None) -> str | None:
    return v.strip() if v and v.strip() else None


def citation_count_lateral(entity_type: str, id_expr: str, *, alias: str = "cc") -> str:
    """SQL fragment adding ``citation_count`` (active rows only) to a row query.

    Embedding the count in the row-fetch SQL — rather than a side dict passed to
    the template — keeps the #341 indicator alive across every render path of a
    row partial (list tbody, single-row HTMX re-renders). One LATERAL probe per
    row on ``idx_citations_entity``; still a single query per list.

    ``id_expr`` is interpolated verbatim: it must be a code-supplied column
    expression (e.g. ``"ra.id"``), never user input.
    """
    if entity_type not in CITABLE_ENTITY_TYPES:
        raise ValueError(f"not a citable entity type: {entity_type!r}")
    return (
        f" LEFT JOIN LATERAL ("
        f"SELECT count(*) AS citation_count FROM citations {alias}"
        f" WHERE {alias}.entity_type = '{entity_type}' AND {alias}.entity_id = {id_expr}"
        f" AND {alias}.archived_at IS NULL) {alias}_j ON TRUE "
    )


def make_citations_router(
    *,
    entity_type: str,
    prefix: str,
    tags: list[str],
    entity_table: str,
    entity_not_found_msg: str,
    detail_url: Callable[[str], str],
    redirect_resolver: Callable[[str, Any], Awaitable[str]] | None = None,
    subject_resolver: Callable[[str, Any], Awaitable[str | None]] | None = None,
    inline_panel: bool = False,
    locked_field: str | None = None,
    subrow_colspan: int = 1,
) -> APIRouter:
    """Return a configured citations APIRouter for the given entity type.

    ``prefix`` must contain ``{entity_id}`` (e.g. ``/orgs/{entity_id}/citations``).

    ``redirect_resolver`` (optional) resolves the non-htmx fallback URL with DB
    access — used by sub-entity routers (person_name → owning person, entity_event
    → owning entity) whose parent isn't derivable from the id alone. When absent,
    the sync ``detail_url`` is used.

    ``subject_resolver`` (optional) resolves a human label for the panel heading
    ("Citations for …") so a sub-entity drawer names what it cites and can never be
    confused with the owning entity's own panel.

    ``inline_panel=True`` registers a ``GET /`` route rendering the whole citations
    panel for one entity — the lazy-load target for a sub-entity's expandable row;
    it renders as a full-width sub-row so it nests under the clicked parent row.

    ``locked_field`` (optional) pins every citation on this entity to a single
    field (e.g. ``"name"`` for person_name, whose only citable field is the name
    itself): the form drops the field picker and the server ignores any posted
    ``field_name``. Prevents a name/event drawer from silently minting a
    whole-record citation that reads as redundant with the owner's panel (#319).

    ``subrow_colspan`` must equal the parent table's exact column count when
    ``inline_panel`` is used (person_name → 4, entity_event → 6). It is NOT a
    "span everything" sentinel: an over-large value implies phantom columns that
    collapse the real ones under ``table-layout:fixed`` (#319 scrunch regression).
    """
    if inline_panel and subrow_colspan < 2:
        raise ValueError(
            "inline_panel routers must set subrow_colspan to the parent table's"
            f" column count (>=2); got {subrow_colspan} for {entity_type}."
        )

    router = APIRouter(prefix=prefix, tags=tags)
    citable_fields = sorted(CITABLE_FIELDS.get(entity_type, frozenset()))

    async def _active_count(entity_id: str, db) -> int:
        """Fresh active-citation count for the parent row's Cite button (#341 CR1)."""
        return await db.fetchval(
            "SELECT count(*) FROM citations"
            " WHERE entity_type=$1 AND entity_id=$2 AND archived_at IS NULL",
            entity_type,
            entity_id,
        )

    async def _get_entity_or_404(entity_id: str, db):
        row = await db.fetchrow(f"SELECT id FROM {entity_table} WHERE id=$1", entity_id)
        if not row:
            raise HTTPException(status_code=404, detail=entity_not_found_msg)

    async def _dest(entity_id: str, db) -> str:
        if redirect_resolver is not None:
            return await redirect_resolver(entity_id, db)
        return detail_url(entity_id)

    def _ctx(entity_id: str, **extra) -> dict:
        return {
            "entity_id": entity_id,
            "cit_base": prefix.replace("{entity_id}", entity_id),
            "citable_fields": citable_fields,
            "locked_field": locked_field,
            "subrow_colspan": subrow_colspan,
            **extra,
        }

    if inline_panel:

        @router.get("/")
        async def citation_panel(
            entity_id: str,
            request: Request,
            user: AdminUser = Depends(get_admin_user),
            db=Depends(get_db),
        ):
            """Render the whole citations panel for one entity (inline lazy-load)."""
            await _get_entity_or_404(entity_id, db)
            citations = await db.fetch(
                "SELECT * FROM citations WHERE entity_type=$1 AND entity_id=$2"
                " AND archived_at IS NULL ORDER BY created_at DESC, id DESC",
                entity_type,
                entity_id,
            )
            subject_label = await subject_resolver(entity_id, db) if subject_resolver else None
            # dismissible=True → Close control; as_subrow=True → the panel is wrapped
            # as a full-width table row so it nests directly under the clicked
            # person_name / entity_event row (tethered, not a bottom drawer, #319).
            return templates.TemplateResponse(
                request,
                _PANEL,
                _ctx(
                    entity_id,
                    citations=citations,
                    dismissible=True,
                    as_subrow=True,
                    subject_label=subject_label,
                ),
            )

    def _validate(field_name: str | None, url: str | None, title: str | None) -> str | None:
        if field_name is not None and field_name not in CITABLE_FIELDS.get(
            entity_type, frozenset()
        ):
            return f"'{field_name}' is not a citable field for {entity_type}."
        if not url and not title:
            return "A citation needs at least a URL or a title."
        return None

    @router.get("/new-row/")
    async def citation_new_row(
        entity_id: str,
        request: Request,
        user: AdminUser = Depends(get_admin_user),
        db=Depends(get_db),
    ):
        await _get_entity_or_404(entity_id, db)
        return templates.TemplateResponse(request, _FORM_ROW, _ctx(entity_id, c=None))

    @router.post("/")
    async def citation_create(
        entity_id: str,
        request: Request,
        field_name: str = Form(""),
        url: str = Form(""),
        title: str = Form(""),
        excerpt: str = Form(""),
        user: AdminUser = Depends(get_admin_user),
        db=Depends(get_db),
    ):
        await _get_entity_or_404(entity_id, db)
        f_field, f_url, f_title, f_excerpt = (
            locked_field or _clean(field_name),
            _clean(url),
            _clean(title),
            _clean(excerpt),
        )
        error = _validate(f_field, f_url, f_title)
        conflict = False
        if error is None:
            cid = generate_id()
            try:
                # Savepoint so a unique-violation rolls back only this INSERT,
                # leaving the request's transaction usable (the rollback test
                # client wraps everything in one outer transaction).
                async with db.transaction():
                    await db.execute(
                        "INSERT INTO citations"
                        " (id, entity_type, entity_id, field_name, url, title, excerpt)"
                        " VALUES ($1,$2,$3,$4,$5,$6,$7)",
                        cid,
                        entity_type,
                        entity_id,
                        f_field,
                        f_url,
                        f_title,
                        f_excerpt,
                    )
            except asyncpg.UniqueViolationError:
                error = "A citation with this field and URL already exists."
                conflict = True
        if error:
            if not is_htmx(request):
                # A uniqueness conflict flashes `exists`; a validation failure
                # flashes `invalid` — both funnel into one `error` here (#351 CR).
                return RedirectResponse(
                    with_flash(await _dest(entity_id, db), "exists" if conflict else "invalid"),
                    status_code=303,
                )
            return templates.TemplateResponse(
                request,
                _FORM_ROW,
                _ctx(
                    entity_id,
                    c=None,
                    error=error,
                    form={
                        "field_name": f_field,
                        "url": f_url,
                        "title": f_title,
                        "excerpt": f_excerpt,
                    },
                ),
            )
        row = await db.fetchrow("SELECT * FROM citations WHERE id=$1", cid)
        if not is_htmx(request):
            return RedirectResponse(
                with_flash(await _dest(entity_id, db), "saved"), status_code=303
            )
        # remove_empty → the read row carries an OOB delete for the panel's
        # "No citations yet." row so the first citation doesn't render above it.
        # cite_count_oob → OOB refresh of the parent row's Cite-button count.
        return templates.TemplateResponse(
            request,
            _READ_ROW,
            _ctx(
                entity_id,
                c=row,
                remove_empty=True,
                cite_count_oob=await _active_count(entity_id, db),
            ),
            headers=flash_trigger(
                "success", f"Citation <strong>{escape(f_url or f_title)}</strong> added."
            ),
        )

    @router.get("/{citation_id}/read-row/")
    async def citation_read_row(
        entity_id: str,
        citation_id: str,
        request: Request,
        user: AdminUser = Depends(get_admin_user),
        db=Depends(get_db),
    ):
        row = await db.fetchrow(
            "SELECT * FROM citations WHERE id=$1 AND entity_type=$2 AND entity_id=$3",
            citation_id,
            entity_type,
            entity_id,
        )
        if not row:
            raise HTTPException(status_code=404)
        return templates.TemplateResponse(request, _READ_ROW, _ctx(entity_id, c=row))

    @router.get("/{citation_id}/edit-row/")
    async def citation_edit_row_get(
        entity_id: str,
        citation_id: str,
        request: Request,
        user: AdminUser = Depends(get_admin_user),
        db=Depends(get_db),
    ):
        row = await db.fetchrow(
            "SELECT * FROM citations WHERE id=$1 AND entity_type=$2 AND entity_id=$3",
            citation_id,
            entity_type,
            entity_id,
        )
        if not row:
            raise HTTPException(status_code=404)
        return templates.TemplateResponse(request, _FORM_ROW, _ctx(entity_id, c=row))

    @router.post("/{citation_id}/edit-row/")
    async def citation_edit_row_post(
        entity_id: str,
        citation_id: str,
        request: Request,
        field_name: str = Form(""),
        url: str = Form(""),
        title: str = Form(""),
        excerpt: str = Form(""),
        user: AdminUser = Depends(get_admin_user),
        db=Depends(get_db),
    ):
        existing = await db.fetchrow(
            "SELECT * FROM citations WHERE id=$1 AND entity_type=$2 AND entity_id=$3",
            citation_id,
            entity_type,
            entity_id,
        )
        if not existing:
            raise HTTPException(status_code=404)
        f_field, f_url, f_title, f_excerpt = (
            locked_field or _clean(field_name),
            _clean(url),
            _clean(title),
            _clean(excerpt),
        )
        error = _validate(f_field, f_url, f_title)
        conflict = False
        if error is None:
            try:
                async with db.transaction():
                    await db.execute(
                        "UPDATE citations SET field_name=$1, url=$2, title=$3, excerpt=$4"
                        " WHERE id=$5",
                        f_field,
                        f_url,
                        f_title,
                        f_excerpt,
                        citation_id,
                    )
            except asyncpg.UniqueViolationError:
                error = "A citation with this field and URL already exists."
                conflict = True
        if error:
            if not is_htmx(request):
                # A uniqueness conflict flashes `exists`; a validation failure
                # flashes `invalid` — both funnel into one `error` here (#351 CR).
                return RedirectResponse(
                    with_flash(await _dest(entity_id, db), "exists" if conflict else "invalid"),
                    status_code=303,
                )
            return templates.TemplateResponse(
                request, _FORM_ROW, _ctx(entity_id, c=existing, error=error)
            )
        row = await db.fetchrow("SELECT * FROM citations WHERE id=$1", citation_id)
        if not is_htmx(request):
            return RedirectResponse(
                with_flash(await _dest(entity_id, db), "saved"), status_code=303
            )
        return templates.TemplateResponse(
            request,
            _READ_ROW,
            _ctx(entity_id, c=row),
            headers=flash_trigger("success", "Citation saved."),
        )

    @router.delete("/{citation_id}/")
    async def citation_delete(
        entity_id: str,
        citation_id: str,
        request: Request,
        user: AdminUser = Depends(get_admin_user),
        db=Depends(get_db),
    ):
        existing = await db.fetchrow(
            "SELECT id FROM citations WHERE id=$1 AND entity_type=$2 AND entity_id=$3",
            citation_id,
            entity_type,
            entity_id,
        )
        if not existing:
            raise HTTPException(status_code=404)
        await db.execute("DELETE FROM citations WHERE id=$1", citation_id)
        if not is_htmx(request):
            return RedirectResponse(
                with_flash(await _dest(entity_id, db), "removed"), status_code=303
            )
        # Body is only the OOB count fragment: the deleted row's outerHTML swap
        # resolves to nothing, while the parent row's Cite button refreshes.
        return templates.TemplateResponse(
            request,
            "admin/citations/partials/_cite_count_oob.html",
            _ctx(entity_id, cite_count_oob=await _active_count(entity_id, db)),
            headers=flash_trigger("success", "Citation removed."),
        )

    return router
