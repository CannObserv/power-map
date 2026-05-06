"""Admin CRUD for `person_name_parts` (sidecar to person_names, 1:0..1).

Phase 2d (#123): the structured-parts editor inside the per-name edit
drawer posts here. Two routes:

- POST `/admin/people/{person_id}/names/{name_id}/parts/`
      Upsert (INSERT … ON CONFLICT DO UPDATE). Empty/blank values are
      filtered before write; if every field is empty the request is a
      no-op (no row written / existing row left intact).
- POST `/admin/people/{person_id}/names/{name_id}/parts/delete/`
      DELETE the parts row. Idempotent (no-op if absent).

visibility-allowlist (issue #121): the parent-name guard SELECTs from
person_names without a visibility predicate because admins editing a
specific name row are operating on a row they already loaded; gating
that lookup on visibility would lock admins out of legal_only / hidden
rows they're trying to enrich. This file is added to the lint
allow-list for the same reason as people_names.py.
"""

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from markupsafe import escape

from src.api.admin.deps import (
    AdminUser,
    flash_trigger,
    get_admin_user,
    get_db,
    is_htmx,
)

templates = Jinja2Templates(directory="src/templates")
router = APIRouter(prefix="/people", tags=["admin-person-name-parts"])

# Mirror person_name_parts.primary_identifier CHECK constraint.
_PRIMARY_IDENTIFIERS: tuple[str, ...] = ("family", "given", "patronymic", "mononym")

ARRAY_CAP = 5


def _summary_oob_fragment(name_id: str, *, has_parts: bool) -> str:
    """HTML fragment that swaps just the editor's <summary> via HTMX OOB.

    Returned by the upsert/delete handlers so the "set" badge reflects
    the new state without re-rendering (and collapsing) the entire
    <details> the user is mid-edit in.

    `name_id` is escaped before interpolation: in practice it's always
    a server-generated ULID that has already passed through the
    `_ensure_name_belongs_to_person` SELECT, but escaping defends
    against a future caller that bypasses that guard.
    """
    nid = escape(name_id)
    badge = ' <span class="badge badge--inactive">set</span>' if has_parts else ""
    return (
        f'<summary id="parts-summary-{nid}" hx-swap-oob="outerHTML"'
        ' style="cursor:pointer;font-size:0.85rem;color:var(--color-text-muted)">'
        f"Details{badge}"
        "</summary>"
    )


def _trim_array(values: list[str] | None) -> list[str]:
    """Strip whitespace, drop empty entries, preserve order."""
    if not values:
        return []
    return [v.strip() for v in values if v and v.strip()]


def _flash(request: Request, msg: str) -> HTMLResponse:
    """HTMX flash helper — non-HTMX raises 422 instead."""
    if not is_htmx(request):
        raise HTTPException(status_code=422, detail=msg)
    return HTMLResponse(
        content="",
        status_code=200,
        headers=flash_trigger("error", escape(msg)),
    )


async def _ensure_name_belongs_to_person(db, person_id: str, name_id: str) -> None:
    row = await db.fetchrow(
        "SELECT 1 FROM person_names WHERE id=$1 AND person_id=$2",
        name_id, person_id,
    )
    if not row:
        raise HTTPException(status_code=404, detail="Name not found for this person")


@router.post("/{person_id}/names/{name_id}/parts/")
async def name_parts_upsert(
    person_id: str,
    name_id: str,
    request: Request,
    given_names: list[str] = Form([]),
    family_names: list[str] = Form([]),
    additional_names: list[str] = Form([]),
    honorific_prefix: str | None = Form(None),
    honorific_suffix: str | None = Form(None),
    primary_identifier: str | None = Form(None),
    user: AdminUser = Depends(get_admin_user),
    db=Depends(get_db),
):
    """Upsert the parts row for `name_id`.

    All-empty payload is a no-op. Caps and primary_identifier validation
    surface as form errors (HTMX flash; non-HTMX 422).
    """
    await _ensure_name_belongs_to_person(db, person_id, name_id)

    # Cap check happens BEFORE trimming so users see "no more than N"
    # against the count they typed, not the post-trim count. Empties
    # contributing to the cap is intentional — keeps the message
    # predictable.
    for label, vals in (
        ("given_names", given_names),
        ("family_names", family_names),
        ("additional_names", additional_names),
    ):
        if len(vals) > ARRAY_CAP:
            return _flash(
                request,
                f"{label}: no more than {ARRAY_CAP} entries (got {len(vals)}).",
            )

    given = _trim_array(given_names)
    family = _trim_array(family_names)
    additional = _trim_array(additional_names)
    pre = (honorific_prefix or "").strip() or None
    suf = (honorific_suffix or "").strip() or None
    pi_raw = (primary_identifier or "").strip()

    if pi_raw and pi_raw not in _PRIMARY_IDENTIFIERS:
        allowed = ", ".join(_PRIMARY_IDENTIFIERS)
        return _flash(
            request,
            f"primary_identifier must be one of: {allowed} (got {pi_raw!r}).",
        )
    pi: str | None = pi_raw or None

    # All-empty → no row written / no UPDATE triggered. Silent success
    # so the same form save works as a "remove" for a row that started
    # empty.
    if not (given or family or additional or pre or suf or pi):
        if not is_htmx(request):
            return RedirectResponse(
                f"/admin/people/{person_id}/", status_code=303,
            )
        return HTMLResponse(content="", status_code=200)

    await db.execute(
        "INSERT INTO person_name_parts ("
        "  person_name_id, given_names, family_names, additional_names,"
        "  honorific_prefix, honorific_suffix, primary_identifier"
        ") VALUES ($1, $2, $3, $4, $5, $6, $7)"
        " ON CONFLICT (person_name_id) DO UPDATE SET"
        "   given_names      = EXCLUDED.given_names,"
        "   family_names     = EXCLUDED.family_names,"
        "   additional_names = EXCLUDED.additional_names,"
        "   honorific_prefix = EXCLUDED.honorific_prefix,"
        "   honorific_suffix = EXCLUDED.honorific_suffix,"
        "   primary_identifier = EXCLUDED.primary_identifier",
        name_id,
        given or None,
        family or None,
        additional or None,
        pre,
        suf,
        pi,
    )

    if not is_htmx(request):
        return RedirectResponse(f"/admin/people/{person_id}/", status_code=303)
    return HTMLResponse(
        content=_summary_oob_fragment(name_id, has_parts=True),
        status_code=200,
        headers=flash_trigger("success", "Structured parts saved."),
    )


@router.post("/{person_id}/names/{name_id}/parts/delete/")
async def name_parts_delete(
    person_id: str,
    name_id: str,
    request: Request,
    user: AdminUser = Depends(get_admin_user),
    db=Depends(get_db),
):
    """Delete the parts row. Idempotent."""
    await _ensure_name_belongs_to_person(db, person_id, name_id)
    await db.execute(
        "DELETE FROM person_name_parts WHERE person_name_id=$1", name_id,
    )
    if not is_htmx(request):
        return RedirectResponse(f"/admin/people/{person_id}/", status_code=303)
    return HTMLResponse(
        content=_summary_oob_fragment(name_id, has_parts=False),
        status_code=200,
        headers=flash_trigger("info", "Structured parts removed."),
    )
