"""Shared factory for entity contact-method CRUD routers (orgs and people)."""

from collections.abc import Callable
from typing import Literal

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from markupsafe import escape

from src.api.admin.deps import AdminUser, flash_trigger, get_admin_user, get_db, is_htmx
from src.core.db import generate_id
from src.core.normalizers.email import EmailNormalizer
from src.core.normalizers.phone import PhoneNormalizer

templates = Jinja2Templates(directory="src/templates")

_email_normalizer = EmailNormalizer()
_phone_normalizer = PhoneNormalizer()

_CONTACT_ERROR_MESSAGES = {
    "email": "Enter a valid email address.",
    "phone": "Enter a valid phone number (e.g. (206) 555-1234 or +12065551234).",
}


def make_contacts_router(
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
    """Return a configured contacts APIRouter for the given entity type.

    Parameters
    ----------
    entity_type:
        DB value stored in contact_methods.entity_type (e.g. ``'organization'``).
    entity_id_key:
        Template context key for the entity id (e.g. ``'org_id'`` or ``'person_id'``).
    prefix:
        Router URL prefix — must contain ``{entity_id}`` as the path variable
        (e.g. ``'/orgs/{entity_id}/contacts'``).
    tags:
        FastAPI tags list.
    entity_table:
        Table name used to verify the parent entity exists.
    entity_not_found_msg:
        Detail string for 404 when parent entity is missing.
    tmpl_form_row:
        Template path for the contact form row partial.
    tmpl_read_row:
        Template path for the contact read row partial.
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

    def _ctx(entity_id: str, **extra) -> dict:
        """Build template context with the correct entity-id key."""
        return {entity_id_key: entity_id, **extra}

    # ---- routes -----------------------------------------------------------------

    @router.get("/new-row/")
    async def contact_new_row(
        entity_id: str,
        request: Request,
        contact_type: Literal["email", "phone"] = Query(...),
        user: AdminUser = Depends(get_admin_user),
        db=Depends(get_db),
    ):
        """Return empty contact form row for the given contact_type (email|phone)."""
        await _get_entity_or_404(entity_id, db)
        return templates.TemplateResponse(
            request,
            tmpl_form_row,
            _ctx(entity_id, c=None, contact_type=contact_type),
        )

    @router.post("/")
    async def contact_create(
        entity_id: str,
        request: Request,
        contact_type: Literal["email", "phone"] = Form(...),
        value: str = Form(...),
        display_label: str = Form(""),
        user: AdminUser = Depends(get_admin_user),
        db=Depends(get_db),
    ):
        """Create a new contact method."""
        await _get_entity_or_404(entity_id, db)
        raw_value = value.strip()
        try:
            if contact_type == "email":
                raw_value = _email_normalizer.normalize(raw_value).value
            elif contact_type == "phone":
                raw_value = _phone_normalizer.normalize(raw_value).value
        except ValueError:
            if not is_htmx(request):
                return RedirectResponse(detail_url(entity_id), status_code=303)
            return templates.TemplateResponse(
                request,
                tmpl_form_row,
                _ctx(
                    entity_id,
                    c=None,
                    contact_type=contact_type,
                    value_input=value.strip(),
                    error=_CONTACT_ERROR_MESSAGES.get(contact_type, "Invalid value."),
                ),
            )
        cid = generate_id()
        await db.execute(
            "INSERT INTO contact_methods"
            " (id, entity_type, entity_id, contact_type, value, display_label)"
            f" VALUES ($1, '{entity_type}', $2, $3, $4, $5)",
            cid,
            entity_id,
            contact_type,
            raw_value,
            display_label.strip() or None,
        )
        row = await db.fetchrow("SELECT * FROM contact_methods WHERE id=$1", cid)
        if not is_htmx(request):
            return RedirectResponse(detail_url(entity_id), status_code=303)
        return templates.TemplateResponse(
            request,
            tmpl_read_row,
            _ctx(entity_id, c=row),
            headers=flash_trigger("success", f"<strong>{escape(raw_value)}</strong> added."),
        )

    @router.get("/{contact_id}/read-row/")
    async def contact_read_row(
        entity_id: str,
        contact_id: str,
        request: Request,
        user: AdminUser = Depends(get_admin_user),
        db=Depends(get_db),
    ):
        """Return read-only contact row (used by Cancel on edit form)."""
        contact = await db.fetchrow(
            "SELECT * FROM contact_methods"
            f" WHERE id=$1 AND entity_type='{entity_type}' AND entity_id=$2",
            contact_id,
            entity_id,
        )
        if not contact:
            raise HTTPException(status_code=404)
        return templates.TemplateResponse(
            request, tmpl_read_row, _ctx(entity_id, c=contact)
        )

    @router.get("/{contact_id}/edit-row/")
    async def contact_edit_row_get(
        entity_id: str,
        contact_id: str,
        request: Request,
        user: AdminUser = Depends(get_admin_user),
        db=Depends(get_db),
    ):
        """Return contact edit form row."""
        contact = await db.fetchrow(
            "SELECT * FROM contact_methods"
            f" WHERE id=$1 AND entity_type='{entity_type}' AND entity_id=$2",
            contact_id,
            entity_id,
        )
        if not contact:
            raise HTTPException(status_code=404)
        return templates.TemplateResponse(
            request,
            tmpl_form_row,
            _ctx(entity_id, c=contact, contact_type=contact["contact_type"]),
        )

    @router.post("/{contact_id}/edit-row/")
    async def contact_edit_row_post(
        entity_id: str,
        contact_id: str,
        request: Request,
        value: str = Form(...),
        display_label: str = Form(""),
        user: AdminUser = Depends(get_admin_user),
        db=Depends(get_db),
    ):
        """Update a contact method (contact_type is immutable)."""
        existing = await db.fetchrow(
            "SELECT * FROM contact_methods"
            f" WHERE id=$1 AND entity_type='{entity_type}' AND entity_id=$2",
            contact_id,
            entity_id,
        )
        if not existing:
            raise HTTPException(status_code=404)
        raw_value = value.strip()
        contact_type = existing["contact_type"]
        try:
            if contact_type == "email":
                raw_value = _email_normalizer.normalize(raw_value).value
            elif contact_type == "phone":
                raw_value = _phone_normalizer.normalize(raw_value).value
        except ValueError:
            if not is_htmx(request):
                return RedirectResponse(detail_url(entity_id), status_code=303)
            return templates.TemplateResponse(
                request,
                tmpl_form_row,
                _ctx(
                    entity_id,
                    c=existing,
                    contact_type=contact_type,
                    value_input=value.strip(),
                    error=_CONTACT_ERROR_MESSAGES.get(contact_type, "Invalid value."),
                ),
            )
        await db.execute(
            "UPDATE contact_methods SET value=$1, display_label=$2 WHERE id=$3",
            raw_value,
            display_label.strip() or None,
            contact_id,
        )
        row = await db.fetchrow("SELECT * FROM contact_methods WHERE id=$1", contact_id)
        if not is_htmx(request):
            return RedirectResponse(detail_url(entity_id), status_code=303)
        return templates.TemplateResponse(
            request,
            tmpl_read_row,
            _ctx(entity_id, c=row),
            headers=flash_trigger("success", f"<strong>{escape(raw_value)}</strong> saved."),
        )

    @router.delete("/{contact_id}/")
    async def contact_delete(
        entity_id: str,
        contact_id: str,
        request: Request,
        user: AdminUser = Depends(get_admin_user),
        db=Depends(get_db),
    ):
        """Delete a contact method."""
        existing = await db.fetchrow(
            "SELECT id FROM contact_methods"
            f" WHERE id=$1 AND entity_type='{entity_type}' AND entity_id=$2",
            contact_id,
            entity_id,
        )
        if not existing:
            raise HTTPException(status_code=404)
        await db.execute("DELETE FROM contact_methods WHERE id=$1", contact_id)
        return HTMLResponse(
            content="",
            status_code=200,
            headers=flash_trigger("info", "Contact removed."),
        )

    return router
