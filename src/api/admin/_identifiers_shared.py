"""Shared factory for entity identifiers CRUD routers (orgs and people)."""

from collections.abc import Callable

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
from src.api.admin.overlay_slots import IDENTIFIER_LOCKED_MSG
from src.core.dataset_ownership import (
    anchored_people,
    dataset_owned_identifier,
    owned_identifier,
)
from src.core.db import generate_id, identifier_entity_vanished
from src.core.identifier_taken import Holder, identifier_holder, is_identifier_taken

templates = Jinja2Templates(directory="src/templates")


def taken_message(entity_type: str, entity_id: str, value: str, holder: Holder | None) -> str:
    """The warning flash for a value ``uq_identifiers_cannabis_observer`` refused (#812)."""
    shown = f"<strong>{escape(value)}</strong>"
    if holder is None:  # the holder let go between the refusal and this read
        return f"{shown} was in use; nothing changed. Try again."
    if holder.entity_id == entity_id:
        return f"{shown} is already on this record; nothing changed."
    label = entity_type.replace("_", " ")
    if holder.archived:
        # #481: archiving keeps identifiers, so the fix is the holder, not the value.
        return (
            f"{shown} is held by an archived {label} ({escape(holder.entity_id)}). "
            "Unarchive or merge it; nothing changed."
        )
    return f"{shown} is held by another {label} ({escape(holder.entity_id)}); nothing changed."


