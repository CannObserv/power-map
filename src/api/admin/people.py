"""Admin views for people."""

from datetime import UTC, datetime

import asyncpg
from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from markupsafe import escape

from src.api.admin.deps import AdminUser, check_auth, flash_trigger, get_admin_user, get_db, is_htmx
from src.api.admin.org_dups import get_org_dup_count
from src.api.admin.pagination import pagination_context
from src.api.admin.people_dups import (
    CANDIDATE_WHERE,
    get_person_dup_count,
)
from src.api.admin.people_dups import (
    invalidate_dup_count_cache as invalidate_person_dup_count_cache,
)
from src.core.db import generate_id

templates = Jinja2Templates(directory="src/templates")
router = APIRouter(prefix="/people", tags=["admin-people"])


@router.get("/")
async def people_list(
    request: Request,
    q: str = "",
    status: str = "active",
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=10, le=500),
    user: AdminUser | RedirectResponse = Depends(get_admin_user),
    db=Depends(get_db),
    org_dup_count: int = Depends(get_org_dup_count),
    person_dup_count: int = Depends(get_person_dup_count),
):
    """List people with search and status filter."""
    redirect, user = check_auth(user)
    if redirect:
        return redirect

    conditions = []
    params: list = []

    if status == "active":
        conditions.append("p.archived_at IS NULL")
    elif status == "archived":
        conditions.append("p.archived_at IS NOT NULL")

    if q:
        params.append(f"%{q}%")
        conditions.append(f"n.name ILIKE ${len(params)}")

    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
    count_params = params[:]

    count = await db.fetchval(
        f"""SELECT count(DISTINCT p.id)
            FROM people p
            LEFT JOIN person_names n
              ON n.person_id = p.id AND n.is_canonical = TRUE
            {where}""",
        *count_params,
    )

    pctx = pagination_context(page, count, page_size)
    offset = (pctx["page"] - 1) * page_size
    list_params = params + [page_size, offset]

    rows = await db.fetch(
        f"""SELECT p.id, p.archived_at, p.created_at,
                   n.name AS canonical_name
            FROM people p
            LEFT JOIN person_names n
              ON n.person_id = p.id AND n.is_canonical = TRUE
            {where}
            ORDER BY n.name NULLS LAST
            LIMIT ${len(list_params) - 1} OFFSET ${len(list_params)}""",
        *list_params,
    )

    ctx = {
        "user": user,
        "active_section": "people",
        "people": rows,
        "q": q,
        "status": status,
        "page_size": page_size,
        "total": count,
        "org_dup_count": org_dup_count,
        "person_dup_count": person_dup_count,
        **pctx,
    }
    template = (
        "admin/people/_region.html"
        if request.headers.get("HX-Request") and not request.headers.get("HX-Boosted")
        else "admin/people/list.html"
    )
    return templates.TemplateResponse(request, template, ctx)


@router.get("/new/")
async def person_new_form(
    request: Request,
    user: AdminUser | RedirectResponse = Depends(get_admin_user),
    db=Depends(get_db),
    org_dup_count: int = Depends(get_org_dup_count),
    person_dup_count: int = Depends(get_person_dup_count),
):
    """New person form."""
    redirect, user = check_auth(user)
    if redirect:
        return redirect
    return templates.TemplateResponse(
        request,
        "admin/people/form.html",
        {
            "user": user,
            "active_section": "people",
            "person": None,
            "canonical_name": "",
            "org_dup_count": org_dup_count,
            "person_dup_count": person_dup_count,
        },
    )


@router.post("/new/")
async def person_create(
    request: Request,
    name: str = Form(...),
    personal_pronouns: str = Form(""),
    notes: str = Form(""),
    user: AdminUser | RedirectResponse = Depends(get_admin_user),
    db=Depends(get_db),
):
    """Create a new person."""
    redirect, user = check_auth(user)
    if redirect:
        return redirect
    person_id = generate_id()
    await db.execute(
        "INSERT INTO people (id, personal_pronouns, notes) VALUES ($1, $2, $3)",
        person_id, personal_pronouns or None, notes or None,
    )
    await db.execute(
        "INSERT INTO person_names"
        " (id, person_id, name, is_canonical) VALUES ($1, $2, $3, TRUE)",
        generate_id(), person_id, name,
    )
    return RedirectResponse(f"/admin/people/{person_id}/", status_code=303)


