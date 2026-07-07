"""Admin jurisdiction views — list/browse plus the role-picker typeahead.

Phase 1 (#275) adds the read-only browse surface (list + detail). The ``/search/``
typeahead (#264) that feeds the role-type form's jurisdiction picker remains.
"""

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.templating import Jinja2Templates

from src.api.admin.deps import AdminUser, escape_like, get_admin_user, get_db, is_htmx
from src.api.admin.jurisdictions_queries import VALID_STATUSES, query_jurisdictions_rows
from src.api.admin.pagination import PAGE_SIZE_DEFAULT, PAGE_SIZE_MAX, PAGE_SIZE_MIN
from src.core.jurisdictions import fetch_lineage

templates = Jinja2Templates(directory="src/templates")
router = APIRouter(prefix="/jurisdictions", tags=["admin-jurisdictions"])


@router.get("/")
async def jurisdictions_list(
    request: Request,
    q: str = "",
    status: str = "active",
    type_: str = Query("", alias="type"),
    page: int = Query(1, ge=1),
    page_size: int = Query(PAGE_SIZE_DEFAULT, ge=PAGE_SIZE_MIN, le=PAGE_SIZE_MAX),
    user: AdminUser = Depends(get_admin_user),
    db=Depends(get_db),
):
    """List jurisdictions with search, type filter, and status filter."""
    if status not in VALID_STATUSES:
        status = "active"
    type_slug = type_ or None
    rows, count, pctx = await query_jurisdictions_rows(
        db, q=q, status=status, type_slug=type_slug, page=page, page_size=page_size
    )
    # The type-filter dropdown lives in list.html, not _region.html, so its
    # options are only needed on a full-page render — skip the query on HTMX
    # region swaps (fired on every debounced search keystroke).
    is_partial = is_htmx(request)
    types = (
        []
        if is_partial
        else await db.fetch(
            "SELECT slug, display_name FROM jurisdiction_types ORDER BY display_name"
        )
    )
    ctx = {
        "user": user,
        "active_section": "jurisdictions",
        "jurisdictions": rows,
        "types": types,
        "q": q,
        "status": status,
        "type_slug": type_slug or "",
        "page_size": page_size,
        "total": count,
        "flash_msg": None,
        **pctx,
    }
    template = "admin/jurisdictions/_region.html" if is_partial else "admin/jurisdictions/list.html"
    return templates.TemplateResponse(request, template, ctx)


@router.get("/search/")
async def jurisdictions_search(
    request: Request,
    q: str = "",
    user: AdminUser = Depends(get_admin_user),
    db=Depends(get_db),
):
    """Typeahead search — returns an HTML fragment of matching jurisdictions."""
    results = []
    if q.strip():
        results = await db.fetch(
            """SELECT id, name, slug
               FROM jurisdictions
               WHERE archived_at IS NULL
                 AND (name ILIKE $1 ESCAPE '\\' OR slug ILIKE $1 ESCAPE '\\')
               ORDER BY name
               LIMIT 20""",
            f"%{escape_like(q.strip())}%",
        )
    return templates.TemplateResponse(
        request,
        "admin/jurisdictions/partials/_search_results.html",
        {"results": results},
    )


