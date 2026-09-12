"""Admin routes for the curation overlay's slot lines, Pin and Unpin (#498).

Each producer-owned slot on an entity detail page, and each edit form for one,
hosts a self-loading fragment from here — the dup-badge pattern: the host is
static markup, loads on page render and reloads on ``refreshOverlay`` (fired by
any edit that pinned), so no panel or mutation response has to carry overlay
state. The fragment is empty for an entity outside the producer's row scope.

Pin keeps PM's current value for the slot over every later snapshot; Unpin
archives the pin so the producer's value returns on the next apply. Each acts
only on what the line showed: Pin only while no pin is live, Unpin only while
the pin it names is still the slot's live one — a line older than the slot must
not replace or archive a pin the curator never saw.
"""

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from markupsafe import escape

from src.api.admin.deps import (
    SHARED_FLASH_MESSAGES,
    AdminUser,
    flash_trigger,
    get_admin_user,
    get_db,
    is_htmx,
    provision_app_user,
    with_flash,
)
from src.api.admin.overlay_slots import (
    PRODUCER_LABEL,
    SLOTS,
    Slot,
    read_slots,
    slot_state,
)
from src.core.curation_overlay import OverlayError, active_pin, pin, unpin_pin

templates = Jinja2Templates(directory="src/templates")
router = APIRouter(prefix="/_overlay", tags=["admin-overlay"])

_TEMPLATE = "admin/shared/_overlay_slot.html"
_ENTITY_TABLES = {"person": "people", "organization": "organizations"}
_DETAIL_PREFIXES = {"person": "/admin/people/", "organization": "/admin/orgs/"}
_VARIANTS = ("status", "note")


def _slot_or_404(entity_type: str, field: str) -> Slot:
    slot = SLOTS.get((entity_type, field))
    if slot is None:
        raise HTTPException(status_code=404, detail="No pinnable slot by that name")
    return slot


async def _entity_or_404(db, entity_type: str, entity_id: str) -> None:
    table = _ENTITY_TABLES[entity_type]  # every registered slot's type has a table
    if not await db.fetchval(f"SELECT 1 FROM {table} WHERE id = $1", entity_id):
        raise HTTPException(status_code=404)


def _detail_url(entity_type: str, entity_id: str) -> str:
    return f"{_DETAIL_PREFIXES[entity_type]}{entity_id}/"


async def _render(request: Request, db, entity_type, entity_id, field, *, variant, headers=None):
    return templates.TemplateResponse(
        request,
        _TEMPLATE,
        {
            "state": await slot_state(db, entity_type, entity_id, field),
            "variant": variant,
            "producer": PRODUCER_LABEL,
            "base": f"/admin/_overlay/{entity_type}/{entity_id}/{field}/",
        },
        headers=headers,
    )


@router.get("/{entity_type}/{entity_id}/{field}/")
async def overlay_slot(
    entity_type: str,
    entity_id: str,
    field: str,
    request: Request,
    variant: str = Query("status"),
    user: AdminUser = Depends(get_admin_user),
    db=Depends(get_db),
):
    """The slot line (``variant=status``) or the edit form's note (``variant=note``)."""
    _slot_or_404(entity_type, field)
    await _entity_or_404(db, entity_type, entity_id)
    variant = variant if variant in _VARIANTS else "status"
    return await _render(request, db, entity_type, entity_id, field, variant=variant)


@router.post("/{entity_type}/{entity_id}/{field}/pin/")
async def overlay_pin(
    entity_type: str,
    entity_id: str,
    field: str,
    request: Request,
    user: AdminUser = Depends(provision_app_user),
    db=Depends(get_db),
):
    """Pin the slot's current value: PM keeps it over every later snapshot.

    Only while no pin is live — the line offers Pin on an unpinned slot alone. A
    pin live now arrived after the page loaded (a merge carried it here, another
    curator pinned) and may hold another value; it is not this Pin's to replace,
    so a warning and the line as it stands.
    """
    slot = _slot_or_404(entity_type, field)
    await _entity_or_404(db, entity_type, entity_id)
    try:
        async with db.transaction():
            already = await active_pin(db, entity_type, entity_id, field) is not None
            if not already:
                value = (await read_slots(db, entity_type, entity_id, (field,)))[field]
                await pin(db, entity_type, entity_id, field, value, user_id=user.id)
    except OverlayError:
        # The Pin control renders only in scope; a stale page is the way here.
        if not is_htmx(request):
            return RedirectResponse(
                with_flash(_detail_url(entity_type, entity_id), "invalid"), status_code=303
            )
        return HTMLResponse(
            content="",
            headers=flash_trigger(
                "warning", f"{PRODUCER_LABEL} does not maintain this record: nothing to pin."
            ),
        )
    if already:
        if not is_htmx(request):
            return RedirectResponse(
                with_flash(_detail_url(entity_type, entity_id), "already_pinned"), status_code=303
            )
        return await _render(
            request,
            db,
            entity_type,
            entity_id,
            field,
            variant="status",
            headers=flash_trigger("warning", SHARED_FLASH_MESSAGES["already_pinned"][1]),
        )
    if not is_htmx(request):
        return RedirectResponse(
            with_flash(_detail_url(entity_type, entity_id), "pinned"), status_code=303
        )
    return await _render(
        request,
        db,
        entity_type,
        entity_id,
        field,
        variant="status",
        headers=flash_trigger(
            "success",
            f"{escape(slot.label)} pinned: PM keeps it over {PRODUCER_LABEL}'s value.",
            extra={"refreshOverlay": True},
        ),
    )


@router.post("/{entity_type}/{entity_id}/{field}/unpin/")
async def overlay_unpin(
    entity_type: str,
    entity_id: str,
    field: str,
    request: Request,
    pin_id: str = Form(...),
    user: AdminUser = Depends(provision_app_user),
    db=Depends(get_db),
):
    """Archive the pin the line showed: the producer's value returns on the next apply.

    Only while ``pin_id`` is still this slot's live pin — checked against the slot,
    so an id from another field archives nothing. Otherwise a warning and the line
    as it now stands.
    """
    slot = _slot_or_404(entity_type, field)
    await _entity_or_404(db, entity_type, entity_id)
    async with db.transaction():
        live = await active_pin(db, entity_type, entity_id, field)
        held = None
        if live is not None and live.id == pin_id:
            held = await unpin_pin(db, pin_id, user_id=user.id)
    if held is None:
        if not is_htmx(request):
            return RedirectResponse(
                with_flash(_detail_url(entity_type, entity_id), "pin_stale"), status_code=303
            )
        return await _render(
            request,
            db,
            entity_type,
            entity_id,
            field,
            variant="status",
            headers=flash_trigger("warning", SHARED_FLASH_MESSAGES["pin_stale"][1]),
        )
    if not is_htmx(request):
        return RedirectResponse(
            with_flash(_detail_url(entity_type, entity_id), "unpinned"), status_code=303
        )
    return await _render(
        request,
        db,
        entity_type,
        entity_id,
        field,
        variant="status",
        headers=flash_trigger(
            "success",
            f"{escape(slot.label)} unpinned: {PRODUCER_LABEL}'s value returns on the next apply.",
            extra={"refreshOverlay": True},
        ),
    )