async def _fetch_duplicate_pairs(db) -> list:
    """Return near-duplicate person pairs; empty list if pg_trgm not installed."""
    try:
        return await db.fetch(
            f"""SELECT
                a.id AS a_id, dn_a.display_name AS a_name, a.created_at AS a_created,
                b.id AS b_id, dn_b.display_name AS b_name, b.created_at AS b_created,
                similarity(dn_a.display_name, dn_b.display_name) AS score,
                (SELECT count(*) FROM role_assignments
                 WHERE person_id = a.id AND archived_at IS NULL) AS a_roles,
                (SELECT count(*) FROM role_assignments
                 WHERE person_id = b.id AND archived_at IS NULL) AS b_roles
            {CANDIDATE_WHERE}
            ORDER BY score DESC"""
        )
    except asyncpg.exceptions.UndefinedFunctionError:
        return []


@router.get("/duplicates/")
async def people_duplicates(
    request: Request,
    user: AdminUser | RedirectResponse = Depends(get_admin_user),
    db=Depends(get_db),
    org_dup_count: int = Depends(get_org_dup_count),
    person_dup_count: int = Depends(get_person_dup_count),
):
    """List near-duplicate person pairs for review."""
    redirect, user = check_auth(user)
    if redirect:
        return redirect
    pairs = await _fetch_duplicate_pairs(db)
    ctx = {
        "user": user,
        "active_section": "people_duplicates",
        "pairs": pairs,
        "org_dup_count": org_dup_count,
        "person_dup_count": person_dup_count,
    }
    return templates.TemplateResponse(
        request,
        "admin/people/_duplicates_region.html"
        if is_htmx(request)
        else "admin/people/duplicates.html",
        ctx,
    )


@router.get("/{person_id}/")
async def person_detail(
    person_id: str,
    request: Request,
    user: AdminUser | RedirectResponse = Depends(get_admin_user),
    db=Depends(get_db),
    org_dup_count: int = Depends(get_org_dup_count),
    person_dup_count: int = Depends(get_person_dup_count),
):
    """Person detail view."""
    redirect, user = check_auth(user)
    if redirect:
        return redirect

    person = await db.fetchrow("SELECT * FROM people WHERE id = $1", person_id)
    if not person:
        raise HTTPException(status_code=404, detail="Person not found")

    names = await db.fetch(
        "SELECT * FROM person_names WHERE person_id = $1 ORDER BY is_canonical DESC",
        person_id,
    )
    contacts = await db.fetch(
        "SELECT * FROM contact_methods WHERE entity_type = 'person' AND entity_id = $1",
        person_id,
    )
    addresses = await db.fetch(
        """SELECT ea.*, a.standardized, a.address_line_1, a.city, a.region, a.postal_code
           FROM entity_addresses ea JOIN addresses a ON a.id = ea.address_id
           WHERE ea.entity_type = 'person' AND ea.entity_id = $1""",
        person_id,
    )
    urls = await db.fetch(
        """SELECT l.*, lt.display_name AS url_type_name, lt.is_social
           FROM links l JOIN link_types lt ON lt.id = l.link_type_id
           WHERE l.entity_type = 'person' AND l.entity_id = $1""",
        person_id,
    )
    social = [r for r in urls if r["is_social"]]
    identifiers = await db.fetch(
        """SELECT i.*, eit.display_name AS type_name, eit.full_name AS type_full_name
           FROM identifiers i
           JOIN entity_identifier_types eit ON eit.id = i.entity_identifier_type_id
           WHERE i.entity_id = $1""",
        person_id,
    )
    role_assignments = await db.fetch(
        """SELECT ra.id, ra.is_current, ra.start_date, ra.end_date, ra.archived_at,
                  r.title, o.id AS org_id, dn.display_name AS org_name
           FROM role_assignments ra
           JOIN roles r ON r.id = ra.role_id
           JOIN organizations o ON o.id = r.organization_id
           LEFT JOIN v_org_display_names dn ON dn.organization_id = o.id
           WHERE ra.person_id = $1
           ORDER BY ra.is_current DESC, ra.start_date DESC NULLS LAST""",
        person_id,
    )

    return templates.TemplateResponse(
        request,
        "admin/people/detail.html",
        {
            "user": user,
            "active_section": "people",
            "person": person,
            "names": names,
            "contacts": contacts,
            "addresses": addresses,
            "urls": urls,
            "social": social,
            "identifiers": identifiers,
            "role_assignments": role_assignments,
            "org_dup_count": org_dup_count,
            "person_dup_count": person_dup_count,
        },
    )


