"""Admin CRUD for person names.

visibility-allowlist (issue #121): the admin name-management page must
edit / count ALL name rows regardless of visibility (incl. legal_only
and deadname); these helpers intentionally bypass the visibility filter.
"""

from src.api.admin._names_shared import make_names_router
from src.api.admin.deps import person_header_extra
from src.core.observation import NO_AUTO_CANONICAL_NAME_TYPES
from src.core.types import PERSON_NAME_TYPES


async def _maybe_promote_sole_name(person_id: str, db) -> None:
    """If the person has exactly one name and it is displayable, promote it.

    Displayable means `visibility='public'` and a name_type outside
    NO_AUTO_CANONICAL_NAME_TYPES — the same bar `core.observation.write_names`
    applies (#308). Promoting a deadname or an mrz row would set is_canonical on
    a row `v_person_display_names` filters out, leaving the person blank *and*
    the canonical slot occupied. Such a person stays deliberately un-canonical
    until a human adds a displayable name.
    """
    rows = await db.fetch(
        "SELECT id, is_canonical, name_type, visibility FROM person_names WHERE person_id=$1",
        person_id,
    )
    if len(rows) != 1:
        return
    row = rows[0]
    if row["is_canonical"]:
        return
    if row["visibility"] != "public" or row["name_type"] in NO_AUTO_CANONICAL_NAME_TYPES:
        return
    await db.execute("UPDATE person_names SET is_canonical=TRUE WHERE id=$1", row["id"])


async def _last_identity_blocked(person_id: str, db) -> bool:
    """Return True when deleting would remove the last person name."""
    name_count = await db.fetchval(
        "SELECT count(*) FROM person_names WHERE person_id=$1",
        person_id,
    )
    return name_count == 1


router = make_names_router(
    entity_id_key="person_id",
    prefix="/people/{entity_id}/names",
    tags=["admin-person-names"],
    entity_table="people",
    entity_not_found_msg="Person not found",
    names_table="person_names",
    entity_fk="person_id",
    tmpl_form_row="admin/people/partials/_name_form_row.html",
    tmpl_read_row="admin/people/partials/_name_row.html",
    tmpl_rows="admin/people/partials/_name_rows.html",
    name_types=PERSON_NAME_TYPES,
    detail_url=lambda eid: f"/admin/people/{eid}/",
    maybe_promote_sole_name=_maybe_promote_sole_name,
    last_identity_blocked=_last_identity_blocked,
    last_identity_error_msg="Cannot remove the only name.",
    last_identity_409_msg="Cannot remove the only name.",
    header_extra=person_header_extra,
    supports_person_metadata=True,
)