@router.get("/{jurisdiction_id}/")
async def jurisdiction_detail(
    jurisdiction_id: str,
    request: Request,
    user: AdminUser = Depends(get_admin_user),
    db=Depends(get_db),
):
    """Read-only jurisdiction detail view."""
    jur = await db.fetchrow(
        """SELECT j.*, jt.slug AS type_slug, jt.display_name AS type_display_name
           FROM jurisdictions j
           JOIN jurisdiction_types jt ON jt.id = j.type_id
           WHERE j.id = $1""",
        jurisdiction_id,
    )
    if not jur:
        raise HTTPException(status_code=404, detail="Jurisdiction not found")

    identifiers = await db.fetch(
        """SELECT i.value, eit.slug AS type_slug, eit.display_name AS type_name
           FROM identifiers i
           JOIN entity_identifier_types eit ON eit.id = i.entity_identifier_type_id
           WHERE i.entity_id = $1 AND eit.entity_type = 'jurisdiction'
           ORDER BY eit.slug, i.value""",
        jurisdiction_id,
    )
    links = await db.fetch(
        """SELECT l.url, l.is_active, lt.display_name AS link_type_name
           FROM links l JOIN link_types lt ON lt.id = l.link_type_id
           WHERE l.entity_type = 'jurisdiction' AND l.entity_id = $1
           ORDER BY lt.display_name, l.url""",
        jurisdiction_id,
    )
    addresses = await db.fetch(
        """SELECT ea.address_type, ea.valid_from, ea.valid_until,
                  a.standardized, a.address_line_1, a.city, a.region, a.postal_code
           FROM entity_addresses ea JOIN addresses a ON a.id = ea.address_id
           WHERE ea.entity_type = 'jurisdiction' AND ea.entity_id = $1
           ORDER BY ea.valid_from DESC NULLS LAST""",
        jurisdiction_id,
    )
    contacts = await db.fetch(
        """SELECT contact_type, value, display_label
           FROM contact_methods
           WHERE entity_type = 'jurisdiction' AND entity_id = $1
           ORDER BY contact_type, value""",
        jurisdiction_id,
    )
    # Lineage-category edges have their own panel (fetch_lineage below), so
    # exclude them here to avoid rendering the same edge in both panels.
    relationships = await db.fetch(
        """SELECT jr.from_id, jr.to_id,
                  jrt.display_name AS rel_type_name,
                  jrt.category, jrt.is_symmetric,
                  jr.valid_from, jr.valid_until,
                  jf.name AS from_name, jto.name AS to_name
           FROM jurisdiction_relationships jr
           JOIN jurisdiction_relationship_types jrt ON jrt.id = jr.rel_type_id
           JOIN jurisdictions jf ON jf.id = jr.from_id
           JOIN jurisdictions jto ON jto.id = jr.to_id
           WHERE (jr.from_id = $1 OR jr.to_id = $1)
             AND jrt.category <> 'lineage'
           ORDER BY jrt.category, jrt.display_name, jr.created_at""",
        jurisdiction_id,
    )
    lineage = await fetch_lineage(db, jurisdiction_id)
    affiliations = await db.fetch(
        """SELECT o.id AS org_id, dn.display_name AS org_name,
                  ojat.display_name AS affiliation_type
           FROM organization_jurisdiction_affiliations oja
           JOIN organizations o ON o.id = oja.organization_id
           LEFT JOIN v_org_display_names dn ON dn.organization_id = o.id
           JOIN organization_jurisdiction_affiliation_types ojat
                ON ojat.id = oja.affiliation_type_id
           WHERE oja.jurisdiction_id = $1
           ORDER BY dn.display_name NULLS LAST""",
        jurisdiction_id,
    )
    roles = await db.fetch(
        """SELECT r.id, r.title, r.qualifier,
                  o.id AS org_id, dn.display_name AS org_name
           FROM roles r
           JOIN organizations o ON o.id = r.organization_id
           LEFT JOIN v_org_display_names dn ON dn.organization_id = o.id
           WHERE r.jurisdiction_id = $1 AND r.archived_at IS NULL
           ORDER BY dn.display_name NULLS LAST, r.title""",
        jurisdiction_id,
    )

    return templates.TemplateResponse(
        request,
        "admin/jurisdictions/detail.html",
        {
            "user": user,
            "active_section": "jurisdictions",
            "jur": jur,
            "identifiers": identifiers,
            "links": links,
            "addresses": addresses,
            "contacts": contacts,
            "relationships": relationships,
            "lineage": lineage,
            "affiliations": affiliations,
            "roles": roles,
        },
    )
