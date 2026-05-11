"""Suggest-only decomposition endpoints for the admin name editor (#139).

Two read-only HTMX endpoints. Neither persists — the Save button (which
routes through ``upsert_or_delete_parts``) remains the sole writer to
``person_name_parts``.

Endpoints:

    GET /admin/people/{person_id}/names/{name_id}/suggest-parts/

        Returns the parts editor partial pre-populated from
        ``suggest_parts``. Optional ``?confirm=1`` bypasses the
        confirm-before-overwrite gate when an existing parts row is
        present.

    GET /admin/people/{person_id}/names/{name_id}/parts-editor/

        Returns the original (un-suggested) parts editor partial for
        the row, populated from any existing ``person_name_parts``
        sidecar. Used by the "Keep current" button in the
        confirm-before-overwrite state to swap the suggestion partial
        back to the editor without disturbing the surrounding row.
"""

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.templating import Jinja2Templates

from src.api.admin.deps import AdminUser, get_admin_user, get_db
from src.core.normalizers.base import is_truthy_like
from src.core.normalizers.person_name import (
    NON_DECOMPOSABLE_TYPES,
    PartsSuggestion,
    suggest_parts,
)

# Mirrors the existing ``Jinja2Templates(directory="src/templates")``
# pattern used elsewhere under ``src.api.admin``. The
# ``inject_array_cap_into_admin_templates`` /
# ``inject_non_decomposable_types_into_admin_templates`` walks pick this
# instance up at startup so ``data-cardstack-cap="{{ ARRAY_CAP }}"`` and
# ``{% if n.name_type not in NON_DECOMPOSABLE_TYPES %}`` resolve from
# the canonical Python sources when this module's templates render.
templates = Jinja2Templates(directory="src/templates")

router = APIRouter(prefix="/people/{person_id}/names", tags=["admin-person-names"])


@router.get("/{name_id}/suggest-parts/")
async def name_suggest_parts(
    person_id: str,
    name_id: str,
    request: Request,
    confirm: str | None = None,
    user: AdminUser = Depends(get_admin_user),
    db=Depends(get_db),
):
    """Return the parts editor partial pre-populated from `suggest_parts`.

    Branches the response into three UX states:

    1. **Confirm-before-overwrite** — when the row already has a parts
       sidecar AND the request did not pass ``confirm=1``. The partial
       carries a small Replace / Keep current form instead of clobbering
       prior operator edits. Replace re-issues the GET with ``confirm=1``.
    2. **Advisory only** — empty/whitespace name, NULL script, or
       ``name_type`` in the non-decomposable set. Suggestion bucket is
       always ``skip``; the partial renders with empty inputs + a small
       reason line so the operator understands why nothing pre-filled.
    3. **Pre-fill** — for ``trivial`` / ``ambiguous`` buckets when no
       existing parts (or the confirm flag is present). Inputs are
       populated from the suggestion; the advisory line surfaces
       confidence + reasons.
    """
    row = await db.fetchrow(
        "SELECT id, name, name_type, locale, script"
        " FROM person_names WHERE id=$1 AND person_id=$2",
        name_id,
        person_id,
    )
    if not row:
        raise HTTPException(status_code=404)

    name = row["name"] or ""
    name_type = row["name_type"]
    locale = row["locale"] or ""
    script = row["script"]

    # Advisory-only short-circuits — never call the suggester for these.
    skip_advisory: str | None = None
    if not name.strip():
        skip_advisory = "Set the visible name first — there's nothing to decompose yet."
    elif script is None:
        skip_advisory = (
            "This row has no script set. Set the script first — a missing "
            "script is itself a data-quality signal worth resolving before "
            "decomposing."
        )
    elif name_type in NON_DECOMPOSABLE_TYPES:
        skip_advisory = (
            f"name_type={name_type!r} is a non-decomposable form. "
            "Structured parts are not meaningful here."
        )

    if skip_advisory is not None:
        suggestion = PartsSuggestion.skip(skip_advisory)
        return templates.TemplateResponse(
            request,
            "admin/people/partials/_name_parts_suggestion.html",
            {
                "person_id": person_id,
                "n": dict(row),
                "parts": None,
                "suggestion": suggestion,
                "advisory": skip_advisory,
                "prefilled": False,
                "needs_confirm": False,
            },
        )

    existing_parts = await db.fetchrow(
        "SELECT given_names, family_names, additional_names,"
        " honorific_prefix, honorific_suffix, primary_identifier"
        " FROM person_name_parts WHERE person_name_id=$1",
        name_id,
    )

    confirm_flag = is_truthy_like(confirm)
    if existing_parts and not confirm_flag:
        return templates.TemplateResponse(
            request,
            "admin/people/partials/_name_parts_suggestion.html",
            {
                "person_id": person_id,
                "n": dict(row),
                "parts": dict(existing_parts),
                "suggestion": None,
                "advisory": None,
                "prefilled": False,
                "needs_confirm": True,
            },
        )

    suggestion = suggest_parts(
        name,
        locale=locale,
        script=script,
        name_type=name_type,
    )

    # Pre-fill only for `confidence='trivial'`. The `ambiguous` bucket
    # signals nameparser couldn't confidently partition the tokens
    # (e.g. three non-initial middle tokens, hyphenated mononym); the
    # issue body explicitly asks for an advisory-only response there
    # so the operator isn't surprised by a half-right pre-fill. `skip`
    # buckets (unsupported script, punctuation-only name, etc.) also
    # render advisory-only.
    prefill = suggestion.confidence == "trivial"
    parts_for_template = (
        {
            "given_names": list(suggestion.given_names),
            "family_names": list(suggestion.family_names),
            "additional_names": list(suggestion.additional_names),
            "honorific_prefix": suggestion.honorific_prefix,
            "honorific_suffix": suggestion.honorific_suffix,
            "primary_identifier": suggestion.primary_identifier,
        }
        if prefill
        else None
    )
    return templates.TemplateResponse(
        request,
        "admin/people/partials/_name_parts_suggestion.html",
        {
            "person_id": person_id,
            "n": dict(row),
            "parts": parts_for_template,
            "suggestion": suggestion,
            "advisory": None,
            "prefilled": prefill,
            "needs_confirm": False,
        },
    )


@router.get("/{name_id}/parts-editor/")
async def name_parts_editor(
    person_id: str,
    name_id: str,
    request: Request,
    user: AdminUser = Depends(get_admin_user),
    db=Depends(get_db),
):
    """Return the un-suggested parts editor partial for one name row.

    Used by the "Keep current" button in the suggestion partial's
    confirm-before-overwrite state. Targeting `#parts-editor-{{ n.id }}`
    (just the parts editor `<details>`) leaves any in-flight edits in
    the surrounding row inputs (visibility / locale / script / sort_as /
    name / canonical / name_type) untouched.
    """
    row = await db.fetchrow(
        "SELECT id, name, name_type, locale, script, sort_as,"
        " visibility, reading_of_id, is_canonical"
        " FROM person_names WHERE id=$1 AND person_id=$2",
        name_id,
        person_id,
    )
    if not row:
        raise HTTPException(status_code=404)
    existing_parts = await db.fetchrow(
        "SELECT given_names, family_names, additional_names,"
        " honorific_prefix, honorific_suffix, primary_identifier"
        " FROM person_name_parts WHERE person_name_id=$1",
        name_id,
    )
    return templates.TemplateResponse(
        request,
        "admin/people/partials/_name_parts_editor.html",
        {
            "person_id": person_id,
            "n": dict(row),
            "parts": dict(existing_parts) if existing_parts else None,
        },
    )