async def fetch_identifiers(db, entity_type: str, entity_id: str) -> list[dict]:
    """The identifiers table's rows, in display order (the detail page and every write, #683).

    Each row carries ``dataset_owned`` (#710): a key a dataset asserts renders read-only.
    """
    rows = await db.fetch(
        """SELECT i.*, eit.slug AS type_slug, eit.display_name AS type_name,
                  eit.full_name AS type_full_name
           FROM identifiers i
           JOIN entity_identifier_types eit ON eit.id = i.entity_identifier_type_id
           WHERE i.entity_id = $1 AND eit.entity_type = $2
           ORDER BY eit.display_name, i.value, i.id""",
        entity_id,
        entity_type,
    )
    anchored = await anchored_people(db, [entity_id]) if entity_type == "person" else set()
    return [
        {**r, "dataset_owned": owned_identifier(entity_type, entity_id, r["type_slug"], anchored)}
        for r in rows
    ]


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
    tmpl_rows: str,
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
    tmpl_rows:
        Template path for the ``<tbody>`` rows partial (rows + empty-state row).
        Every create/edit/delete answers with it (#683).
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

    async def _get_identifier_or_404(ident_id: str, entity_id: str, db) -> dict:
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
        owned = await dataset_owned_identifier(
            db, entity_type, entity_id, row["entity_identifier_type_id"]
        )
        return {**row, "dataset_owned": owned}

    async def _write_or_404(db, sql: str, *args) -> bool:
        """Run an identifier insert or update; False when its value is taken (#812).

        404 when the entity vanished since its check (#622). Its own
        (sub)transaction (#424), so a refused write leaves the connection usable —
        under a request transaction, and for the holder read that follows.
        """
        try:
            async with db.transaction():
                await db.execute(sql, *args)
        except asyncpg.UniqueViolationError as exc:
            if not is_identifier_taken(exc):
                raise
            return False
        except asyncpg.ForeignKeyViolationError as exc:
            if not identifier_entity_vanished(exc):
                raise
            raise HTTPException(status_code=404, detail=entity_not_found_msg) from exc
        return True

    async def _require_attachable_type(type_id: str, db) -> None:
        """400 unless the type is one the picker offers: this entity type, external.

        The form is the only filter, and get_db opens no transaction, so a stale or
        crafted post would write before the read-back 404 (#617 CR).
        """
        ok = await db.fetchval(
            "SELECT 1 FROM entity_identifier_types"
            " WHERE id=$1 AND entity_type=$2 AND NOT is_internal",
            type_id,
            entity_type,
        )
        if not ok:
            raise HTTPException(status_code=400, detail="Unknown identifier type")

    async def _picker_types(db, entity_id: str) -> list:
        """The types the add and edit pickers offer: attachable, and (#710) none a
        dataset owns on this entity — every door would refuse it."""
        types = await db.fetch(
            "SELECT * FROM entity_identifier_types"
            " WHERE entity_type=$1 AND NOT is_internal ORDER BY display_name",
            entity_type,
        )
        anchored = await anchored_people(db, [entity_id]) if entity_type == "person" else set()
        return [
            t for t in types if not owned_identifier(entity_type, entity_id, t["slug"], anchored)
        ]

    def _ctx(entity_id: str, **extra) -> dict:
        """Build template context with the correct entity-id key."""
        return {entity_id_key: entity_id, **extra}

    async def _rows_response(request, entity_id: str, db, headers: dict):
        """The whole ``#identifiers-table`` tbody (#683)."""
        identifiers = await fetch_identifiers(db, entity_type, entity_id)
        return templates.TemplateResponse(
            request, tmpl_rows, _ctx(entity_id, identifiers=identifiers), headers=headers
        )

    async def _locked(request, entity_id: str, db):
        """A door refusing a key a dataset owns (#710): the rows as they stand, and why."""
        if not is_htmx(request):
            return RedirectResponse(
                with_flash(detail_url(entity_id), "identifier_locked"), status_code=303
            )
        return await _rows_response(
            request, entity_id, db, flash_trigger("warning", IDENTIFIER_LOCKED_MSG)
        )

    async def _taken(request, entity_id: str, db, type_id: str, value: str):
        """A door refusing a value another row holds (#812): the rows as they stand, and who."""
        if not is_htmx(request):
            return RedirectResponse(with_flash(detail_url(entity_id), "exists"), status_code=303)
        holder = await identifier_holder(db, type_id, value)
        message = taken_message(entity_type, entity_id, value, holder)
        return await _rows_response(request, entity_id, db, flash_trigger("warning", message))

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
        ident_types = await _picker_types(db, entity_id)
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
        """Create an identifier, unless a dataset owns its type (#710) or it is taken (#812)."""
        await _get_entity_or_404(entity_id, db)
        await _require_attachable_type(entity_identifier_type_id, db)
        # #710 CR 10: a key added here could never be deleted, so the add is refused too.
        if await dataset_owned_identifier(db, entity_type, entity_id, entity_identifier_type_id):
            return await _locked(request, entity_id, db)
        iid = generate_id()
        if not await _write_or_404(
            db,
            "INSERT INTO identifiers (id, entity_id, entity_identifier_type_id, value)"
            " VALUES ($1, $2, $3, $4)",
            iid,
            entity_id,
            entity_identifier_type_id,
            value.strip(),
        ):
            return await _taken(request, entity_id, db, entity_identifier_type_id, value.strip())
        if not is_htmx(request):
            return RedirectResponse(with_flash(detail_url(entity_id), "saved"), status_code=303)
        return await _rows_response(
            request,
            entity_id,
            db,
            flash_trigger("success", f"<strong>{escape(value.strip())}</strong> added."),
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
        """Return identifier edit form row — or, for a key a dataset owns, the rows and why."""
        row = await _get_identifier_or_404(ident_id, entity_id, db)
        if row["dataset_owned"]:
            # The caller swaps this row (outerHTML): answer with the row, never the tbody (CR 6).
            return templates.TemplateResponse(
                request,
                tmpl_read_row,
                _ctx(entity_id, ident=row),
                headers=flash_trigger("warning", IDENTIFIER_LOCKED_MSG),
            )
        ident_types = await _picker_types(db, entity_id)
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
        """Update an identifier — judged as it is and as it would be (#710)."""
        existing = await _get_identifier_or_404(ident_id, entity_id, db)
        if existing["dataset_owned"]:
            return await _locked(request, entity_id, db)  # before the type check (CR 8)
        await _require_attachable_type(entity_identifier_type_id, db)
        if await dataset_owned_identifier(db, entity_type, entity_id, entity_identifier_type_id):
            return await _locked(request, entity_id, db)
        # A retype counts too: a value free under its old type can be taken under the new.
        if not await _write_or_404(
            db,
            "UPDATE identifiers SET entity_identifier_type_id=$1, value=$2 WHERE id=$3",
            entity_identifier_type_id,
            value.strip(),
            ident_id,
        ):
            return await _taken(request, entity_id, db, entity_identifier_type_id, value.strip())
        if not is_htmx(request):
            return RedirectResponse(with_flash(detail_url(entity_id), "saved"), status_code=303)
        return await _rows_response(
            request,
            entity_id,
            db,
            flash_trigger("success", f"<strong>{escape(value.strip())}</strong> saved."),
        )

    @router.delete("/{ident_id}/")
    async def identifier_delete(
        entity_id: str,
        ident_id: str,
        request: Request,
        user: AdminUser = Depends(get_admin_user),
        db=Depends(get_db),
    ):
        """Delete an identifier, unless a dataset owns it (#710)."""
        existing = await _get_identifier_or_404(ident_id, entity_id, db)
        if existing["dataset_owned"]:
            return await _locked(request, entity_id, db)
        await db.execute("DELETE FROM identifiers WHERE id=$1", ident_id)
        if not is_htmx(request):
            return RedirectResponse(with_flash(detail_url(entity_id), "removed"), status_code=303)
        return await _rows_response(
            request, entity_id, db, flash_trigger("success", "Identifier removed.")
        )

    return router
