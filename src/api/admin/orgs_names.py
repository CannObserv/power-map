"""Admin CRUD for organization names."""

import asyncpg

from src.api.admin._names_shared import make_names_router
from src.api.admin.deps import org_header_extra
from src.core.logging import get_logger
from src.core.types import ORG_NAME_TYPES

logger = get_logger(__name__)

# Display preference when promoting a replacement canonical name. Orgs have no
# visibility column and no machine-readable name_types, so unlike the person
# ladder every name is eligible — this only orders the choice.
_ORG_NAME_TYPE_PRIORITY_SQL = "CASE name_type WHEN 'legal' THEN 1 WHEN 'dba' THEN 2 ELSE 3 END"


async def _maybe_promote_sole_name(org_id: str, db) -> None:
    """Restore the org's display pointer after a name delete (CR6 #52).

    The org twin of `heal_person_canonical`: v_org_display_names joins only
    `is_canonical = TRUE` (falling back to a canonical acronym, if any), so an
    org whose canonical name is deleted renders blank no matter how many names
    remain. The previous "promote only when exactly one name remains" shortcut
    is the same hole CR5 #45 closed on the person side.

    Guarded and savepointed like the person heal: a concurrent promotion
    between probe and UPDATE loses quietly rather than aborting the route's
    transaction. Best-effort — anything but a lost race logs at WARNING.
    """
    try:
        async with db.transaction():
            candidate = await db.fetchval(
                f"SELECT id FROM organization_names"
                f" WHERE organization_id=$1 AND is_canonical=FALSE"
                f"   AND NOT EXISTS (SELECT 1 FROM organization_names"
                f"                   WHERE organization_id=$1 AND is_canonical=TRUE)"
                f" ORDER BY {_ORG_NAME_TYPE_PRIORITY_SQL}, id LIMIT 1",
                org_id,
            )
            if candidate is None:
                return
            await db.execute(
                "UPDATE organization_names SET is_canonical=TRUE"
                " WHERE id=$1 AND is_canonical=FALSE"
                "   AND NOT EXISTS (SELECT 1 FROM organization_names x"
                "                   WHERE x.organization_id=organization_names.organization_id"
                "                     AND x.is_canonical=TRUE)",
                candidate,
            )
    except asyncpg.exceptions.UniqueViolationError:
        logger.debug("org canonical heal: lost race for org=%s", org_id)
    except asyncpg.exceptions.PostgresError as exc:
        logger.warning(
            "org canonical heal: promotion failed for org=%s (%s: %s)",
            org_id,
            type(exc).__name__,
            exc,
        )


async def _last_identity_blocked(org_id: str, db) -> bool:
    """Return True when deleting would remove the last name with no canonical acronym."""
    name_count = await db.fetchval(
        "SELECT count(*) FROM organization_names WHERE organization_id=$1",
        org_id,
    )
    canonical_acronym_count = await db.fetchval(
        "SELECT count(*) FROM organization_acronyms WHERE organization_id=$1 AND is_canonical=TRUE",
        org_id,
    )
    return name_count == 1 and canonical_acronym_count == 0


router = make_names_router(
    entity_id_key="org_id",
    prefix="/orgs/{entity_id}/names",
    tags=["admin-org-names"],
    entity_table="organizations",
    entity_not_found_msg="Organization not found",
    names_table="organization_names",
    entity_fk="organization_id",
    tmpl_form_row="admin/orgs/partials/_name_form_row.html",
    tmpl_read_row="admin/orgs/partials/_name_row.html",
    tmpl_rows="admin/orgs/partials/_name_rows.html",
    name_types=ORG_NAME_TYPES,
    detail_url=lambda eid: f"/admin/orgs/{eid}/",
    maybe_promote_sole_name=_maybe_promote_sole_name,
    last_identity_blocked=_last_identity_blocked,
    last_identity_error_msg=(
        "Cannot remove the only name when the organization has no canonical acronym."
    ),
    last_identity_409_msg="Cannot remove the only name: no canonical acronym exists.",
    header_extra=org_header_extra,
    supports_effective_dates=True,
)
