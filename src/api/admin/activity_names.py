"""Activity › Key-written names (#847): review the person names an API key wrote.

From #845 an API key can add names to people, public at once. The safeguard is
review after the fact, and this page is where a curator does it: every
``person_names`` row with a ``source_key_id``, newest first, filterable by key.
Keys from every owner are listed (Settings › API keys shows a curator only their
own), the way Activity › API requests is.

What it can show: names held now. A retract deletes the row, and a deleted key's
names lose their stamp (``ON DELETE SET NULL``). The stamp is the first writer
only. After a key rotation the old key can no longer retract its names, so this
page is how a curator finds them.

Non-public rows (a key-sent ``deadname`` lands ``legal_only``; a curator may
hide any name) keep their place but **withhold their text and type in the SQL**,
so no template change can leak them. The row points at the person page, whose
disclosure toggle shows the name — the #847 exception in ``docs/NAMES.md``
§ Visibility rule.

No #306 status axis: names are not archived, and filtering on the person's
status would hide names by default. An archived person is badged instead.
"""

from fastapi import APIRouter, Depends, Query, Request
from fastapi.templating import Jinja2Templates

from src.api.admin._citations_shared import citation_count_lateral
from src.api.admin.deps import AdminUser, get_admin_user, get_db, is_htmx
from src.api.admin.pagination import (
    PAGE_SIZE_DEFAULT,
    PAGE_SIZE_MAX,
    PAGE_SIZE_MIN,
    pagination_context,
)

templates = Jinja2Templates(directory="src/templates")
router = APIRouter(prefix="/activity/names", tags=["admin-activity-names"])

_PAGE = "admin/activity/names/list.html"
_REGION = "admin/activity/names/_region.html"


def _where(key: str) -> tuple[str, list]:
    """The stamped-rows predicate, narrowed to one key when ``key`` is given."""
    if key:
        return "WHERE pn.source_key_id = $1", [key]
    return "WHERE pn.source_key_id IS NOT NULL", []


async def query_key_written_names(
    db, *, key: str, page: int, page_size: int
) -> tuple[list, int, dict]:
    """One page of key-written person names, newest first: ``(rows, count, pctx)``.

    ``name`` and ``name_type`` are NULL on a non-public row (``is_public``
    False): the text never leaves the database. The ``ORDER BY`` ends on the
    unique ``pn.id`` (#297), so a ``created_at`` tie pages stably.
    """
    where, params = _where(key)
    count = await db.fetchval(f"SELECT count(*) FROM person_names pn {where}", *params)
    pctx = pagination_context(page, count, page_size)
    offset = (pctx["page"] - 1) * page_size
    list_params = [*params, page_size, offset]
    # visibility-allowlist (issue #121): every row is read, but a non-public
    # row's text and type are withheld below — see the module docstring.
    rows = await db.fetch(
        "SELECT pn.id, pn.person_id, pn.created_at, pn.source_key_id,"
        "       (pn.visibility = 'public') AS is_public,"
        "       CASE WHEN pn.visibility = 'public' THEN pn.name END AS name,"
        "       CASE WHEN pn.visibility = 'public' THEN pn.name_type END AS name_type,"
        "       k.label AS key_label,"
        "       dn.display_name AS person_name,"
        "       p.archived_at AS person_archived_at,"
        "       cc_j.citation_count"
        " FROM person_names pn"
        " JOIN api_keys k ON k.id = pn.source_key_id"
        " JOIN people p ON p.id = pn.person_id"
        " LEFT JOIN v_person_display_names dn ON dn.person_id = pn.person_id"
        f"{citation_count_lateral('person_name', 'pn.id')}"
        f" {where}"
        " ORDER BY pn.created_at DESC, pn.id DESC"
        f" LIMIT ${len(list_params) - 1} OFFSET ${len(list_params)}",
        *list_params,
    )
    return rows, count, pctx


async def keys_with_names(db) -> list:
    """Keys, from any owner, that hold at least one person name — the filter's options."""
    return await db.fetch(
        "SELECT k.id, k.label FROM api_keys k"
        " WHERE EXISTS (SELECT 1 FROM person_names pn WHERE pn.source_key_id = k.id)"
        " ORDER BY k.label, k.id"
    )


async def count_key_written_names(db) -> int:
    """How many person names carry a key stamp — the Activity landing card's figure."""
    return await db.fetchval("SELECT count(*) FROM person_names WHERE source_key_id IS NOT NULL")


@router.get("/")
async def key_written_names(
    request: Request,
    key: str = Query(""),
    page: int = Query(1, ge=1),
    page_size: int = Query(PAGE_SIZE_DEFAULT, ge=PAGE_SIZE_MIN, le=PAGE_SIZE_MAX),
    user: AdminUser = Depends(get_admin_user),
    db=Depends(get_db),
):
    """List key-written person names, newest first; ``?key=`` narrows to one key.

    An unknown ``key`` lists nothing rather than every key: a link from a
    deleted key's badge must not read as "this key wrote all of these".
    """
    key = key.strip()
    rows, count, pctx = await query_key_written_names(db, key=key, page=page, page_size=page_size)
    keys = await keys_with_names(db)
    ctx = {
        "user": user,
        "active_section": "activity_names",
        "rows": rows,
        "keys": keys,
        "key": key,
        "key_known": any(k["id"] == key for k in keys),
        "page_size": page_size,
        "total": count,
        **pctx,
    }
    template = _REGION if is_htmx(request) else _PAGE
    return templates.TemplateResponse(request, template, ctx)
