"""Admin routes for the pins page (#498): every curation-overlay pin, filterable,
each live one unpinnable from its row.

An override nobody can find is a future mystery, so the per-slot badges on the
detail pages are backed by one list. Unpin here archives *that* row, and only
while it is still the live pin: a row older than the page may have been replaced
since, and unpinning by field would archive the newer pin instead.
"""

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from markupsafe import escape

from src.api.admin.deps import (
    AdminUser,
    flash_trigger,
    get_admin_user,
    get_db,
    is_htmx,
    provision_app_user,
    resolve_query_flash,
    with_flash,
)
from src.api.admin.overlay_slots import PRODUCER_LABEL
from src.api.admin.pins_queries import ENTITY_TYPES, VALID_STATUSES, pin_row, query_pins
from src.core.curation_overlay import unpin_pin

templates = Jinja2Templates(directory="src/templates")
router = APIRouter(prefix="/pins", tags=["admin-pins"])

# Route-local: a rejected no-op is `warning` (#353).
_PIN_FLASH_MESSAGES: dict[str, tuple[str, str]] = {
    "stale": ("warning", "That pin was already replaced or unpinned; nothing changed."),
}


@router.get("/")
async def pins_list(
    request: Request,
    status: str = "active",
    type_: str = Query("", alias="type"),
    flash: str | None = Query(None),
    user: AdminUser = Depends(get_admin_user),
    db=Depends(get_db),
):
    """List pins by status (#306 axis) and entity type."""
    if status not in VALID_STATUSES:
        status = "active"  # never no filter (#306)
    entity_type = type_ if type_ in ENTITY_TYPES else ""
    rows = await query_pins(db, status=status, entity_type=entity_type or None)
    flash_msg, headers = resolve_query_flash(request, _PIN_FLASH_MESSAGES, flash)
    ctx = {
        "user": user,
        "active_section": "pins",
        "rows": rows,
        "status": status,
        "entity_type": entity_type,
        "entity_types": ENTITY_TYPES,
        "producer": PRODUCER_LABEL,
        "flash_msg": flash_msg,
    }
    template = "admin/pins/_region.html" if is_htmx(request) else "admin/pins/list.html"
    return templates.TemplateResponse(request, template, ctx, headers=headers)


@router.post("/{pin_id}/unpin/")
async def pins_unpin(
    pin_id: str,
    request: Request,
    user: AdminUser = Depends(provision_app_user),
    db=Depends(get_db),
):
    """Archive this pin while it is the live one; its producer value returns on the next apply."""
    held = await unpin_pin(db, pin_id, user_id=user.id)
    row = await pin_row(db, pin_id)
    if row is None:
        raise HTTPException(status_code=404)
    if held is None:
        if not is_htmx(request):
            return RedirectResponse(with_flash("/admin/pins/", "stale"), status_code=303)
        headers = flash_trigger("warning", _PIN_FLASH_MESSAGES["stale"][1])
    else:
        if not is_htmx(request):
            return RedirectResponse(with_flash("/admin/pins/", "unpinned"), status_code=303)
        headers = flash_trigger(
            "success",
            f"{escape(row['label'])} unpinned: {PRODUCER_LABEL}'s value returns on the next apply.",
            extra={"refreshOverlay": True},
        )
    return templates.TemplateResponse(request, "admin/pins/_row.html", {"p": row}, headers=headers)
