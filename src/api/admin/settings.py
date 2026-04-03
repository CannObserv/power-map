"""Admin settings views: link types, identifier types."""

import asyncpg
from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from markupsafe import escape

from src.api.admin.deps import (
    AdminUser,
    check_auth,
    flash_trigger,
    get_admin_user,
    get_db,
    is_htmx,
)
from src.api.admin.org_dups import get_org_dup_count
from src.api.admin.people_dups import get_person_dup_count
from src.core.db import generate_id

templates = Jinja2Templates(directory="src/templates")
router = APIRouter(prefix="/settings", tags=["admin-settings"])

_SCOPE_MAP = {"general": False, "social": True}


def _scope_to_is_social(scope: str) -> bool:
    if scope not in _SCOPE_MAP:
        raise HTTPException(status_code=404, detail="Invalid scope")
    return _SCOPE_MAP[scope]


async def _fetch_link_types(db, is_social: bool) -> list:
    return await db.fetch(
        """
        SELECT lt.*, COUNT(l.id) AS usage_count
        FROM link_types lt
        LEFT JOIN links l ON l.link_type_id = lt.id
        WHERE lt.is_social = $1
        GROUP BY lt.id
        ORDER BY lt.display_name
        """,
        is_social,
    )


async def _fetch_identifier_types(db) -> list:
    return await db.fetch(
        """
        SELECT eit.*, COUNT(i.id) AS usage_count
        FROM entity_identifier_types eit
        LEFT JOIN identifiers i ON i.entity_identifier_type_id = eit.id
        GROUP BY eit.id
        ORDER BY eit.display_name
        """
    )


def _base_ctx(user, org_dup_count, person_dup_count, active_section: str = "settings"):
    return {
        "user": user,
        "active_section": active_section,
        "org_dup_count": org_dup_count,
        "person_dup_count": person_dup_count,
    }


# ---------------------------------------------------------------------------
# Landing page
# ---------------------------------------------------------------------------


@router.get("/")
async def settings_index(
    request: Request,
    user: AdminUser | RedirectResponse = Depends(get_admin_user),
    db=Depends(get_db),
    org_dup_count: int = Depends(get_org_dup_count),
    person_dup_count: int = Depends(get_person_dup_count),
):
    redirect, user = check_auth(user)
    if redirect:
        return redirect
    counts = await db.fetchrow(
        """
        SELECT
            (SELECT COUNT(*) FROM link_types WHERE is_social = FALSE) AS general_link_types,
            (SELECT COUNT(*) FROM link_types WHERE is_social = TRUE)  AS social_link_types,
            (SELECT COUNT(*) FROM entity_identifier_types)            AS identifier_types
        """
    )
    return templates.TemplateResponse(
        request,
        "admin/settings/index.html",
        {**_base_ctx(user, org_dup_count, person_dup_count), "counts": counts},
    )


# ---------------------------------------------------------------------------
# Link Types
# ---------------------------------------------------------------------------


@router.get("/link-types/")
async def link_types_page(
    request: Request,
    user: AdminUser | RedirectResponse = Depends(get_admin_user),
    db=Depends(get_db),
    org_dup_count: int = Depends(get_org_dup_count),
    person_dup_count: int = Depends(get_person_dup_count),
):
    redirect, user = check_auth(user)
    if redirect:
        return redirect
    general = await _fetch_link_types(db, False)
    social = await _fetch_link_types(db, True)
    return templates.TemplateResponse(
        request,
        "admin/settings/link_types.html",
        {**_base_ctx(user, org_dup_count, person_dup_count, "settings_link_types"), "general": general, "social": social},  # noqa: E501
    )


@router.get("/link-types/{scope}/new-row/")
async def link_type_new_row(
    scope: str,
    request: Request,
    user: AdminUser | RedirectResponse = Depends(get_admin_user),
):
    redirect, user = check_auth(user)
    if redirect:
        return redirect
    is_social = _scope_to_is_social(scope)
    return templates.TemplateResponse(
        request,
        "admin/settings/partials/_link_type_edit_row.html",
        {"lt": None, "scope": scope, "is_social": is_social},
    )


