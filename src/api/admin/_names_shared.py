"""Shared factory for entity names CRUD routers (orgs and people)."""

from collections.abc import Awaitable, Callable
from datetime import date

import asyncpg
from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from markupsafe import escape

from src.api.admin._citations_shared import citation_count_lateral
from src.api.admin.deps import (
    AdminUser,
    build_parts_summary,
    flash_trigger,
    get_admin_user,
    get_db,
    is_htmx,
    with_flash,
)
from src.api.admin.people_name_parts import upsert_or_delete_parts
from src.core.ancillary_migrate import delete_citations
from src.core.db import generate_id
from src.core.observation import NEVER_CANONICAL_NAME_TYPES
from src.core.types import OrgNameType, PersonNameType, PersonNameVisibility

# Either-side `name_type` value. The factory is shared between people
# (PersonNameType) and orgs (OrgNameType); this union is the strongest
# static type the helpers can carry. Runtime narrowing happens against
# the per-router ``name_types`` tuple at handler entry.
NameType = PersonNameType | OrgNameType


class _PartsValidationError(Exception):
    """Internal signal that parts validation failed; transaction rolls back.

    Raised inside the `async with db.transaction():` block of the name
    create / edit handlers when ``upsert_or_delete_parts`` returns an
    error message. Asyncpg's transaction context manager rolls back on
    raise, so both the name write and any partial parts write are
    undone before the catch block runs (issue #127).
    """


def _normalise_optional_str(value: str | None) -> str | None:
    """Strip whitespace and return None for empty/whitespace-only values.

    Used for ``sort_as``, ``locale``, ``script`` Form inputs so that an
    empty submission becomes a NULL column rather than ''.
    """
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


# Sentinel-ish: raised to signal a malformed effective-date Form value so the
# handler can surface a 422 / flash instead of bubbling a raw ValueError 500.
class _DateParseError(Exception):
    """A non-empty effective-date Form field was not a valid ISO date."""


def _parse_optional_date(value: str | None) -> date | None:
    """Parse an optional ``<input type=date>`` Form value to a ``date``.

    Empty / whitespace-only → None (clears the column). A malformed
    non-empty value raises ``_DateParseError`` (browsers send YYYY-MM-DD,
    so this only guards raw/scripted POSTs).
    """
    if value is None:
        return None
    stripped = value.strip()
    if not stripped:
        return None
    try:
        return date.fromisoformat(stripped)
    except ValueError as exc:
        raise _DateParseError(str(exc)) from exc


def _form_error_response(
    error: str,
    request: Request,
    *,
    from_exc: BaseException | None = None,
) -> HTMLResponse:
    """Render a form-validation error.

    HTMX clients get 200 + HX-Trigger flash (page stays put, surfaces
    the message). Non-HTMX clients get JSON 422.

    Use as ``return _form_error_response(...)`` at every call site: in
    the HTMX branch the function returns an HTMLResponse with the flash
    trigger; in the non-HTMX branch it raises ``HTTPException(422)`` so
    control never reaches the caller's ``return``. The static ``return``
    keeps the type-checker happy and documents intent.

    ``from_exc`` is forwarded to ``raise … from`` for traceback chaining
    when the error originated from a caught exception (e.g. asyncpg
    constraint violation). Defaults to ``None`` for plain validation.
    """
    if not is_htmx(request):
        if from_exc is not None:
            raise HTTPException(status_code=422, detail=error) from from_exc
        raise HTTPException(status_code=422, detail=error)
    return HTMLResponse(
        content="",
        status_code=200,
        headers=flash_trigger("warning", escape(error)),
    )