@router.get("/{person_id}/edit/")
async def person_edit_form(
    person_id: str,
    request: Request,
    user: AdminUser | RedirectResponse = Depends(get_admin_user),
    db=Depends(get_db),
    org_dup_count: int = Depends(get_org_dup_count),
    person_dup_count: int = Depends(get_person_dup_count),
):
    """Edit person form."""
    redirect, user = check_auth(user)
    if redirect:
        return redirect
    person = await db.fetchrow("SELECT * FROM people WHERE id = $1", person_id)
    if not person:
        raise HTTPException(status_code=404, detail="Person not found")
    canonical = await db.fetchrow(
        "SELECT name FROM person_names WHERE person_id = $1 AND is_canonical = TRUE",
        person_id,
    )
    return templates.TemplateResponse(
        request,
        "admin/people/form.html",
        {
            "user": user,
            "active_section": "people",
            "person": person,
            "canonical_name": canonical["name"] if canonical else "",
            "org_dup_count": org_dup_count,
            "person_dup_count": person_dup_count,
        },
    )


@router.post("/{person_id}/edit/")
async def person_update(
    person_id: str,
    request: Request,
    name: str = Form(...),
    personal_pronouns: str = Form(""),
    notes: str = Form(""),
    user: AdminUser | RedirectResponse = Depends(get_admin_user),
    db=Depends(get_db),
):
    """Update a person."""
    redirect, user = check_auth(user)
    if redirect:
        return redirect
    person = await db.fetchrow("SELECT id FROM people WHERE id = $1", person_id)
    if not person:
        raise HTTPException(status_code=404, detail="Person not found")
    await db.execute(
        "UPDATE people SET personal_pronouns = $1, notes = $2 WHERE id = $3",
        personal_pronouns or None, notes or None, person_id,
    )
    existing = await db.fetchrow(
        "SELECT id FROM person_names WHERE person_id = $1 AND is_canonical = TRUE",
        person_id,
    )
    if existing:
        await db.execute(
            "UPDATE person_names SET name = $1 WHERE id = $2", name, existing["id"]
        )
    else:
        await db.execute(
            "INSERT INTO person_names"
            " (id, person_id, name, is_canonical) VALUES ($1, $2, $3, TRUE)",
            generate_id(), person_id, name,
        )
    return RedirectResponse(f"/admin/people/{person_id}/", status_code=303)


@router.post("/{person_id}/archive/")
async def person_archive(
    person_id: str,
    request: Request,
    user: AdminUser | RedirectResponse = Depends(get_admin_user),
    db=Depends(get_db),
):
    """Archive a person (soft delete)."""
    redirect, user = check_auth(user)
    if redirect:
        return redirect
    person = await db.fetchrow("SELECT id FROM people WHERE id = $1", person_id)
    if not person:
        raise HTTPException(status_code=404, detail="Person not found")
    await db.execute("UPDATE people SET archived_at = NOW() WHERE id = $1", person_id)
    return RedirectResponse(f"/admin/people/{person_id}/", status_code=303)