@router.post("/link-types/{scope}/")
async def link_type_create(
    scope: str,
    request: Request,
    display_name: str = Form(...),
    slug: str = Form(...),
    user: AdminUser | RedirectResponse = Depends(get_admin_user),
    db=Depends(get_db),
):
    redirect, user = check_auth(user)
    if redirect:
        return redirect
    is_social = _scope_to_is_social(scope)
    slug_val = slug.strip()
    if not slug_val:
        raise HTTPException(status_code=422, detail="slug is required")
    lid = generate_id()
    await db.execute(
        "INSERT INTO link_types (id, display_name, slug, is_social) VALUES ($1, $2, $3, $4)",
        lid, display_name.strip(), slug_val, is_social,
    )
    rows = await _fetch_link_types(db, is_social)
    if not is_htmx(request):
        return RedirectResponse("/admin/settings/link-types/", status_code=303)
    return templates.TemplateResponse(
        request,
        "admin/settings/partials/_link_type_rows.html",
        {"rows": rows, "scope": scope},
        headers=flash_trigger(
            "success", f"Link type <strong>{escape(display_name.strip())}</strong> added."
        ),
    )


@router.get("/link-types/{scope}/{item_id}/edit-row/")
async def link_type_edit_row_get(
    scope: str,
    item_id: str,
    request: Request,
    user: AdminUser | RedirectResponse = Depends(get_admin_user),
    db=Depends(get_db),
):
    redirect, user = check_auth(user)
    if redirect:
        return redirect
    is_social = _scope_to_is_social(scope)
    lt = await db.fetchrow(
        "SELECT * FROM link_types WHERE id=$1 AND is_social=$2", item_id, is_social
    )
    if not lt:
        raise HTTPException(status_code=404)
    return templates.TemplateResponse(
        request,
        "admin/settings/partials/_link_type_edit_row.html",
        {"lt": lt, "scope": scope, "is_social": is_social},
    )


@router.post("/link-types/{scope}/{item_id}/edit-row/")
async def link_type_edit_row_post(
    scope: str,
    item_id: str,
    request: Request,
    display_name: str = Form(...),
    slug: str = Form(...),
    user: AdminUser | RedirectResponse = Depends(get_admin_user),
    db=Depends(get_db),
):
    redirect, user = check_auth(user)
    if redirect:
        return redirect
    is_social = _scope_to_is_social(scope)
    slug_val = slug.strip()
    if not slug_val:
        raise HTTPException(status_code=422, detail="slug is required")
    lt = await db.fetchrow(
        "SELECT id FROM link_types WHERE id=$1 AND is_social=$2", item_id, is_social
    )
    if not lt:
        raise HTTPException(status_code=404)
    await db.execute(
        "UPDATE link_types SET display_name=$1, slug=$2 WHERE id=$3",
        display_name.strip(), slug_val, item_id,
    )
    row = await db.fetchrow(
        """
        SELECT lt.*, COUNT(l.id) AS usage_count
        FROM link_types lt
        LEFT JOIN links l ON l.link_type_id = lt.id
        WHERE lt.id = $1
        GROUP BY lt.id
        """,
        item_id,
    )
    if not is_htmx(request):
        return RedirectResponse("/admin/settings/link-types/", status_code=303)
    return templates.TemplateResponse(
        request,
        "admin/settings/partials/_link_type_row.html",
        {"lt": row, "scope": scope},
        headers=flash_trigger(
            "success", f"Link type <strong>{escape(display_name.strip())}</strong> saved."
        ),
    )


