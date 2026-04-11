"""Admin CRUD for person names."""

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
    person_header_extra,
)
from src.core.db import generate_id

templates = Jinja2Templates(directory="src/templates")
router = APIRouter(prefix="/people/{person_id}/names", tags=["admin-person-names"])


async def _get_person_or_404(person_id: str, db):
    person = await db.fetchrow("SELECT id FROM people WHERE id=$1", person_id)
    if not person:
        raise HTTPException(status_code=404, detail="Person not found")
    return person


async def _maybe_promote_sole_name(person_id: str, db) -> None:
    """If the person has exactly one name and it is not canonical, promote it."""
    rows = await db.fetch(
        "SELECT id, is_canonical FROM person_names WHERE person_id=$1",
        person_id,
    )
    if len(rows) == 1 and not rows[0]["is_canonical"]:
        await db.execute(
            "UPDATE person_names SET is_canonical=TRUE WHERE id=$1",
            rows[0]["id"],
        )


@router.get("/new-row/")
async def name_new_row(
    person_id: str,
    request: Request,
    user: AdminUser | RedirectResponse = Depends(get_admin_user),
    db=Depends(get_db),
):
    """Return empty name form row."""
    redirect, user = check_auth(user)
    if redirect:
        return redirect
    await _get_person_or_404(person_id, db)
    return templates.TemplateResponse(
        request,
        "admin/people/partials/_name_form_row.html",
        {"person_id": person_id, "n": None},
    )


@router.post("/")
async def name_create(
    person_id: str,
    request: Request,
    name: str = Form(...),
    name_type: str = Form("legal"),
    is_canonical: str = Form(""),
    user: AdminUser | RedirectResponse = Depends(get_admin_user),
    db=Depends(get_db),
):
    """Create a new person name."""
    redirect, user = check_auth(user)
    if redirect:
        return redirect
    await _get_person_or_404(person_id, db)
    nid = generate_id()
    async with db.transaction():
        if is_canonical == "true":
            await db.execute(
                "UPDATE person_names SET is_canonical=FALSE"
                " WHERE person_id=$1 AND is_canonical=TRUE",
                person_id,
            )
        await db.execute(
            "INSERT INTO person_names (id, person_id, name, name_type, is_canonical)"
            " VALUES ($1, $2, $3, $4, $5)",
            nid,
            person_id,
            name.strip(),
            name_type,
            is_canonical == "true",
        )
    if not is_htmx(request):
        return RedirectResponse(f"/admin/people/{person_id}/", status_code=303)
    names = await db.fetch(
        "SELECT * FROM person_names WHERE person_id=$1"
        " ORDER BY is_canonical DESC, name_type, name",
        person_id,
    )
    return templates.TemplateResponse(
        request,
        "admin/people/partials/_name_rows.html",
        {"person_id": person_id, "names": names},
        headers=flash_trigger(
            "success",
            f"Name <strong>{escape(name.strip())}</strong> added.",
            extra=await person_header_extra(person_id, db),
        ),
    )


@router.get("/{name_id}/read-row/")
async def name_read_row(
    person_id: str,
    name_id: str,
    request: Request,
    user: AdminUser | RedirectResponse = Depends(get_admin_user),
    db=Depends(get_db),
):
    """Return read-only name row (used by Cancel on edit form)."""
    redirect, user = check_auth(user)
    if redirect:
        return redirect
    name_row = await db.fetchrow(
        "SELECT * FROM person_names WHERE id=$1 AND person_id=$2",
        name_id,
        person_id,
    )
    if not name_row:
        raise HTTPException(status_code=404)
    return templates.TemplateResponse(
        request, "admin/people/partials/_name_row.html", {"person_id": person_id, "n": name_row}
    )


@router.get("/{name_id}/edit-row/")
async def name_edit_row_get(
    person_id: str,
    name_id: str,
    request: Request,
    user: AdminUser | RedirectResponse = Depends(get_admin_user),
    db=Depends(get_db),
):
    """Return name edit form row."""
    redirect, user = check_auth(user)
    if redirect:
        return redirect
    name_row = await db.fetchrow(
        "SELECT * FROM person_names WHERE id=$1 AND person_id=$2",
        name_id,
        person_id,
    )
    if not name_row:
        raise HTTPException(status_code=404)
    return templates.TemplateResponse(
        request,
        "admin/people/partials/_name_form_row.html",
        {"person_id": person_id, "n": name_row},
    )