@router.delete("/{person_id}/")
async def person_delete(
    person_id: str,
    request: Request,
    user: AdminUser | RedirectResponse = Depends(get_admin_user),
    db=Depends(get_db),
):
    """Hard delete an archived person."""
    redirect, user = check_auth(user)
    if redirect:
        return redirect
    person = await db.fetchrow("SELECT id, archived_at FROM people WHERE id = $1", person_id)
    if not person:
        raise HTTPException(status_code=404, detail="Person not found")
    if not person["archived_at"]:
        raise HTTPException(status_code=409, detail="Person must be archived before deletion")
    try:
        await db.execute("DELETE FROM person_names WHERE person_id = $1", person_id)
        await db.execute("DELETE FROM people WHERE id = $1", person_id)
    except asyncpg.ForeignKeyViolationError:
        raise HTTPException(
            status_code=409,
            detail="Cannot delete: person has related records (role assignments, etc.)",
        )
    return HTMLResponse(content="", status_code=200)


@router.post("/{winner_id}/merge/{loser_id}/")
async def person_merge(
    winner_id: str,
    loser_id: str,
    request: Request,
    user: AdminUser | RedirectResponse = Depends(get_admin_user),
    db=Depends(get_db),
):
    """Merge loser into winner: reassign all references, hard-delete loser."""
    redirect, user = check_auth(user)
    if redirect:
        return redirect

    winner_name = await db.fetchval(
        "SELECT display_name FROM v_person_display_names WHERE person_id=$1", winner_id
    )
    loser_name = await db.fetchval(
        "SELECT display_name FROM v_person_display_names WHERE person_id=$1", loser_id
    )

    async with db.transaction():
        winner = await db.fetchrow(
            "SELECT id, notes FROM people WHERE id=$1 FOR UPDATE", winner_id
        )
        loser = await db.fetchrow(
            "SELECT id, notes FROM people WHERE id=$1 FOR UPDATE", loser_id
        )
        if not winner or not loser:
            raise HTTPException(status_code=404, detail="Person not found")

        # notes: prefix loser's notes with merge metadata and append to winner
        if loser["notes"]:
            merge_date = datetime.now(UTC).strftime("%Y-%m-%d")
            prefix = f"Merged from {loser_name} on {merge_date} by {user.email}"
            appended = f"{prefix}\n{loser['notes']}"
            new_notes = (
                f"{winner['notes']}\n\n{appended}" if winner["notes"] else appended
            )
            await db.execute(
                "UPDATE people SET notes=$1 WHERE id=$2", new_notes, winner_id
            )

        # person_names: demote loser's canonical to alias, drop exact name duplicates,
        # then reassign remaining loser names to winner
        await db.execute(
            "UPDATE person_names SET is_canonical=FALSE"
            " WHERE person_id=$1 AND is_canonical=TRUE",
            loser_id,
        )
        await db.execute(
            "DELETE FROM person_names"
            " WHERE person_id=$1"
            "   AND name IN (SELECT name FROM person_names WHERE person_id=$2)",
            loser_id, winner_id,
        )
        await db.execute(
            "UPDATE person_names SET person_id=$1 WHERE person_id=$2",
            winner_id, loser_id,
        )

        # role_assignments: delete conflicts (same role+start_date on both), then reassign
        await db.execute(
            """DELETE FROM role_assignments
               WHERE person_id=$1 AND archived_at IS NULL
                 AND (role_id, COALESCE(start_date, '0001-01-01')) IN (
                     SELECT role_id, COALESCE(start_date, '0001-01-01')
                     FROM role_assignments
                     WHERE person_id=$2 AND archived_at IS NULL
                 )""",
            loser_id, winner_id,
        )
        await db.execute(
            "UPDATE role_assignments SET person_id=$1 WHERE person_id=$2",
            winner_id, loser_id,
        )

        # Polymorphic entity tables
        for table in ("contact_methods", "links", "entity_addresses",
                      "import_provenance", "field_confidence"):
            await db.execute(
                f"UPDATE {table} SET entity_id=$1"
                f" WHERE entity_type='person' AND entity_id=$2",
                winner_id, loser_id,
            )

        # identifiers (no entity_type column)
        await db.execute(
            "UPDATE identifiers SET entity_id=$1 WHERE entity_id=$2",
            winner_id, loser_id,
        )

        # duplicate_dismissals: delete the merged pair, reassign any others referencing loser
        await db.execute(
            "DELETE FROM duplicate_dismissals"
            " WHERE entity_type='person'"
            "   AND ((entity_a_id=$1 AND entity_b_id=$2)"
            "    OR  (entity_a_id=$2 AND entity_b_id=$1))",
            winner_id, loser_id,
        )
        # Delete loser dismissals that would conflict with existing winner dismissals
        # (winner already has a dismissal with the same third party)
        await db.execute(
            """DELETE FROM duplicate_dismissals dd
               USING duplicate_dismissals dw
               WHERE dd.entity_type = 'person'
                 AND dw.entity_type = 'person'
                 AND dw.entity_a_id = $2
                 AND (
                   (dd.entity_a_id = $1 AND dd.entity_b_id = dw.entity_b_id)
                   OR (dd.entity_b_id = $1 AND dd.entity_a_id = dw.entity_b_id)
                 )""",
            loser_id, winner_id,
        )
        await db.execute(
            """DELETE FROM duplicate_dismissals dd
               USING duplicate_dismissals dw
               WHERE dd.entity_type = 'person'
                 AND dw.entity_type = 'person'
                 AND dw.entity_b_id = $2
                 AND (
                   (dd.entity_a_id = $1 AND dd.entity_b_id = dw.entity_a_id)
                   OR (dd.entity_b_id = $1 AND dd.entity_a_id = dw.entity_a_id)
                 )""",
            loser_id, winner_id,
        )
        await db.execute(
            """UPDATE duplicate_dismissals
               SET entity_a_id = LEAST($1, entity_b_id),
                   entity_b_id = GREATEST($1, entity_b_id)
               WHERE entity_type='person' AND entity_a_id=$2""",
            winner_id, loser_id,
        )
        await db.execute(
            """UPDATE duplicate_dismissals
               SET entity_a_id = LEAST(entity_a_id, $1),
                   entity_b_id = GREATEST(entity_a_id, $1)
               WHERE entity_type='person' AND entity_b_id=$2""",
            winner_id, loser_id,
        )

        await db.execute("DELETE FROM people WHERE id=$1", loser_id)

    invalidate_person_dup_count_cache()

    if is_htmx(request):
        pairs = await _fetch_duplicate_pairs(db)
        body = (
            f'Merged <strong>{escape(loser_name)}</strong> into '
            f'<a href="/admin/people/{winner_id}/"><strong>{escape(winner_name)}</strong></a>. '
            f'Review role assignments and contact info for duplicates.'
        )
        ctx = {
            "user": user,
            "active_section": "people_duplicates",
            "pairs": pairs,
        }
        return templates.TemplateResponse(
            request,
            "admin/people/_duplicates_region.html",
            ctx,
            headers=flash_trigger("success", body),
        )
    return RedirectResponse("/admin/people/duplicates/", status_code=303)


