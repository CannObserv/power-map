"""Admin CRUD for person names.

visibility-allowlist (issue #121): the admin name-management page must
edit / count ALL name rows regardless of visibility (incl. legal_only
and deadname); these helpers intentionally bypass the visibility filter.
"""

from src.api.admin._names_shared import make_names_router
from src.api.admin.deps import person_header_extra
from src.core.observation import heal_person_canonical
from src.core.types import PERSON_NAME_TYPES


async def _maybe_promote_sole_name(person_id: str, db) -> None:
    """Restore the person's display pointer after a name change (#308).

    Delegates to `heal_person_canonical`, the shared repair used by the
    observation path, merge, and the #308c backfill — so every route that can
    strand a person picks the same replacement row, by the same ladder.

    Previously this promoted only when *exactly one* name remained, so deleting
    the canonical of a multi-name person left `v_person_display_names` NULL with
    perfectly good public names still present, and nothing repaired it until an
    observation happened to touch that person (CR5 #45).

    The displayability bar is unchanged and lives in the helper: `visibility =
    'public'` and a name_type outside NO_AUTO_CANONICAL_NAME_TYPES. Promoting a
    deadname or an mrz row would set is_canonical on a row
    `v_person_display_names` filters out — which
    `chk_person_canonical_is_public` now rejects outright for deadnames. A
    person carrying only such names stays deliberately blank until a human adds
    a displayable one.
    """
    await heal_person_canonical(db, person_id)


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