@router.get("/link-types/{scope}/{item_id}/read-row/")
async def link_type_read_row(
    scope: str,
    item_id: str,
    request: Request,
    user: AdminUser | RedirectResponse = Depends(get_admin_user),
    db=Depends(get_db),
):
    redirect, user = check_auth(user)
    if redirect:
        return redirect
    is_social = _scope_to_is_social(scope)
    row = await db.fetchrow(
        """
        SELECT lt.*, COUNT(l.id) AS usage_count
        FROM link_types lt
        LEFT JOIN links l ON l.link_type_id = lt.id
        WHERE lt.id = $1 AND lt.is_social = $2
        GROUP BY lt.id
        """,
        item_id, is_social,
    )
    if not row:
        raise HTTPException(status_code=404)
    return templates.TemplateResponse(
        request,
        "admin/settings/partials/_link_type_row.html",
        {"lt": row, "scope": scope},
    )


@router.delete("/link-types/{scope}/{item_id}/")
async def link_type_delete(
    scope: str,
    item_id: str,
    request: Request,
    user: AdminUser | RedirectResponse = Depends(get_admin_user),
    db=Depends(get_db),
):
    redirect, user = check_auth(user)
    if redirect:
        return redirect
    is_social = _scope_to_is_social(scope)
    existing = await db.fetchrow(
        "SELECT id FROM link_types WHERE id=$1 AND is_social=$2", item_id, is_social
    )
    if not existing:
        raise HTTPException(status_code=404)
    try:
        await db.execute("DELETE FROM link_types WHERE id=$1", item_id)
    except asyncpg.ForeignKeyViolationError:
        if not is_htmx(request):
            raise HTTPException(status_code=409, detail="Cannot delete: record is in use")
        return HTMLResponse(
            content="",
            status_code=200,
            headers=flash_trigger("error", "Cannot delete: this link type is in use."),
        )
    return HTMLResponse(
        content="",
        status_code=200,
        headers=flash_trigger("info", "Link type deleted."),
    )


# ---------------------------------------------------------------------------
# Identifier Types
# ---------------------------------------------------------------------------


@router.get("/identifier-types/")
async def identifier_types_page(
    request: Request,
    user: AdminUser | RedirectResponse = Depends(get_admin_user),
    db=Depends(get_db),
    org_dup_count: int = Depends(get_org_dup_count),
    person_dup_count: int = Depends(get_person_dup_count),
):
    redirect, user = check_auth(user)
    if redirect:
        return redirect
    rows = await _fetch_identifier_types(db)
    return templates.TemplateResponse(
        request,
        "admin/settings/identifier_types.html",
        {**_base_ctx(user, org_dup_count, person_dup_count, "settings_identifier_types"), "rows": rows},  # noqa: E501
    )


@router.get("/identifier-types/new-row/")
async def identifier_type_new_row(
    request: Request,
    user: AdminUser | RedirectResponse = Depends(get_admin_user),
):
    redirect, user = check_auth(user)
    if redirect:
        return redirect
    return templates.TemplateResponse(
        request,
        "admin/settings/partials/_identifier_type_edit_row.html",
        {"eit": None},
    )


@router.post("/identifier-types/")
async def identifier_type_create(
    request: Request,
    display_name: str = Form(...),
    slug: str = Form(...),
    full_name: str = Form(...),
    entity_type: str = Form(...),
    user: AdminUser | RedirectResponse = Depends(get_admin_user),
    db=Depends(get_db),
):
    redirect, user = check_auth(user)
    if redirect:
        return redirect
    slug_val = slug.strip()
    full_name_val = full_name.strip()
    if not slug_val:
        raise HTTPException(status_code=422, detail="slug is required")
    if not full_name_val:
        raise HTTPException(status_code=422, detail="full_name is required")
    iid = generate_id()
    await db.execute(
        "INSERT INTO entity_identifier_types"
        " (id, display_name, slug, full_name, entity_type)"
        " VALUES ($1, $2, $3, $4, $5)",
        iid, display_name.strip(), slug_val, full_name_val, entity_type,
    )
    rows = await _fetch_identifier_types(db)
    if not is_htmx(request):
        return RedirectResponse("/admin/settings/identifier-types/", status_code=303)
    return templates.TemplateResponse(
        request,
        "admin/settings/partials/_identifier_type_rows.html",
        {"rows": rows},
        headers=flash_trigger(
            "success", f"Identifier type <strong>{escape(display_name.strip())}</strong> added."
        ),
    )