@router.post("/{name_id}/edit-row/")
async def name_edit_row_post(
    person_id: str,
    name_id: str,
    request: Request,
    name: str = Form(...),
    name_type: str = Form("legal"),
    is_canonical: str = Form(""),
    user: AdminUser | RedirectResponse = Depends(get_admin_user),
    db=Depends(get_db),
):
    """Update a person name."""
    redirect, user = check_auth(user)
    if redirect:
        return redirect
    existing = await db.fetchrow(
        "SELECT * FROM person_names WHERE id=$1 AND person_id=$2",
        name_id,
        person_id,
    )
    if not existing:
        raise HTTPException(status_code=404)
    if is_canonical != "true" and existing["is_canonical"]:
        # Guard runs outside the transaction intentionally: a concurrent promotion
        # (another request canonicalizing a different name) would make this check
        # false — i.e., the save would be allowed — which is the safe direction.
        other_canonical = await db.fetchval(
            "SELECT id FROM person_names"
            " WHERE person_id=$1 AND is_canonical=TRUE AND id != $2",
            person_id,
            name_id,
        )
        if not other_canonical:
            if not is_htmx(request):
                return RedirectResponse(f"/admin/people/{person_id}/", status_code=303)
            return HTMLResponse(
                content="",
                status_code=200,
                headers=flash_trigger(
                    "error",
                    "Cannot remove canonical. Promote another name first.",
                ),
            )
    async with db.transaction():
        if is_canonical == "true":
            await db.execute(
                "UPDATE person_names SET is_canonical=FALSE"
                " WHERE person_id=$1 AND is_canonical=TRUE AND id != $2",
                person_id,
                name_id,
            )
        await db.execute(
            "UPDATE person_names SET name=$1, name_type=$2, is_canonical=$3 WHERE id=$4",
            name.strip(),
            name_type,
            is_canonical == "true",
            name_id,
        )
    if not is_htmx(request):
        return RedirectResponse(f"/admin/people/{person_id}/", status_code=303)
    names = await db.fetch(
        "SELECT * FROM person_names WHERE person_id=$1"
        " ORDER BY is_canonical DESC, name_type, name",
        person_id,
    )
    return templates.TemplateResponse(
        request,
        "admin/people/partials/_name_rows.html",
        {"person_id": person_id, "names": names},
        headers=flash_trigger(
            "success",
            f"Name <strong>{escape(name.strip())}</strong> saved.",
            extra=await person_header_extra(person_id, db),
        ),
    )


@router.delete("/{name_id}/")
async def name_delete(
    person_id: str,
    name_id: str,
    request: Request,
    user: AdminUser | RedirectResponse = Depends(get_admin_user),
    db=Depends(get_db),
):
    """Delete a person name."""
    redirect, user = check_auth(user)
    if redirect:
        return redirect
    existing = await db.fetchrow(
        "SELECT id FROM person_names WHERE id=$1 AND person_id=$2",
        name_id,
        person_id,
    )
    if not existing:
        raise HTTPException(status_code=404)
    async with db.transaction():
        name_count = await db.fetchval(
            "SELECT count(*) FROM person_names WHERE person_id=$1",
            person_id,
        )
        if name_count == 1:
            if not is_htmx(request):
                raise HTTPException(
                    status_code=409,
                    detail="Cannot remove the only name.",
                )
            return HTMLResponse(
                content="",
                status_code=200,
                headers=flash_trigger(
                    "error",
                    "Cannot remove the only name.",
                ),
            )
        await db.execute("DELETE FROM person_names WHERE id=$1", name_id)
        await _maybe_promote_sole_name(person_id, db)
    if not is_htmx(request):
        return RedirectResponse(f"/admin/people/{person_id}/", status_code=303)
    names = await db.fetch(
        "SELECT * FROM person_names WHERE person_id=$1"
        " ORDER BY is_canonical DESC, name_type, name",
        person_id,
    )
    return templates.TemplateResponse(
        request,
        "admin/people/partials/_name_rows.html",
        {"person_id": person_id, "names": names},
        headers=flash_trigger(
            "info", "Name removed.", extra=await person_header_extra(person_id, db)
        ),
    )