def _fk_violation_message(exc: asyncpg.ForeignKeyViolationError) -> str:
    """Human-friendly form error for FK violations on locale/script/reading_of_id."""
    detail = (exc.detail or "").lower()
    cname = exc.constraint_name or ""
    if "bcp47_locales" in detail or "_locale_fkey" in cname:
        return "Locale is not a registered BCP 47 code. Pick from the typeahead suggestions."
    if "iso15924_scripts" in detail or "_script_fkey" in cname:
        return "Script is not a registered ISO 15924 code. Pick from the typeahead suggestions."
    if "reading_of_id" in detail or "reading_of_id" in cname:
        return "Reading-of target is not a valid name row. Pick from the typeahead suggestions."
    return "Foreign key constraint violation. Check locale/script/reading_of values."


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
    name_types: tuple[NameType, ...],
    detail_url: Callable[[str], str],
    maybe_promote_sole_name: Callable[[str, object], Awaitable[None]],
    last_identity_blocked: Callable[[str, object], Awaitable[bool]],
    last_identity_error_msg: str,
    last_identity_409_msg: str,
    header_extra: Callable[[str, object], Awaitable[dict]],
    supports_person_metadata: bool = False,
    supports_effective_dates: bool = False,
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
    name_types:
        Tuple of valid ``name_type`` values for this entity (from
        ``src.core.types``). People-side passes ``PERSON_NAME_TYPES``;
        orgs-side passes ``ORG_NAME_TYPES``. Threaded through ``_ctx``
        so the form-row template iterates a single source of truth, and
        validated at handler entry by ``_validate_name_type`` (returns
        422 / flash on unknown values, ahead of the DB CHECK).
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
    supports_person_metadata:
        Person-specific behavior gate (#123 Phase 2a–2d). When True the
        router accepts the visibility / locale / script / sort_as /
        reading_of_id Form fields, runs the reading-of validator, joins
        ``person_name_parts`` in the post-mutation re-render, and
        pre-populates the structured-parts editor. False (default) for
        ``organization_names``, which has none of the referenced
        columns or sidecar table. See ``docs/STYLE.md`` §"Person-name
        metadata controls" for the full enumeration. Named
        person-specifically (not ``supports_metadata``) because the
        True branch hard-codes person schema; a third entity type with
        metadata would need a richer abstraction, not a second caller.
    supports_effective_dates:
        Org-specific gate (#239). When True the router accepts the
        ``effective_start`` / ``effective_end`` Form fields and writes them
        to ``organization_names`` (form-as-source-of-truth, empty → NULL).
        False (default) for ``person_names``, which has no effective-date
        columns. Independent of ``supports_person_metadata`` so the two
        entity types stay decoupled.
    """
    router = APIRouter(prefix=prefix, tags=tags)

    # Single source of truth for the optional metadata column ordering
    # used by both _insert_name and _update_name. Order matters: it
    # determines the column / placeholder order in the generated SQL.
    def _metadata_pairs(
        vis: PersonNameVisibility | None,
        locale: str | None,
        script: str | None,
        sort_as: str | None,
        reading_of_id: str | None,
    ) -> tuple[tuple[str, object | None], ...]:
        return (
            ("visibility", vis),
            ("locale", locale),
            ("script", script),
            ("sort_as", sort_as),
            ("reading_of_id", reading_of_id),
        )

    def _validate_name_type(value: str) -> str | None:
        """Return an error message if *value* is not in the configured
        ``name_types`` tuple, else None.

        Defense in depth above the DB CHECK constraint: a typo in the
        client (or a stale cached HTML form) returns a friendly 422 /
        flash instead of bubbling a raw ``CheckViolationError``. The
        error message intentionally does NOT enumerate allowed values
        — that list lives in the dropdown the user just submitted, and
        embedding it here would drift as the schema evolves.
        """
        if value not in name_types:
            return f"Invalid name_type {value!r}. Choose a value from the dropdown."
        return None

    def _validate_canonical_visibility(
        is_canonical: str, vis: str | None, name_type: str
    ) -> str | None:
        """Reject "canonical *and* not displayable" before the DB does (#308).

        `is_canonical` is the display pointer and `v_person_display_names`
        filters to `visibility='public'`, so a canonical `legal_only`/`hidden`
        row would render the person blank while occupying their only display
        slot. `chk_person_canonical_is_public` forbids it; catching it here turns
        a raw CheckViolationError 500 into a friendly 422 / flash that names the
        fields the user needs to change.

        Two things must be checked, not one (CR4 #28/#31):

        `name_type` — the submitted visibility is not necessarily the visibility
        that lands. `trg_deadname_visibility` rewrites a deadname row to
        legal_only BEFORE INSERT/UPDATE, so `deadname` + `public` passes a
        value-only check and then violates the CHECK. `deadname` is in the
        person name_type dropdown and visibility defaults to public, so this was
        reachable from the ordinary admin form.

        `vis` — callers must pass the *effective* visibility, i.e. the stored
        value when the form omits the field, because `_update_name` leaves the
        column untouched in that case rather than resetting it to the default.

        Orgs pass `vis=None` and never use these name_types, so this stays a
        no-op for them.
        """
        if is_canonical != "true":
            return None
        if name_type in NEVER_CANONICAL_NAME_TYPES:
            return (
                f"A {name_type} cannot be the canonical name — it is recorded"
                f" privately and is never displayed. Uncheck canonical, or choose"
                f" a different name type."
            )
        if vis is not None and vis != "public":
            return (
                f"A canonical name must be public — it is the name shown for this"
                f" record. Set visibility to 'public', or uncheck canonical to keep"
                f" this name {vis!r}."
            )
        return None

    async def _insert_name(
        db,
        *,
        nid: str,
        entity_id: str,
        name: str,
        name_type: NameType,
        is_canonical: bool,
        vis: PersonNameVisibility | None,
        locale: str | None = None,
        script: str | None = None,
        sort_as: str | None = None,
        reading_of_id: str | None = None,
        effective_start: date | None = None,
        effective_end: date | None = None,
    ) -> None:
        """Insert a name row. Optional metadata columns are included only
        when non-None so the DB default / triggers handle the omitted case.
        Effective dates (org-only) are written verbatim when the router
        supports them — NULL is a valid, intended value.
        """
        cols = ["id", entity_fk, "name", "name_type", "is_canonical"]
        vals: list[object] = [nid, entity_id, name, name_type, is_canonical]
        if supports_effective_dates:
            cols += ["effective_start", "effective_end"]
            vals += [effective_start, effective_end]
        for col, val in _metadata_pairs(vis, locale, script, sort_as, reading_of_id):
            if val is None:
                continue
            cols.append(col)
            vals.append(val)
        placeholders = ", ".join(f"${i + 1}" for i in range(len(vals)))
        await db.execute(
            f"INSERT INTO {names_table} ({', '.join(cols)}) VALUES ({placeholders})",
            *vals,
        )

    async def _update_name(
        db,
        *,
        name_id: str,
        name: str,
        name_type: NameType,
        is_canonical: bool,
        vis: PersonNameVisibility | None,
        locale: str | None = None,
        script: str | None = None,
        sort_as: str | None = None,
        reading_of_id: str | None = None,
        write_metadata: bool = False,
        effective_start: date | None = None,
        effective_end: date | None = None,
    ) -> None:
        """Update a name row.

        When ``write_metadata`` is True, all metadata columns are SET to
        the supplied values — including NULL — so the form is treated as
        the source of truth. Visibility is the one exception: a None
        value is skipped so the DB default ('public') + deadname trigger
        keep authority over visibility.

        Org-side calls (write_metadata=False) leave the metadata columns
        untouched, preserving the legacy schema where they don't exist.

        Effective dates (org-only, gated by ``supports_effective_dates``)
        are SET unconditionally — including NULL — so the form clears a
        previously-set date.
        """
        sets = ["name=$1", "name_type=$2", "is_canonical=$3"]
        vals: list[object] = [name, name_type, is_canonical]
        if write_metadata:
            for col, val in _metadata_pairs(
                vis,
                locale,
                script,
                sort_as,
                reading_of_id,
            ):
                if col == "visibility" and val is None:
                    continue
                vals.append(val)
                sets.append(f"{col}=${len(vals)}")
        if supports_effective_dates:
            vals.append(effective_start)
            sets.append(f"effective_start=${len(vals)}")
            vals.append(effective_end)
            sets.append(f"effective_end=${len(vals)}")
        vals.append(name_id)
        await db.execute(
            f"UPDATE {names_table} SET {', '.join(sets)} WHERE id=${len(vals)}",
            *vals,
        )

    async def _validate_reading_of_target(
        db,
        *,
        entity_id: str,
        reading_of_id: str | None,
        name_id: str | None = None,
    ) -> str | None:
        """Return the reason this `reading_of_id` is invalid, or None.

        Validates four conditions the typeahead enforces but a raw POST
        could bypass:
          1. target row exists (DB FK catches absence too, but this
             produces a friendlier message);
          2. target is on the SAME person (`{entity_fk}` match);
          3. target is NOT the editing row itself (self-reference) —
             only checked when `name_id` is supplied (edit path);
          4. target's `name_type` is OUTSIDE the reading set
             (visual-target-only — rejects chains like A→B→C).

        Surfaces as a form error string; the caller turns it into an
        HTMX flash or non-HTMX 422.
        """
        if reading_of_id is None:
            return None
        if name_id is not None and reading_of_id == name_id:
            return "Reading-of cannot point at itself. Pick a different visual row."
        target = await db.fetchrow(
            f"SELECT {entity_fk}, name_type FROM {names_table} WHERE id = $1",
            reading_of_id,
        )
        if target is None:
            return "Reading-of target row does not exist. Pick from the typeahead suggestions."
        if target[entity_fk] != entity_id:
            return (
                "Reading-of target must be on the same person. Pick from the typeahead suggestions."
            )
        if target["name_type"] in ("reading", "romanization", "mrz"):
            return (
                "Reading-of target must be a visual row (not another reading/romanization/mrz row)."
            )
        return None

    # ---- helpers ----------------------------------------------------------------

    async def _fetch_names_for_rows(db, entity_id: str) -> list[dict]:
        """Fetch all names for `entity_id` and (for person_names) attach
        Phase 2c/2d enrichment so the read-row template can render the
        linked-name subtitle, cascade-aware delete confirm, and parts
        summary after a mutation re-renders the tbody.

        Org-side calls fall through to a plain SELECT-as-dict so they
        don't blow up on a missing person_name_parts join. Always
        returns a list of dicts (never asyncpg Records) so callers and
        templates see a uniform mapping shape.
        """
        if not supports_person_metadata:
            rows = await db.fetch(
                f"SELECT * FROM {names_table} WHERE {entity_fk}=$1"
                " ORDER BY is_canonical DESC, name_type, name",
                entity_id,
            )
            return [dict(r) for r in rows]
        rows = await db.fetch(
            f"SELECT pn.*,"
            "  parent.name AS reading_of_name,"
            "  COALESCE(c.cnt, 0) AS reading_child_count,"
            "  cc_j.citation_count,"
            "  pnp.given_names      AS pnp_given_names,"
            "  pnp.family_names     AS pnp_family_names,"
            "  pnp.additional_names AS pnp_additional_names"
            f" FROM {names_table} pn"
            f" LEFT JOIN {names_table} parent ON parent.id = pn.reading_of_id"
            " LEFT JOIN person_name_parts pnp ON pnp.person_name_id = pn.id"
            " LEFT JOIN LATERAL ("
            f"   SELECT COUNT(*) AS cnt FROM {names_table} ch"
            "   WHERE ch.reading_of_id = pn.id"
            " ) c ON TRUE"
            f"{citation_count_lateral('person_name', 'pn.id')}"
            f" WHERE pn.{entity_fk}=$1"
            " ORDER BY pn.is_canonical DESC, pn.name_type, pn.name",
            entity_id,
        )
        return [
            {
                **dict(r),
                "parts_summary": build_parts_summary(
                    r["pnp_family_names"],
                    r["pnp_given_names"],
                    r["pnp_additional_names"],
                ),
            }
            for r in rows
        ]

    async def _get_entity_or_404(entity_id: str, db):
        row = await db.fetchrow(f"SELECT id FROM {entity_table} WHERE id=$1", entity_id)
        if not row:
            raise HTTPException(status_code=404, detail=entity_not_found_msg)
        return row

    def _ctx(entity_id: str, **extra) -> dict:
        """Build template context with the correct entity-id key."""
        return {entity_id_key: entity_id, "name_types": name_types, **extra}

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
        visibility: PersonNameVisibility | None = Form(None),
        locale: str | None = Form(None),
        script: str | None = Form(None),
        sort_as: str | None = Form(None),
        reading_of_id: str | None = Form(None),
        # Effective dates — only consumed when supports_effective_dates=True (#239, org).
        effective_start: str | None = Form(None),
        effective_end: str | None = Form(None),
        # Parts fields — only consumed when supports_person_metadata=True (#127).
        # Accepted on create for handler symmetry with edit-row; the current
        # admin UI only renders the parts editor for existing rows (parts
        # editor template gates on `{% if n %}`), so this path is exercised
        # by tests and reserved for programmatic / future-UI callers.
        given_names: list[str] = Form([]),
        family_names: list[str] = Form([]),
        additional_names: list[str] = Form([]),
        honorific_prefix: str | None = Form(None),
        honorific_suffix: str | None = Form(None),
        primary_identifier: str | None = Form(None),
        user: AdminUser = Depends(get_admin_user),
        db=Depends(get_db),
    ):
        """Create a new name."""
        # Pydantic Literal validates visibility value range; gate drops all
        # metadata fields for org_names (supports_person_metadata=False) which has
        # no locale/script/sort_as/visibility/reading_of_id columns.
        vis = visibility if supports_person_metadata else None
        loc = _normalise_optional_str(locale) if supports_person_metadata else None
        scr = _normalise_optional_str(script) if supports_person_metadata else None
        sa = _normalise_optional_str(sort_as) if supports_person_metadata else None
        rof = _normalise_optional_str(reading_of_id) if supports_person_metadata else None
        await _get_entity_or_404(entity_id, db)
        # Body-level validations run after path-level (entity) checks so a
        # 404 wins over a 422 when both apply — matches the convention set
        # by `_validate_reading_of_target` below.
        nt_err = _validate_name_type(name_type)
        if nt_err is not None:
            return _form_error_response(nt_err, request)
        # `_insert_name` omits the column when vis is None, so the row lands on
        # the schema default. Validate against that default explicitly rather
        # than passing None and skipping the visibility branch — otherwise the
        # guarantee is inherited from schema.sql instead of enforced here
        # (CR5 #49). Orgs keep vis=None: organization_names has no such column.
        effective_vis = vis if not supports_person_metadata else (vis or "public")
        cv_err = _validate_canonical_visibility(is_canonical, effective_vis, name_type)
        if cv_err is not None:
            return _form_error_response(cv_err, request)
        if rof is not None:
            err = await _validate_reading_of_target(
                db,
                entity_id=entity_id,
                reading_of_id=rof,
            )
            if err is not None:
                return _form_error_response(err, request)
        try:
            es = _parse_optional_date(effective_start) if supports_effective_dates else None
            ee = _parse_optional_date(effective_end) if supports_effective_dates else None
        except _DateParseError as exc:
            return _form_error_response(
                "Effective dates must be valid calendar dates (YYYY-MM-DD).",
                request,
                from_exc=exc,
            )
        nid = generate_id()
        try:
            async with db.transaction():
                if is_canonical == "true":
                    await db.execute(
                        f"UPDATE {names_table} SET is_canonical=FALSE"
                        f" WHERE {entity_fk}=$1 AND is_canonical=TRUE",
                        entity_id,
                    )
                await _insert_name(
                    db,
                    nid=nid,
                    entity_id=entity_id,
                    name=name.strip(),
                    name_type=name_type,
                    is_canonical=(is_canonical == "true"),
                    vis=vis,
                    locale=loc,
                    script=scr,
                    sort_as=sa,
                    reading_of_id=rof,
                    effective_start=es,
                    effective_end=ee,
                )
                # Skip the parts helper entirely on create when no parts
                # fields were submitted: the just-inserted name has no
                # parts row, so the helper's all-empty DELETE branch
                # would issue a guaranteed-zero-row write. Cap validation
                # still runs when any parts field IS submitted.
                if supports_person_metadata and (
                    given_names
                    or family_names
                    or additional_names
                    or honorific_prefix
                    or honorific_suffix
                    or primary_identifier
                ):
                    parts_err = await upsert_or_delete_parts(
                        db,
                        name_id=nid,
                        given_names=given_names,
                        family_names=family_names,
                        additional_names=additional_names,
                        honorific_prefix=honorific_prefix,
                        honorific_suffix=honorific_suffix,
                        primary_identifier=primary_identifier,
                    )
                    if parts_err is not None:
                        # Transaction context manager rolls back on raise.
                        raise _PartsValidationError(parts_err)
        except asyncpg.ForeignKeyViolationError as exc:
            return _form_error_response(
                _fk_violation_message(exc),
                request,
                from_exc=exc,
            )
        except asyncpg.CheckViolationError as exc:
            if exc.constraint_name == "chk_org_name_effective_date_order":
                return _form_error_response(
                    "Effective start must be on or before effective end.",
                    request,
                    from_exc=exc,
                )
            if exc.constraint_name == "chk_person_canonical_is_public":
                # Backstop for #308: `_validate_canonical_visibility` should have
                # caught this, but a trigger can still rewrite visibility after
                # validation. Degrade to a flash rather than a 500 (CR4 #28).
                return _form_error_response(
                    "A canonical name must be public — it is the name shown for"
                    " this record. Uncheck canonical, or set visibility to 'public'.",
                    request,
                    from_exc=exc,
                )
            raise
        except _PartsValidationError as exc:
            # Transaction already rolled back by the `async with` exit on raise —
            # both the name insert and any partial parts write are undone.
            return _form_error_response(str(exc), request, from_exc=exc)
        if not is_htmx(request):
            return RedirectResponse(with_flash(detail_url(entity_id), "saved"), status_code=303)
        names = await _fetch_names_for_rows(db, entity_id)
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
        # Attach parts_summary so the cancel-from-edit transition keeps
        # the subtitle (parity with the post-mutation tbody re-render), and
        # citation_count so the Cite button keeps its #341 count. Single-row
        # render — one extra scalar query, not an N+1.
        n_ctx: object = name_row
        if supports_person_metadata:
            parts_row = await db.fetchrow(
                "SELECT given_names, family_names, additional_names"
                " FROM person_name_parts WHERE person_name_id=$1",
                name_id,
            )
            citation_count = await db.fetchval(
                "SELECT count(*) FROM citations"
                " WHERE entity_type='person_name' AND entity_id=$1 AND archived_at IS NULL",
                name_id,
            )
            n_ctx = {
                **dict(name_row),
                "citation_count": citation_count,
                "parts_summary": build_parts_summary(
                    parts_row["family_names"] if parts_row else None,
                    parts_row["given_names"] if parts_row else None,
                    parts_row["additional_names"] if parts_row else None,
                ),
            }
        return templates.TemplateResponse(request, tmpl_read_row, _ctx(entity_id, n=n_ctx))

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
        # Phase 2d: pre-populate the structured-parts editor when an
        # existing parts row is present. Only person_names has a parts
        # sidecar — guarded by supports_person_metadata so org_names paths
        # never run the extra query.
        parts = None
        if supports_person_metadata:
            parts = await db.fetchrow(
                "SELECT given_names, family_names, additional_names,"
                " honorific_prefix, honorific_suffix, primary_identifier"
                " FROM person_name_parts WHERE person_name_id=$1",
                name_id,
            )
        return templates.TemplateResponse(
            request,
            tmpl_form_row,
            _ctx(entity_id, n=name_row, parts=parts),
        )

    @router.post("/{name_id}/edit-row/")
    async def name_edit_row_post(
        entity_id: str,
        name_id: str,
        request: Request,
        name: str = Form(...),
        name_type: str = Form("legal"),
        is_canonical: str = Form(""),
        visibility: PersonNameVisibility | None = Form(None),
        locale: str | None = Form(None),
        script: str | None = Form(None),
        sort_as: str | None = Form(None),
        reading_of_id: str | None = Form(None),
        # Effective dates — only consumed when supports_effective_dates=True (#239, org).
        effective_start: str | None = Form(None),
        effective_end: str | None = Form(None),
        # Parts fields — only consumed when supports_person_metadata=True (#127).
        given_names: list[str] = Form([]),
        family_names: list[str] = Form([]),
        additional_names: list[str] = Form([]),
        honorific_prefix: str | None = Form(None),
        honorific_suffix: str | None = Form(None),
        primary_identifier: str | None = Form(None),
        user: AdminUser = Depends(get_admin_user),
        db=Depends(get_db),
    ):
        """Update a name."""
        # Pydantic Literal validates visibility range; gate drops all metadata
        # for org_names. With write_metadata=True (person path) the form is
        # treated as the source of truth — empty inputs become NULL columns.
        vis = visibility if supports_person_metadata else None
        loc = _normalise_optional_str(locale) if supports_person_metadata else None
        scr = _normalise_optional_str(script) if supports_person_metadata else None
        sa = _normalise_optional_str(sort_as) if supports_person_metadata else None
        rof = _normalise_optional_str(reading_of_id) if supports_person_metadata else None
        existing = await db.fetchrow(
            f"SELECT * FROM {names_table} WHERE id=$1 AND {entity_fk}=$2",
            name_id,
            entity_id,
        )
        if not existing:
            raise HTTPException(status_code=404)
        # Body-level validations run after path-level (existence) checks so
        # a 404 wins over a 422 when both apply — matches the convention
        # set by `_validate_reading_of_target` below.
        nt_err = _validate_name_type(name_type)
        if nt_err is not None:
            return _form_error_response(nt_err, request)
        # `_update_name` skips a None visibility, so the row keeps its stored
        # value — validate against that, not against the absent submission.
        effective_vis = vis
        if effective_vis is None and supports_person_metadata:
            effective_vis = existing["visibility"]
        cv_err = _validate_canonical_visibility(is_canonical, effective_vis, name_type)
        if cv_err is not None:
            return _form_error_response(cv_err, request)
        if rof is not None:
            err = await _validate_reading_of_target(
                db,
                entity_id=entity_id,
                reading_of_id=rof,
                name_id=name_id,
            )
            if err is not None:
                return _form_error_response(err, request)
        try:
            es = _parse_optional_date(effective_start) if supports_effective_dates else None
            ee = _parse_optional_date(effective_end) if supports_effective_dates else None
        except _DateParseError as exc:
            return _form_error_response(
                "Effective dates must be valid calendar dates (YYYY-MM-DD).",
                request,
                from_exc=exc,
            )
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
                    return RedirectResponse(
                        with_flash(detail_url(entity_id), "exists"), status_code=303
                    )
                return HTMLResponse(
                    content="",
                    status_code=200,
                    headers=flash_trigger(
                        "warning",
                        "Cannot remove canonical. Promote another name first.",
                    ),
                )
        try:
            async with db.transaction():
                if is_canonical == "true":
                    await db.execute(
                        f"UPDATE {names_table} SET is_canonical=FALSE"
                        f" WHERE {entity_fk}=$1 AND is_canonical=TRUE AND id != $2",
                        entity_id,
                        name_id,
                    )
                await _update_name(
                    db,
                    name_id=name_id,
                    name=name.strip(),
                    name_type=name_type,
                    is_canonical=(is_canonical == "true"),
                    vis=vis,
                    locale=loc,
                    script=scr,
                    sort_as=sa,
                    reading_of_id=rof,
                    write_metadata=supports_person_metadata,
                    effective_start=es,
                    effective_end=ee,
                )
                if supports_person_metadata:
                    parts_err = await upsert_or_delete_parts(
                        db,
                        name_id=name_id,
                        given_names=given_names,
                        family_names=family_names,
                        additional_names=additional_names,
                        honorific_prefix=honorific_prefix,
                        honorific_suffix=honorific_suffix,
                        primary_identifier=primary_identifier,
                    )
                    if parts_err is not None:
                        # Transaction context manager rolls back on raise.
                        raise _PartsValidationError(parts_err)
        except asyncpg.ForeignKeyViolationError as exc:
            return _form_error_response(
                _fk_violation_message(exc),
                request,
                from_exc=exc,
            )
        except asyncpg.CheckViolationError as exc:
            if exc.constraint_name == "chk_org_name_effective_date_order":
                return _form_error_response(
                    "Effective start must be on or before effective end.",
                    request,
                    from_exc=exc,
                )
            if exc.constraint_name == "chk_person_canonical_is_public":
                # Backstop for #308: `_validate_canonical_visibility` should have
                # caught this, but a trigger can still rewrite visibility after
                # validation. Degrade to a flash rather than a 500 (CR4 #28).
                return _form_error_response(
                    "A canonical name must be public — it is the name shown for"
                    " this record. Uncheck canonical, or set visibility to 'public'.",
                    request,
                    from_exc=exc,
                )
            raise
        except _PartsValidationError as exc:
            # Transaction already rolled back by the `async with` exit on raise —
            # both the name update and any partial parts write are undone.
            return _form_error_response(str(exc), request, from_exc=exc)
        if not is_htmx(request):
            return RedirectResponse(with_flash(detail_url(entity_id), "saved"), status_code=303)
        names = await _fetch_names_for_rows(db, entity_id)
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
                    headers=flash_trigger("warning", last_identity_error_msg),
                )
            # person_name is citable (#319) with no FK; drop its citations first so
            # they don't orphan. organization_names is not citable — skip.
            if names_table == "person_names":
                await delete_citations(db, "person_name", name_id)
            await db.execute(f"DELETE FROM {names_table} WHERE id=$1", name_id)
            await maybe_promote_sole_name(entity_id, db)
        if not is_htmx(request):
            return RedirectResponse(with_flash(detail_url(entity_id), "removed"), status_code=303)
        names = await _fetch_names_for_rows(db, entity_id)
        return templates.TemplateResponse(
            request,
            tmpl_rows,
            _ctx(entity_id, names=names),
            headers=flash_trigger(
                "success", "Name removed.", extra=await header_extra(entity_id, db)
            ),
        )

    return router