@router.get("/identifier-types/{item_id}/edit-row/")
async def identifier_type_edit_row_get(
    item_id: str,
    request: Request,
    user: AdminUser | RedirectResponse = Depends(get_admin_user),
    db=Depends(get_db),
):
    redirect, user = check_auth(user)
    if redirect:
        return redirect
    eit = await db.fetchrow(
        "SELECT * FROM entity_identifier_types WHERE id=$1", item_id
    )
    if not eit:
        raise HTTPException(status_code=404)
    return templates.TemplateResponse(
        request,
        "admin/settings/partials/_identifier_type_edit_row.html",
        {"eit": eit},
    )


@router.post("/identifier-types/{item_id}/edit-row/")
async def identifier_type_edit_row_post(
    item_id: str,
    request: Request,
    display_name: str = Form(...),
    slug: str = Form(...),
    full_name: str = Form(...),
    entity_type: str = Form(...),
    user: AdminUser | RedirectResponse = Depends(get_admin_user),
    db=Depends(get_db),
):
    redirect, user = check_auth(user)
    if redirect:
        return redirect
    slug_val = slug.strip()
    full_name_val = full_name.strip()
    if not slug_val:
        raise HTTPException(status_code=422, detail="slug is required")
    if not full_name_val:
        raise HTTPException(status_code=422, detail="full_name is required")
    existing = await db.fetchrow(
        "SELECT id FROM entity_identifier_types WHERE id=$1", item_id
    )
    if not existing:
        raise HTTPException(status_code=404)
    await db.execute(
        "UPDATE entity_identifier_types"
        " SET display_name=$1, slug=$2, full_name=$3, entity_type=$4"
        " WHERE id=$5",
        display_name.strip(), slug_val, full_name_val, entity_type, item_id,
    )
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
    if not is_htmx(request):
        return RedirectResponse("/admin/settings/identifier-types/", status_code=303)
    return templates.TemplateResponse(
        request,
        "admin/settings/partials/_identifier_type_row.html",
        {"eit": row},
        headers=flash_trigger(
            "success", f"Identifier type <strong>{escape(display_name.strip())}</strong> saved."
        ),
    )


@router.get("/identifier-types/{item_id}/read-row/")
async def identifier_type_read_row(
    item_id: str,
    request: Request,
    user: AdminUser | RedirectResponse = Depends(get_admin_user),
    db=Depends(get_db),
):
    redirect, user = check_auth(user)
    if redirect:
        return redirect
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


@router.delete("/identifier-types/{item_id}/")
async def identifier_type_delete(
    item_id: str,
    request: Request,
    user: AdminUser | RedirectResponse = Depends(get_admin_user),
    db=Depends(get_db),
):
    redirect, user = check_auth(user)
    if redirect:
        return redirect
    existing = await db.fetchrow(
        "SELECT id FROM entity_identifier_types WHERE id=$1", item_id
    )
    if not existing:
        raise HTTPException(status_code=404)
    try:
        await db.execute("DELETE FROM entity_identifier_types WHERE id=$1", item_id)
    except asyncpg.ForeignKeyViolationError:
        if not is_htmx(request):
            raise HTTPException(status_code=409, detail="Cannot delete: record is in use")
        return HTMLResponse(
            content="",
            status_code=200,
            headers=flash_trigger("error", "Cannot delete: this identifier type is in use."),
        )
    return HTMLResponse(
        content="",
        status_code=200,
        headers=flash_trigger("info", "Identifier type deleted."),
    )