@router.post("/{id_a}/dismiss-duplicate/{id_b}/")
async def person_dismiss_duplicate(
    id_a: str,
    id_b: str,
    request: Request,
    user: AdminUser | RedirectResponse = Depends(get_admin_user),
    db=Depends(get_db),
):
    """Record that this pair is not a duplicate (suppress from future results)."""
    redirect, user = check_auth(user)
    if redirect:
        return redirect
    # Store with consistent ordering (a < b)
    a, b = (id_a, id_b) if id_a < id_b else (id_b, id_a)
    await db.execute(
        "INSERT INTO duplicate_dismissals"
        " (id, entity_type, entity_a_id, entity_b_id, dismissed_by)"
        " VALUES ($1, 'person', $2, $3, $4)"
        " ON CONFLICT (entity_type, entity_a_id, entity_b_id) DO NOTHING",
        generate_id(), a, b, user.email,
    )
    invalidate_person_dup_count_cache()
    if is_htmx(request):
        pairs = await _fetch_duplicate_pairs(db)
        ctx = {
            "user": user,
            "active_section": "people_duplicates",
            "pairs": pairs,
        }
        return templates.TemplateResponse(
            request,
            "admin/people/_duplicates_region.html",
            ctx,
            headers=flash_trigger("info", "Pair marked as not a duplicate."),
        )
    return RedirectResponse("/admin/people/duplicates/", status_code=303)
